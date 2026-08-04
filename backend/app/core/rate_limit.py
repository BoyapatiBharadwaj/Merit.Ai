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

Two deliberate scope limits, not oversights:

  * In-process, not Redis-backed. This app runs as a single uvicorn process
    (see the run instructions in the repo root), so an external shared store
    would be pure overhead for no benefit. Running multiple worker processes
    behind a load balancer would split real traffic across separate
    in-memory counters, one per process, each seeing only a fraction of any
    given attacker's requests -- worth swapping for a shared store at that
    point, not before.
  * The outer `_buckets` dict itself is never pruned of long-idle keys, only
    the timestamps inside each one. An attacker rotating through a very
    large number of distinct source IPs could grow it unboundedly. Doing
    that at meaningful scale needs real distributed infrastructure (a
    botnet), which is a fundamentally different threat than the
    single-source credential-stuffing this exists to stop; a TTL-evicting
    store is the right answer if that ever becomes a real concern here.
"""
import threading
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request, status

from app.core.config import settings

_lock = threading.Lock()
_buckets: dict[tuple[str, str], deque] = defaultdict(deque)


def _client_ip(request: Request) -> str:
    # request.client.host is the actual TCP peer uvicorn accepted the
    # connection from -- unlike an X-Forwarded-For header, it costs an
    # attacker a real new source to rotate, not just a different header
    # value, which is what makes it meaningful as a rate-limit key.
    return request.client.host if request.client else "unknown"


def rate_limit(bucket: str, max_requests: int | None = None, window_seconds: int | None = None):
    """FastAPI dependency factory: add `Depends(rate_limit("login"))` to a
    route. `bucket` namespaces the counter per caller so that, e.g.,
    hammering /auth/login can't also burn through /auth/register's budget."""
    limit = max_requests if max_requests is not None else settings.AUTH_RATE_LIMIT_MAX_REQUESTS
    window = window_seconds if window_seconds is not None else settings.AUTH_RATE_LIMIT_WINDOW_SECONDS

    def _checker(request: Request) -> None:
        key = (bucket, _client_ip(request))
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
                    "Too many attempts. Please wait a moment before trying again.",
                    headers={"Retry-After": str(retry_after)},
                )
            timestamps.append(now)

    return _checker


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
