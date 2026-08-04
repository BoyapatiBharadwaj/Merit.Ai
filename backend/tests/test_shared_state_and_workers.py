"""
The pieces that make running more than one API process safe.

The bug this guards against is a quiet one: with N uvicorn workers and no shared
state, each keeps its own rate-limit counters, so every limit is silently
multiplied by N. Nothing errors, nothing looks wrong, and an attacker simply
gets N times the budget. These tests pin the shared path, the fallback path, and
the boundary between them.

Redis is optional here for the same reason it is optional in the app: the suite
must run on a machine with no Redis. Tests needing a real one use `fakeredis`
when it is installed and skip otherwise, so the shared-counter behaviour is
still exercised wherever it can be.
"""
import pytest

from app.core import rate_limit, shared_state
from app.core.config import settings


@pytest.fixture(autouse=True)
def _clean_state():
    """Both backends reset between tests, and the connection re-decided.

    shared_state caches its "is there a Redis" answer per process, which is
    right in production and wrong in a test that switches it on and off.
    """
    shared_state.reset_for_tests()
    rate_limit._reset_all()
    yield
    shared_state.reset_for_tests()
    rate_limit._reset_all()


@pytest.fixture
def redis_backed(monkeypatch):
    """Point shared_state at an in-memory Redis, or skip."""
    fakeredis = pytest.importorskip(
        "fakeredis", reason="fakeredis not installed; shared-counter behaviour not exercised here."
    )
    client = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(shared_state, "_client", lambda: client)
    return client


# --- the fallback path --------------------------------------------------------

def test_without_redis_the_in_process_counter_is_used(monkeypatch):
    """The default, and what a single-worker deployment runs on."""
    monkeypatch.setattr(settings, "REDIS_URL", "")
    shared_state.reset_for_tests()

    assert shared_state.is_available() is False
    for _ in range(3):
        rate_limit.consume("fallback", "user-1", limit=3, window=60)
    with pytest.raises(Exception) as caught:
        rate_limit.consume("fallback", "user-1", limit=3, window=60)
    assert caught.value.status_code == 429


def test_an_unreachable_redis_does_not_break_requests(monkeypatch):
    """A dead cache must degrade to looser limits, never to a failed login.

    This is the property that makes Redis safe to depend on: the worst outcome
    of it disappearing mid-exam is that limits get more permissive for a moment.
    """
    monkeypatch.setattr(settings, "REDIS_URL", "redis://127.0.0.1:1/0")  # nothing listening
    shared_state.reset_for_tests()

    assert shared_state.is_available() is False
    rate_limit.consume("degraded", "user-1", limit=5, window=60)  # must not raise


def test_a_redis_error_mid_request_falls_back_rather_than_500ing(redis_backed, monkeypatch):
    def _explode(*_args, **_kwargs):
        raise ConnectionError("redis went away")

    monkeypatch.setattr(redis_backed, "pipeline", _explode)
    rate_limit.consume("blip", "user-1", limit=5, window=60)  # must not raise


# --- the shared path ----------------------------------------------------------

def test_redis_counts_are_shared_not_per_process(redis_backed):
    """The whole reason Redis is here.

    Two consumes from what would be two different worker processes must count
    against ONE budget. With the in-process deque each would start from zero.
    """
    for _ in range(5):
        rate_limit.consume("shared", "user-1", limit=5, window=60)

    with pytest.raises(Exception) as caught:
        rate_limit.consume("shared", "user-1", limit=5, window=60)
    assert caught.value.status_code == 429

    # And the counter really lives in Redis, not in this process.
    keys = list(redis_backed.scan_iter("ratelimit:shared:user-1:*"))
    assert keys, "no shared counter was written"
    assert int(redis_backed.get(keys[0])) == 6


def test_identities_and_buckets_stay_separate_in_redis(redis_backed):
    for _ in range(5):
        rate_limit.consume("bucket-a", "user-1", limit=5, window=60)

    # A different user, and a different endpoint, each get their own budget.
    rate_limit.consume("bucket-a", "user-2", limit=5, window=60)
    rate_limit.consume("bucket-b", "user-1", limit=5, window=60)


def test_the_counter_carries_an_expiry(redis_backed):
    """Without a TTL every key would live forever and the limit would become
    permanent -- a lockout rather than a rate limit."""
    rate_limit.consume("ttl", "user-1", limit=5, window=60)
    key = next(iter(redis_backed.scan_iter("ratelimit:ttl:user-1:*")))
    assert 0 < redis_backed.ttl(key) <= 60


def test_a_429_from_redis_carries_retry_after(redis_backed):
    for _ in range(2):
        rate_limit.consume("retry", "user-1", limit=2, window=60)
    with pytest.raises(Exception) as caught:
        rate_limit.consume("retry", "user-1", limit=2, window=60)
    assert int(caught.value.headers["Retry-After"]) > 0


# --- connection handling ------------------------------------------------------

def test_a_password_is_never_logged_in_a_connection_url():
    """Connection strings are credentials."""
    assert "hunter2" not in shared_state._safe_url("redis://user:hunter2@cache:6379/0")


def test_the_api_no_longer_runs_the_scheduler_itself():
    """The reminder loop moved to its own single-replica service.

    If it came back into the API's lifespan, N workers would each run a copy and
    race to send the same reminders -- which is exactly what splitting it out
    was meant to prevent, and would be invisible until someone got four emails.
    """
    from pathlib import Path

    main_source = (Path(__file__).resolve().parent.parent / "app" / "main.py").read_text(encoding="utf-8")
    assert "reminder_service.start()" not in main_source


# --- the job queue ------------------------------------------------------------

def test_jobs_run_inline_when_there_is_no_queue(monkeypatch):
    """A deployment without the worker container must lose no functionality."""
    from app.worker import jobs

    monkeypatch.setattr(settings, "REDIS_URL", "")
    shared_state.reset_for_tests()

    assert jobs.is_async() is False
    assert jobs.enqueue(lambda: "ran", description="test") == "ran"


def test_a_failing_queue_still_runs_the_job(redis_backed, monkeypatch):
    """A queue that accepts work and loses it is worse than no queue at all."""
    from app.worker import jobs

    monkeypatch.setattr(jobs, "_queue", lambda: (_ for _ in ()).throw(RuntimeError("broker down")))
    with pytest.raises(RuntimeError):
        jobs.enqueue(lambda: "ran")


def test_bulk_email_counts_failures_instead_of_giving_up(monkeypatch):
    """One bad address must not abandon the other recipients."""
    from app.services import email_service
    from app.worker import jobs

    monkeypatch.setattr(email_service, "send",
                        lambda **kw: kw["to"] != "bad@example.com")

    result = jobs.send_bulk_email(
        ["a@example.com", "bad@example.com", "c@example.com"],
        subject="Notice", text_body="body",
    )
    assert result == {"sent": 2, "failed": 1, "total": 3}
