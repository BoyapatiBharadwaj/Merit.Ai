"""
Merit.Ai Worker -- an RQ Worker consuming the emails/reports/default queues
over Redis (see app/core/queues.py).

Run as `python -m app.worker.main`. Shares the core-api image because the jobs
it runs (app/worker/jobs.py) import the same services, models and settings a
separate image would otherwise need a second copy of.

This process needs Redis to do anything at all -- unlike the rest of the
application, which treats Redis as an optional-at-the-margin dependency for
rate limiting, OTP state and locks (each failing its own request with a
controlled 503 when Redis is down, see app/core/redis_client.py), a queue
worker with no queue to read from has nothing to do. It exits with a clear
error rather than idling silently if it cannot reach Redis at startup, so a
misconfigured REDIS_URL shows up as a failed container instead of a queue that
quietly never drains.

Scale this freely: RQ workers claim jobs from Redis atomically, so more
replicas simply means more throughput, the same property the Postgres-outbox
poller this replaces had over its own table.
"""
import logging
import sys

from redis.exceptions import RedisError
from rq import Queue, Worker

from app.core.config import settings
from app.core.queues import ALL_QUEUES
from app.core.redis_client import get_raw_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("worker")


def main() -> int:
    connection = get_raw_client()
    try:
        connection.ping()
    except RedisError:
        logger.exception("Could not reach Redis at %s; this worker has no queue to read from "
                         "and is exiting rather than idling.", settings.REDIS_URL)
        return 1

    queue_objs = [Queue(name, connection=connection) for name in ALL_QUEUES]
    logger.info("Listening on queues: %s", ", ".join(ALL_QUEUES))

    worker = Worker(queue_objs, connection=connection)
    # with_scheduler=True: RQ's built-in scheduler thread, which is what
    # actually re-enqueues a job after a Retry's backoff interval elapses --
    # without it, a failed job's `retry` would be recorded but nothing would
    # ever wake it back up.
    worker.work(with_scheduler=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
