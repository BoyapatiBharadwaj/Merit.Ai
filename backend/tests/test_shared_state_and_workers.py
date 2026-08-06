"""
Tests for the pieces that make running more than one core-api or worker
process safe -- all backed by Redis now (app/core/redis_client.py):
cross-worker rate limiting (app/core/rate_limit.py), distributed locks
(app/core/locks.py), and the RQ background job/email queue
(app/core/queues.py, app/worker/).

PostgreSQL remains the permanent source of truth throughout -- these tests
pin that Redis failure is a controlled 503 (never a silent bypass of a rate
limit, an OTP check, a lock, or a queued operation), that locks actually
exclude a second concurrent acquisition, and that the docker-compose stack
keeps both Postgres (permanent data) and Redis (shared ephemeral state) with
Redis never reachable from outside the internal network.
"""
import redis
import pytest
from fastapi import HTTPException

from app.core import queues as _queues
from app.core import rate_limit
from app.core.locks import distributed_lock
from app.core.redis_client import RedisUnavailableError, get_client


# Module-level, not nested inside a test function: RQ resolves a queued job by
# its dotted module path (`module.qualname`) even when running eagerly in
# tests (see app/core/queues.py's module docstring) -- a closure defined
# inside a test function has no importable path, so it fails with an
# "Invalid attribute name" error the moment RQ tries to look it up, even
# though nothing about the job itself is broken.
def _double_for_test(x):
    return x * 2


def _explode_for_test():
    raise RuntimeError("boom")


@pytest.fixture(autouse=True)
def _clean_state():
    rate_limit._reset_all()
    yield
    rate_limit._reset_all()


# --- shared rate limiting, now via Redis ---------------------------------------

def test_counts_are_shared_across_what_would_be_separate_processes():
    """The whole point: two `consume()` calls -- standing in for two different
    uvicorn workers -- count against ONE budget, because both increment the
    same Redis key rather than a private in-memory counter."""
    for _ in range(5):
        rate_limit.consume("shared", "user-1", limit=5, window=60)

    with pytest.raises(Exception) as caught:
        rate_limit.consume("shared", "user-1", limit=5, window=60)
    assert caught.value.status_code == 429


def test_identities_and_buckets_stay_separate():
    for _ in range(5):
        rate_limit.consume("bucket-a", "user-1", limit=5, window=60)

    # A different user, and a different endpoint, each get their own budget.
    rate_limit.consume("bucket-a", "user-2", limit=5, window=60)
    rate_limit.consume("bucket-b", "user-1", limit=5, window=60)


def test_the_counter_is_really_a_redis_key():
    """Not an implementation detail -- this is what makes it survive a process
    restart and be visible to every worker, which an in-memory dict cannot be."""
    rate_limit.consume("row-check", "user-1", limit=5, window=60)
    keys = get_client().keys("ratelimit:row-check:user-1:*")
    assert len(keys) == 1
    assert int(get_client().get(keys[0])) == 1


def test_a_429_carries_retry_after():
    for _ in range(2):
        rate_limit.consume("retry", "user-1", limit=2, window=60)
    with pytest.raises(Exception) as caught:
        rate_limit.consume("retry", "user-1", limit=2, window=60)
    assert int(caught.value.headers["Retry-After"]) > 0


def test_the_counter_key_expires_on_its_own():
    """Redis's own TTL is the expiry mechanism now -- there is no
    rate_limit_counters table to sweep any more (see app/core/rate_limit.py)."""
    rate_limit.consume("ttl-check", "user-1", limit=5, window=60)
    keys = get_client().keys("ratelimit:ttl-check:user-1:*")
    assert len(keys) == 1
    ttl = get_client().ttl(keys[0])
    assert 0 < ttl <= 60


# --- Redis connection failure: controlled 503s, never a silent bypass ---------

def _explode(*_a, **_kw):
    raise redis.exceptions.ConnectionError("Redis is down")


def test_a_redis_failure_refuses_the_rate_limit_check_rather_than_letting_it_through(monkeypatch):
    """Deliberately the opposite of the old Postgres-backed limiter's fallback:
    this rewrite's brief requires a controlled service-unavailable response for
    rate-limited operations when Redis is down, not a looser, silently-degraded
    limit. A credential-stuffing run timed to a Redis blip must not sail
    through unthrottled."""
    monkeypatch.setattr(get_client(), "incr", _explode)
    with pytest.raises(RedisUnavailableError):
        rate_limit.consume("degraded", "user-1", limit=5, window=60)


