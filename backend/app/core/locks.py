"""Distributed locks, for the handful of operations that must never run twice at once across
multiple core-api or worker processes.
"""
import logging
import time
import uuid
from contextlib import contextmanager

import redis

from app.core.config import settings
from app.core.redis_client import get_client, translate_errors

logger = logging.getLogger("app")

_PREFIX = "lock:"


class _Lock:
    def __init__(self, client, key: str, ttl_seconds: float):
        self._client = client
        self._key = key
        self._ttl_ms = max(int(ttl_seconds * 1000), 1)
        self._token: str | None = None

    def acquire(self, blocking_timeout: float) -> bool:
        token = uuid.uuid4().hex
        deadline = time.monotonic() + max(blocking_timeout, 0)
        while True:
            if self._client.set(self._key, token, nx=True, px=self._ttl_ms):
                self._token = token
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.05)

    def release(self) -> None:
        """Delete the key only if it still holds OUR token."""
        if self._token is None:
            return
        token, self._token = self._token, None
        try:
            with self._client.pipeline(transaction=True) as pipe:
                pipe.watch(self._key)
                current = pipe.get(self._key)
                if current != token:
                    pipe.unwatch()
                    return
                pipe.multi()
                pipe.delete(self._key)
                pipe.execute()
        except redis.WatchError:
            # Someone else's SET landed between our GET and our MULTI.
            pass


@contextmanager
def distributed_lock(name: str, *, timeout: float | None = None, blocking_timeout: float = 5.0):
    """Hold an exclusive, expiring lock named `name` for the life of the `with` block."""
    ttl = timeout if timeout is not None else settings.LOCK_DEFAULT_TTL_SECONDS
    with translate_errors(f"acquire the {name} lock"):
        lock = _Lock(get_client(), f"{_PREFIX}{name}", ttl)
        acquired = lock.acquire(blocking_timeout)
    try:
        yield acquired
    finally:
        if acquired:
            with translate_errors(f"release the {name} lock"):
                lock.release()
