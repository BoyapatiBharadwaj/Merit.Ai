"""
The one Redis connection, and the rule for what happens without it.

Redis exists here for exactly one reason: several processes now need to agree on
something. Before this, `core-api` ran a single uvicorn worker, so a Python dict
was a perfectly good shared store -- there was nothing to share it with. Running
more than one worker is what breaks that, and it breaks it silently: N workers
each keep their own rate-limit counters, so an attacker gets N times the budget
and nobody notices.

**Redis is optional, and its absence is a supported configuration.** With
REDIS_URL unset, every caller here falls back to the in-process behaviour that
already worked. That matters for three reasons: the test suite must not need a
Redis container, a fresh clone must still run, and a single-worker deployment
genuinely does not need one. What it must never do is fail closed and take the
API down because a cache is missing -- the same graceful-degradation contract
email_service and ai_worker_client already follow.

The connection is created lazily and cached per process, not at import time: a
Redis that is slow to start must not stop the app booting, and a worker process
that never touches shared state should never open a socket at all.
"""
import logging
from functools import lru_cache

from app.core.config import settings

logger = logging.getLogger("app")

# Set once, the first time a connection is attempted and fails, so a Redis that
# is configured but unreachable produces ONE log line rather than one per
# request. A rate-limited endpoint under load would otherwise turn a dead cache
# into a flood of identical errors, which is its own outage.
_connection_failed = False


@lru_cache
def _client():
    """The shared Redis client, or None.

    lru_cache rather than a module global so the "no Redis configured" answer is
    cached too -- otherwise every call would re-parse the URL and re-decide.
    """
    global _connection_failed

    url = settings.REDIS_URL.strip()
    if not url:
        return None

    try:
        import redis

        client = redis.Redis.from_url(
            url,
            decode_responses=True,
            # Bounded so a wedged Redis cannot hold a request thread open. Every
            # operation here is a fallback-able convenience, so waiting seconds
            # for one is never the right trade.
            socket_connect_timeout=settings.REDIS_TIMEOUT_SECONDS,
            socket_timeout=settings.REDIS_TIMEOUT_SECONDS,
            health_check_interval=30,
        )
        client.ping()
        logger.info("Connected to Redis at %s", _safe_url(url))
        return client
    except Exception as error:
        if not _connection_failed:
            _connection_failed = True
            logger.warning(
                "Redis at %s is unreachable (%s). Falling back to per-process state: rate limits "
                "and caches will NOT be shared between workers. Fine for a single worker; with "
                "several, each gets its own counters.",
                _safe_url(url), type(error).__name__,
            )
        return None


def _safe_url(url: str) -> str:
    """Strip any password before logging. A connection string is a credential."""
    if "@" in url:
        scheme, _, rest = url.partition("://")
        return f"{scheme}://***@{rest.rpartition('@')[2]}"
    return url


def is_available() -> bool:
    """Whether shared state is actually working right now.

    Checked by callers that need to warn about a degraded mode rather than
    silently accept it -- see the multi-worker check in main.py.
    """
    return _client() is not None


def get_client():
    """The raw client, or None. Callers must handle None."""
    return _client()


def reset_for_tests() -> None:
    """Drop the cached connection so a test can switch Redis on or off.

    Test-only, and named so. The lru_cache means a process decides once whether
    Redis exists, which is right in production and useless in a test that wants
    to exercise both paths.
    """
    global _connection_failed
    _connection_failed = False
    _client.cache_clear()
