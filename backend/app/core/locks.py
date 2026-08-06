"""
Distributed locks, for the handful of operations that must never run twice at
once across multiple core-api or worker processes: creating an examiner
account, approving an access request, enqueueing a notification, submitting an
exam attempt, generating a report, and a scheduler pass.

Backed by Redis's own `SET NX PX` primitive: acquiring a lock is one atomic
SET that only succeeds if the key does not already exist, with an expiry
attached in the same command. Releasing is a WATCH/MULTI/EXEC optimistic
transaction that only deletes the key if it still holds the same random token
this process set -- the same "never release a lock you don't own" guarantee
redis-py's own `Redis.lock()` gives via a Lua script, implemented instead with
a plain Redis transaction so this module has no dependency on server-side
scripting (EVAL/EVALSHA) being available. That matters here specifically
because fakeredis -- the in-memory stand-in the test suite runs against, the
same way it runs Postgres against SQLite -- does not implement scripting, and
a lock implementation only tested against a real Redis server would pass in
production and silently be untested everywhere this suite runs.

Every lock carries a TTL. An unbounded lock held by a process that crashes
mid-operation would deadlock every future attempt at that operation forever;
a TTL means the worst case is "another attempt has to wait until the old lock
expires", never "nothing can ever run again".

Redis being unreachable when a lock is needed is treated the same way as
everywhere else Redis is load-bearing (see app/core/redis_client.py): the
operation refuses with a controlled failure rather than silently proceeding
unprotected, because "duplicate examiner accounts" and "double-submitted
exam attempts" are exactly the failure modes a lock exists to prevent, and
skipping the lock when it cannot be checked would defeat the whole point.
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
        """Delete the key only if it still holds OUR token.

        A WATCH/MULTI/EXEC transaction rather than a Lua script (see this
        module's docstring): the transaction aborts (EXEC returns None) if
        another client wrote to the key between the WATCH and the EXEC --
        which is exactly the case where our TTL already expired and someone
        else's lock is now live, the one case a naive `DEL` would corrupt.
        """
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
            # Someone else's SET landed between our GET and our MULTI -- same
            # outcome as `current != token` above: our lock had already
            # expired, so there is nothing of ours left to delete.
            pass


@contextmanager
def distributed_lock(name: str, *, timeout: float | None = None, blocking_timeout: float = 5.0):
    """Hold an exclusive, expiring lock named `name` for the life of the `with`
    block.

    Yields `True` if the lock was acquired, `False` if another process already
    holds it (after waiting up to `blocking_timeout` seconds). Callers that
    only want to prevent a duplicate -- not force one to wait for the other --
    should check the yielded value and short-circuit when it is `False`, the
    same way `access_request_service.approve` already checks
    `_load_pending`'s status before doing any work.

    Raises RedisUnavailableError (via translate_errors) if Redis cannot be
    reached at all -- see this module's docstring for why that refuses the
    operation rather than running it unprotected.
    """
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
