"""
The one Redis connection this application holds, and the one place that
decides what "Redis is down" means to everything built on top of it.

Redis is back in this stack, but scoped deliberately: it is the shared,
ephemeral-state backend for cross-worker rate limiting, one-time-passcode
state, distributed locks and the background job queue (see
app/core/rate_limit.py, app/services/otp_redis_store.py, app/core/locks.py,
app/core/queues.py). PostgreSQL remains the permanent source of truth for
every durable record -- users, exams, attempts, results, violations,
credentials, audit logs and email history never live in Redis alone.

`RedisUnavailableError` is the single exception every one of those modules
raises when it cannot reach Redis. It is registered as a FastAPI exception
handler (see app/main.py) that turns it into a clean 503, the same pattern
already used for SQLAlchemyError. Centralising it here means "Redis is
temporarily unavailable" produces the same controlled, logged response
everywhere it happens, rather than four subsystems each inventing their own
fallback behaviour -- which is exactly the inconsistency a prior version of
this rate limiter had (silently degrading to a looser, per-process counter
instead of saying anything went wrong).
"""
import logging

import redis

from app.core.config import settings

logger = logging.getLogger("app")


class RedisUnavailableError(RuntimeError):
    """Raised by every Redis-backed module when Redis cannot be reached.

    Deliberately NOT a subclass of redis.RedisError -- callers should not need
    to import redis themselves just to catch failures from this module's public
    functions. app/main.py's exception handler catches this one type and
    returns 503 for every request path that hits it.
    """


# Test hook: a fixture can install a fake client (e.g. fakeredis) here so the
# suite never needs a real Redis server, the same pattern used elsewhere in
# this codebase for swapping out an external dependency in tests (see
# email_service's `send`/`queue` being monkeypatched rather than this module
# reaching for a real SMTP server).
_override_client = None
_override_raw_client = None
_client = None
_raw_client = None


def _build_client(*, decode_responses: bool) -> "redis.Redis":
    return redis.Redis.from_url(
        settings.REDIS_URL,
        password=settings.REDIS_PASSWORD or None,
        socket_connect_timeout=settings.REDIS_SOCKET_TIMEOUT_SECONDS,
        socket_timeout=settings.REDIS_SOCKET_TIMEOUT_SECONDS,
        decode_responses=decode_responses,
        health_check_interval=30,
    )


def get_client():
    """The shared Redis client for rate limiting, OTP state and locks -- every
    one of which only ever stores and compares plain strings, so responses are
    decoded for them. Built once and reused: redis-py's client owns a
    connection pool internally, so there is no benefit to reconnecting per
    call, only latency cost."""
    global _client
    if _override_client is not None:
        return _override_client
    if _client is None:
        _client = _build_client(decode_responses=True)
    return _client


def get_raw_client():
    """A second connection, for RQ (app/core/queues.py, app/worker/main.py)
    specifically. RQ pickles/unpickles job and result payloads itself and
    expects raw bytes back from Redis -- handing it a `decode_responses=True`
    connection breaks its internal (de)serialisation in ways that surface as
    obscure `AttributeError`s deep inside rq's own code, not as anything
    obviously Redis-shaped. Kept as a genuinely separate client, not a flag
    on the one above, so the two can never be accidentally shared.
    """
    global _raw_client
    if _override_raw_client is not None:
        return _override_raw_client
    if _raw_client is None:
        _raw_client = _build_client(decode_responses=False)
    return _raw_client


def set_client_for_tests(client, *, raw_client=None) -> None:
    """Install a fake client (fakeredis) for the life of a test. `raw_client`
    defaults to the same fake server built WITHOUT decode_responses, matching
    the real get_client()/get_raw_client() split, when the caller passes a
    fakeredis server object rather than an already-connected client."""
    global _override_client, _override_raw_client
    _override_client = client
    _override_raw_client = raw_client if raw_client is not None else client


def reset_for_tests() -> None:
    global _override_client, _override_raw_client, _client, _raw_client
    _override_client = None
    _override_raw_client = None
    _client = None
    _raw_client = None


def is_available() -> bool:
    """Best-effort liveness check, for health endpoints. Never raises."""
    try:
        get_client().ping()
        return True
    except redis.RedisError:
        return False


def translate_errors(operation: str):
    """Context manager that turns any redis.RedisError into RedisUnavailableError.

    `operation` names what was being attempted (e.g. "check the login rate
    limit", "verify the signup code") so the resulting 503 -- and the log line
    behind it -- says what actually failed, not just that "Redis" did.
    """
    return _ErrorTranslator(operation)


class _ErrorTranslator:
    def __init__(self, operation: str):
        self._operation = operation

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, _tb):
        if exc_type is not None and issubclass(exc_type, redis.RedisError):
            logger.warning("Redis unavailable while trying to %s: %s", self._operation, exc)
            raise RedisUnavailableError(
                f"Could not {self._operation}: Redis is temporarily unavailable."
            ) from exc
        return False