def test_a_redis_failure_refuses_otp_requests(monkeypatch):
    from app.models.otp import OtpPurpose
    from app.services import otp_redis_store

    monkeypatch.setattr(get_client(), "pipeline", _explode)
    with pytest.raises(RedisUnavailableError):
        otp_redis_store.issue(OtpPurpose.SIGNUP, "a@b.com", code_hash="x", ttl_seconds=600, cooldown_seconds=60)


def test_a_redis_failure_refuses_lock_acquisition(monkeypatch):
    monkeypatch.setattr(get_client(), "set", _explode)
    with pytest.raises(RedisUnavailableError):
        with distributed_lock("some-operation"):
            pass


def test_a_redis_failure_refuses_enqueueing_a_job(monkeypatch):
    # Patched where queues.py looks it up (it imported the name directly),
    # not where it's defined -- the usual monkeypatch gotcha.
    monkeypatch.setattr(_queues, "get_raw_client", _explode)
    with pytest.raises(RedisUnavailableError):
        _queues.enqueue(_queues.QUEUE_DEFAULT, _double_for_test, 1)


def test_the_login_endpoint_returns_503_not_a_bypass_when_redis_is_down(client, seed_roles, monkeypatch):
    """End-to-end: confirms the 503 behaviour is actually wired into the route,
    not just the unit-level rate_limit.consume() call."""
    monkeypatch.setattr(get_client(), "incr", _explode)
    response = client.post("/api/v1/auth/login", json={"email": "nobody@example.com", "password": "x"})
    assert response.status_code == 503


# --- distributed locks (app/core/locks.py) -------------------------------------

def test_a_second_acquisition_is_refused_while_the_first_holds_the_lock():
    with distributed_lock("exclusive-thing", blocking_timeout=0) as first:
        assert first is True
        with distributed_lock("exclusive-thing", blocking_timeout=0) as second:
            assert second is False


def test_the_lock_is_available_again_after_release():
    with distributed_lock("reusable-thing", blocking_timeout=0) as first:
        assert first is True
    with distributed_lock("reusable-thing", blocking_timeout=0) as second:
        assert second is True


def test_different_lock_names_do_not_contend():
    with distributed_lock("thing-a", blocking_timeout=0) as a:
        with distributed_lock("thing-b", blocking_timeout=0) as b:
            assert a is True
            assert b is True


def test_a_lock_expires_on_its_own_ttl():
    """The crash-safety property: a lock nobody released must not deadlock the
    operation forever."""
    with distributed_lock("expiring-thing", timeout=0.05, blocking_timeout=0) as first:
        assert first is True
        import time
        time.sleep(0.1)
        # The TTL has elapsed; a fresh acquisition attempt (standing in for a
        # different process) succeeds even though the first "holder" never
        # released.
        with distributed_lock("expiring-thing", blocking_timeout=0) as second:
            assert second is True


def test_examiner_creation_is_locked_against_concurrent_duplicates(client, seed_roles, admin_token, monkeypatch):
    """A lock acquired and never released (simulating a second, concurrent
    request already in flight) must refuse a second creation for the same
    address with a clear conflict, not a confusing 500 or a duplicate account."""
    from app.core import locks as locks_module
    from tests.conftest import auth_headers

    real_lock = locks_module.distributed_lock

    def _always_taken(name, **kwargs):
        if name.startswith("examiner-create:"):
            kwargs["blocking_timeout"] = 0

            class _TakenLock:
                def __enter__(self):
                    return False

                def __exit__(self, *exc):
                    return False

            return _TakenLock()
        return real_lock(name, **kwargs)

    monkeypatch.setattr(locks_module, "distributed_lock", _always_taken)
    # examiner_provisioning_service imported the name directly, so the
    # monkeypatch has to target that reference too.
    import app.services.examiner_provisioning_service as provisioning
    monkeypatch.setattr(provisioning, "distributed_lock", _always_taken)

    response = client.post("/api/v1/auth/examiners", json={
        "first_name": "Pat", "last_name": "Reviewer", "email": "contested@example.com",
        "organization_name": "Acme Institute",
    }, headers=auth_headers(admin_token))
    assert response.status_code == 409


# --- the job queue (RQ over Redis, replacing job_outbox) -----------------------

