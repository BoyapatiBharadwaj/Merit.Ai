"""
Per-IP (and per-user) rate limiting, shared across every core-api worker and
replica via Redis -- the one place in this application where "shared across
workers" and "backed by Postgres" stop being the same thing.

A fixed-window counter per (bucket, identity, window), incremented atomically
with `INCR` + a one-time `EXPIRE` on the first increment of each window. Redis
gives this for free in a way a second Postgres round trip cannot improve on:
`INCR` is O(1) and atomic without a transaction, and the key's own TTL is the
window's lifetime, so there is nothing to sweep -- unlike the Postgres-backed
version this replaces, which needed a periodic DELETE over a
`rate_limit_counters` table that only ever grew.

Deliberately fails CLOSED, not open, when Redis cannot be reached: earlier
versions of this module (both the original Redis-or-in-process design and the
Postgres-backed one that came after it) fell back to a per-process counter so
a rate limiter outage could never block a login. This rewrite's brief is
explicit that rate-limited operations must return a controlled
service-unavailable response when their backing store is down rather than
silently letting every request through unthrottled -- a credential-stuffing
run timed to a Redis blip is exactly the case a silent fallback would miss.
See app/core/redis_client.py's RedisUnavailableError and its handler in
app/main.py for what "controlled" means here: a clean 503, not a crash.
"""
import ipaddress
import logging
from functools import lru_cache

from fastapi import HTTPException, Request, status

from app.core.config import settings
from app.core.redis_client import get_client, translate_errors

logger = logging.getLogger("app")

_KEY_PREFIX = "ratelimit:"


def _client_ip(request: Request) -> str:
    """The caller's address: the TCP peer, or the forwarded one if a trusted
    proxy put it there.

    request.client.host alone is wrong in this deployment because, behind the
    bundled nginx, the TCP peer is the PROXY -- one address for every candidate
    in the building. Ten wrong passwords from one person would exhaust the
    login budget for the entire exam hall, and the symptom would have been
    "the login page is broken" during a live sitting.

    Reading X-Forwarded-For unconditionally would be worse: any client can set
    that header, so it would hand out a fresh budget per request to exactly the
    attacker the limit exists to stop. Both halves are needed -- believe the
    header, but only from a peer inside TRUSTED_PROXY_IPS.
    """
    peer = request.client.host if request.client else None
    if not peer:
        return "unknown"

    if not _is_trusted_proxy(peer):
        return peer

    # Rightmost-untrusted, not leftmost. X-Forwarded-For is a client-to-proxy
    # chain and only the entries our own proxies appended can be believed; a
    # client that sends its own header simply prepends to it, so taking [0]
    # would take the attacker's chosen value. Walking from the right and
    # stopping at the first address not in the trusted set yields the address
    # our outermost proxy actually saw.
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
    """Count one request against (bucket, identity) and raise 429 if over
    budget, or 503 (via RedisUnavailableError) if the shared counter itself
    cannot be reached.

    Split out of `rate_limit` so the same fixed-window counter can be keyed on
    something other than a source IP. The authenticated proctoring endpoints key
    on user id instead (see app/api/deps.py::rate_limit_user): an IP is the only
    identity available before login, but once a request carries a token the user
    is both the more precise key and the harder one to rotate -- and keying those
    endpoints by IP would throttle an entire exam hall behind one NAT as though
    it were a single abuser.
    """
    key = f"{_KEY_PREFIX}{bucket}:{identity}:{int(_window_start(window))}"
    with translate_errors("check the rate limit"):
        client = get_client()
        count = client.incr(key)
        if count == 1:
            # Only set on the window's first hit -- INCR on every later hit in
            # the same window must never slide the key's expiry outward, or a
            # steady trickle of requests would keep the window open forever.
            # PEXPIRE (milliseconds), not EXPIRE: `window` is normally a whole
            # number of seconds in production, but the unit tests exercise
            # sub-second windows directly to keep the suite fast, and EXPIRE
            # itself rejects a fractional argument outright.
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
    """Test-only: clear every rate-limit key.

    Starlette's TestClient sends every request from the same fake source IP
    for the life of the pytest process, so without a reset between tests the
    counters here would accumulate across unrelated test functions and start
    rejecting their login/register calls with spurious 429s. See the
    autouse fixture in tests/conftest.py.
    """
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
    """The caller's address, as this module resolves it, for anything outside
    rate limiting that needs it.

    A thin public name over `_client_ip` rather than a second implementation:
    the trusted-proxy walk is subtle enough (rightmost-untrusted, not leftmost)
    that a login-notification email quietly using a different rule would report
    an address the rate limiter never saw. Returns None rather than the
    "unknown" sentinel, because a notice that says nothing is better than one
    that says something wrong.
    """
    ip = _client_ip(request)
    return None if not ip or ip == "unknown" else ip
