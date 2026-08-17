"""Per-IP (and per-user) rate limiting, shared across every core-api worker and replica via Redis."""
import ipaddress
import logging
from functools import lru_cache

from fastapi import HTTPException, Request, status

from app.core.config import settings
from app.core.redis_client import get_client, translate_errors

logger = logging.getLogger("app")

_KEY_PREFIX = "ratelimit:"


def _client_ip(request: Request) -> str:
    """The caller's address: the TCP peer, or the forwarded one if a trusted proxy put it there."""
    peer = request.client.host if request.client else None
    if not peer:
        return "unknown"

    if not _is_trusted_proxy(peer):
        return peer

    # Rightmost-untrusted, not leftmost. X-Forwarded-For is a client-to-proxy chain and only the
    # entries our own proxies appended can be believed.
    forwarded = request.headers.get("x-forwarded-for", "")
    for candidate in reversed([part.strip() for part in forwarded.split(",") if part.strip()]):
        if not _is_trusted_proxy(candidate):
            return candidate

    # Every hop was a trusted proxy, or the header was absent. Fall back to the
    # peer rather than inventing an identity.
    return peer


@lru_cache(maxsize=1024)
def _is_trusted_proxy(address: str) -> bool:
    """Cached because this runs on every rate-limited request and the answer for
    a given address cannot change without a restart."""
    networks = settings.trusted_proxies
    if not networks:
        return False
    try:
        parsed = ipaddress.ip_address(address)
    except ValueError:
        return False
    return any(parsed in network for network in networks)


def rate_limit(bucket: str, max_requests: int | None = None, window_seconds: int | None = None):
    """FastAPI dependency factory: add `Depends(rate_limit("login"))` to a
    route. `bucket` namespaces the counter per caller so that, e.g.,
    hammering /auth/login can't also burn through /auth/register's budget."""
    limit = max_requests if max_requests is not None else settings.AUTH_RATE_LIMIT_MAX_REQUESTS
    window = window_seconds if window_seconds is not None else settings.AUTH_RATE_LIMIT_WINDOW_SECONDS

    def _checker(request: Request) -> None:
        consume(bucket, _client_ip(request), limit=limit, window=window)

    return _checker


def consume(bucket: str, identity: str, *, limit: int, window: int,
            message: str = "Too many attempts. Please wait a moment before trying again.") -> None:
    """Count one request against (bucket, identity) and raise 429 if over budget, or 503 (via
    RedisUnavailableError) if the shared counter itself cannot be reached.
    """
    key = f"{_KEY_PREFIX}{bucket}:{identity}:{int(_window_start(window))}"
    with translate_errors("check the rate limit"):
        client = get_client()
        count = client.incr(key)
        if count == 1:
            # Only set on the window's first hit -- INCR on every later hit in the same window
            # must never slide the key's expiry outward, or a steady trickle of requests would
            # keep the window open forever.
            client.pexpire(key, max(int(window * 1000), 1))

    if count > limit:
        ttl = _seconds_left_in_window(window)
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            message,
            headers={"Retry-After": str(max(1, ttl))},
        )


def _window_start(window: int) -> int:
    import time
    return int(time.time() // window) if window > 0 else 0


def _seconds_left_in_window(window: int) -> int:
    import time
    now = time.time()
    return max(1, int(window - (now % window))) if window > 0 else 1


def _reset_all() -> None:
    """Test-only: clear every rate-limit key."""
    client = get_client()
    cursor = 0
    while True:
        cursor, keys = client.scan(cursor=cursor, match=f"{_KEY_PREFIX}*", count=500)
        if keys:
            client.delete(*keys)
        if cursor == 0:
            break
    _is_trusted_proxy.cache_clear()


def client_ip(request: Request) -> str | None:
    """The caller's address, as this module resolves it, for anything outside rate limiting that needs it."""
    ip = _client_ip(request)
    return None if not ip or ip == "unknown" else ip