def test_enqueue_runs_the_job_eagerly_in_tests_and_returns_its_result():
    """Queues run eagerly in the test suite (see conftest.py's _fake_redis
    fixture) -- no separate worker process needed to see a job's outcome."""
    job = _queues.enqueue(_queues.QUEUE_DEFAULT, _double_for_test, 21)
    assert job.return_value() == 42


def test_a_failing_job_is_recorded_as_failed_without_crashing_the_caller():
    job = _queues.enqueue(_queues.QUEUE_DEFAULT, _explode_for_test)
    assert job is not None
    assert job.is_failed
    assert "boom" in job.latest_result().exc_string


def test_duplicate_job_prevention_via_a_lock_around_enqueueing():
    """Mirrors how access_request_service._notify_admins guards against
    enqueueing two overlapping notification jobs for the same request: a
    non-blocking lock around the enqueue call means a second, concurrent
    caller for the same key skips enqueueing rather than double-queuing."""
    enqueued = []

    def _maybe_enqueue(key: str):
        with distributed_lock(f"dedupe:{key}", blocking_timeout=0) as acquired:
            if acquired:
                enqueued.append(key)

    _maybe_enqueue("request-1")
    _maybe_enqueue("request-1")  # same key, lock already released -- both should have gone through
    assert enqueued == ["request-1", "request-1"]

    with distributed_lock("dedupe:request-2", blocking_timeout=0):
        # Held open, simulating an enqueue already in flight.
        _maybe_enqueue("request-2")
    assert enqueued == ["request-1", "request-1"]  # request-2 was correctly skipped


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


def test_report_generation_is_locked_per_attempt():
    """A second, concurrent report-generation attempt for the SAME attempt id
    is skipped rather than duplicated -- see app/worker/jobs.build_attempt_report_pdf."""
    from app.worker import jobs

    with distributed_lock("report-generation:999", blocking_timeout=0) as acquired:
        assert acquired is True
        result = jobs.build_attempt_report_pdf(999)
        assert result == {"attempt_id": 999, "generated": False, "reason": "already generating"}


# --- docker-compose: both data stores present, Redis never publicly exposed ---

def test_the_api_no_longer_runs_the_scheduler_itself():
    """The reminder loop moved to its own single-replica service.

    If it came back into the API's lifespan, N workers would each run a copy and
    race to send the same reminders -- which is exactly what splitting it out
    was meant to prevent, and would be invisible until someone got four emails.
    """
    from pathlib import Path

    main_source = (Path(__file__).resolve().parent.parent / "app" / "main.py").read_text(encoding="utf-8")
    assert "reminder_service.start()" not in main_source


def _compose_text() -> str:
    from pathlib import Path

    return (Path(__file__).resolve().parent.parent.parent / "docker-compose.yml").read_text(encoding="utf-8")


def test_docker_compose_has_both_postgres_and_redis():
    compose = _compose_text()
    assert "postgres:16-alpine" in compose
    assert "redis:7-alpine" in compose


def test_redis_publishes_no_host_port_in_docker_compose():
    """Uncommented `ports:` under the redis service would expose it to the
    host network -- the internal `cache-net` network is meant to be the only
    way anything reaches it. Checks the redis service block specifically
    (not the whole file, which legitimately publishes a port for `proxy`)."""
    import yaml

    doc = yaml.safe_load(_compose_text())
    redis_service = doc["services"]["redis"]
    assert "ports" not in redis_service
    assert redis_service["networks"] == ["cache-net"]


def test_redis_requires_password_authentication_in_docker_compose():
    compose = _compose_text()
    assert "REDIS_PASSWORD" in compose
    assert "--requirepass" in compose


def test_redis_has_a_healthcheck_and_restart_policy_in_docker_compose():
    import yaml

    doc = yaml.safe_load(_compose_text())
    redis_service = doc["services"]["redis"]
    assert "healthcheck" in redis_service
    assert redis_service["restart"] == "unless-stopped"


def test_redis_has_persistent_storage_and_resource_limits_in_docker_compose():
    import yaml

    doc = yaml.safe_load(_compose_text())
    redis_service = doc["services"]["redis"]
    assert "redis_data" in str(redis_service["volumes"])
    assert "--appendonly" in str(redis_service["command"])
    assert "resources" in redis_service["deploy"]


def test_core_api_scheduler_and_worker_all_depend_on_redis_being_healthy():
    import yaml

    doc = yaml.safe_load(_compose_text())
    for name in ("core-api", "scheduler", "worker"):
        depends_on = doc["services"][name]["depends_on"]
        assert depends_on["redis"]["condition"] == "service_healthy"
