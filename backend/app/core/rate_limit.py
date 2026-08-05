"""
Lightweight, dependency-free per-IP rate limiting for the unauthenticated
auth endpoints (login, student self-registration), where there is no
account or session yet to throttle on -- the client's source IP is all
there is.

Fixed-window counter per (bucket, client_ip): a deque of request timestamps
is kept per key and trimmed to the current window on every check, so a given
key's memory use stays bounded by its own recent traffic. A single
process-wide lock guards the shared dict, because FastAPI runs each sync
`def` endpoint on its own worker thread -- the same reasoning (and the same
kind of bug if skipped) as the locks added around the shared MediaPipe/
ArcFace model instances in app/ai/face_service.py and app/ai/pose_service.py.

Two storage backends, chosen at runtime:

  * **Redis**, when REDIS_URL is set. Every API worker and replica then counts
    against one shared budget. This is required once core-api runs more than a
    single uvicorn worker -- N processes with N private counters means an
    attacker gets N times the limit, and nothing about the system looks wrong
    while it happens.
  * **In-process**, otherwise. Exactly correct for one worker, and what the
    test suite and any Redis-less deployment use. A Redis outage also falls
    back here rather than failing the request: looser limits for a moment beat
    nobody being able to sign in.

One scope limit that remains:

  * The in-process `_buckets` dict is never pruned of long-idle keys, only
    the timestamps inside each one. An attacker rotating through a very
    large number of distinct source IPs could grow it unboundedly. Doing
    that at meaningful scale needs real distributed infrastructure (a
    botnet), which is a fundamentally different threat than the
    single-source credential-stuffing this exists to stop; a TTL-evicting
    store is the right answer if that ever becomes a real concern here.
"""
import ipaddress
import logging
import threading
import time
from collections import defaultdict, deque
from functools import lru_cache

from fastapi import HTTPException, Request, status

from app.core import shared_state
from app.core.config import settings

logger = logging.getLogger("app")

_lock = threading.Lock()
_buckets: dict[tuple[str, str], deque] = defaultdict(deque)


def _client_ip(request: Request) -> str:
    """The caller's address: the TCP peer, or the forwarded one if a trusted
    proxy put it there.

    The comment that used to live here argued that request.client.host is the
    right key because rotating it costs an attacker a real new source rather
    than a different header value. That reasoning is correct and the code was
    still wrong in deployment, because behind the bundled nginx the TCP peer is
    the PROXY -- one address for every candidate in the building. Ten wrong
    passwords from one person exhausted the login budget for the entire exam
    hall, and the symptom would have been "the login page is broken" during a
    live sitting.

    Reading X-Forwarded-For unconditionally would have been worse: any client
    can set that header, so it would hand out a fresh budget per request to
    exactly the attacker the limit exists to stop. Both halves are needed --
    believe the header, but only from a peer inside TRUSTED_PROXY_IPS.
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
    """Count one request against (bucket, identity) and raise 429 if over budget.

    Split out of `rate_limit` so the same fixed-window counter can be keyed on
    something other than a source IP. The authenticated proctoring endpoints key
    on user id instead (see app/api/deps.py::rate_limit_user): an IP is the only
    identity available before login, but once a request carries a token the user
    is both the more precise key and the harder one to rotate -- and keying those
    endpoints by IP would throttle an entire exam hall behind one NAT as though
    it were a single abuser.

    Backed by Redis when it is configured, so every API worker and replica
    counts against ONE budget. Without Redis it falls back to the per-process
    deque below, which is correct for a single worker and quietly wrong for
    several -- see _consume_in_process.
    """
    if _consume_in_redis(bucket, identity, limit=limit, window=window, message=message):
        return
    _consume_in_process(bucket, identity, limit=limit, window=window, message=message)


def _consume_in_redis(bucket: str, identity: str, *, limit: int, window: int, message: str) -> bool:
    """Shared counter. Returns False if Redis isn't available, so the caller falls back.

    INCR + EXPIRE in one pipeline, not a read-modify-write. Reading the count and
    then writing it back would let two workers both see `limit - 1` and both
    proceed, which is precisely the race a shared counter exists to remove.
    INCR is atomic server-side, so the Nth caller always gets N.

    The window is a fixed bucket keyed on the current interval rather than a
    sliding log of timestamps: it costs one integer per key instead of a list,
    and the difference in fairness at a boundary is not worth per-request memory
    proportional to traffic.
    """
    client = shared_state.get_client()
    if client is None:
        return False

    now = int(time.time())
    slot = now // window
    key = f"ratelimit:{bucket}:{identity}:{slot}"

    try:
        pipe = client.pipeline()
        pipe.incr(key)
        # Set on every call rather than only on creation: a key that somehow
        # loses its TTL would otherwise count forever and permanently lock the
        # caller out. Re-setting an equal TTL is free.
        pipe.expire(key, window)
        count, _ = pipe.execute()
    except Exception:
        # A Redis blip must never turn into a 500 on a login. Fall through to
        # the in-process counter, which is strictly more permissive but always
        # available -- the failure mode is "limits are looser for a moment",
        # not "nobody can sign in".
        logger.warning("Rate-limit check against Redis failed; using per-process counters", exc_info=True)
        return False

    if count > limit:
        retry_after = max(1, window - (now % window))
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            message,
            headers={"Retry-After": str(retry_after)},
        )
    return True


def _consume_in_process(bucket: str, identity: str, *, limit: int, window: int, message: str) -> None:
    """The original per-process sliding window.

    Correct for one worker. With several, each process keeps its own deque, so
    the effective limit is `limit x worker_count` -- which is why main.py warns
    at startup if workers > 1 and Redis is absent.
    """
    key = (bucket, identity)
    now = time.monotonic()
    with _lock:
        timestamps = _buckets[key]
        cutoff = now - window
        while timestamps and timestamps[0] < cutoff:
            timestamps.popleft()
        if len(timestamps) >= limit:
            retry_after = max(1, int(window - (now - timestamps[0])))
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                message,
                headers={"Retry-After": str(retry_after)},
            )
        timestamps.append(now)


def _reset_all() -> None:
    """Test-only: clear every counter.

    Starlette's TestClient sends every request from the same fake source IP
    for the life of the pytest process, so without a reset between tests the
    counters here would accumulate across unrelated test functions and start
    rejecting their login/register calls with spurious 429s. See the
    autouse fixture in tests/conftest.py.
    """
    with _lock:
        _buckets.clear()
    # The trusted-proxy answer is memoised per address, and a test that changes
    # TRUSTED_PROXY_IPS would otherwise keep getting the previous verdict.
    _is_trusted_proxy.cache_clear()
    # Redis keys as well, when it is in play -- otherwise a test that exhausts a
    # limit leaves it exhausted for every test after it.
    client = shared_state.get_client()
    if client is not None:
        try:
            for key in client.scan_iter("ratelimit:*", count=500):
                client.delete(key)
        except Exception:
            logger.warning("Could not clear Redis rate-limit keys", exc_info=True)


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
