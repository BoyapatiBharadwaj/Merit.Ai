"""The one Redis connection this application holds, and the one place that decides what "Redis
is down" means to everything built on top of it.
"""
import logging

import redis

from app.core.config import settings

logger = logging.getLogger("app")


class RedisUnavailableError(RuntimeError):
    """Raised by every Redis-backed module when Redis cannot be reached."""


# Test hook: a fixture can install a fake client (e.g. fakeredis) here so the suite never needs
# a real Redis server, the same pattern used elsewhere in this codebase for swapping out an
# external dependency in tests (see email_service's `send`/`queue` being monkeypatched rather
# than this module reaching for a real SMTP server).
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
    """The shared Redis client for rate limiting, OTP state and locks."""
    global _client
    if _override_client is not None:
        return _override_client
    if _client is None:
        _client = _build_client(decode_responses=True)
    return _client


def get_raw_client():
    """A second connection, for RQ (app/core/queues.py, app/worker/main.py) specifically."""
    global _raw_client
    if _override_raw_client is not None:
        return _override_raw_client
    if _raw_client is None:
        _raw_client = _build_client(decode_responses=False)
    return _raw_client


def set_client_for_tests(client, *, raw_client=None) -> None:
    """Install a fake client (fakeredis) for the life of a test."""
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
    """Context manager that turns any redis.RedisError into RedisUnavailableError."""
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
