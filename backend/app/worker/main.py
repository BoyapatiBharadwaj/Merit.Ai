"""Merit.Ai Worker -- an RQ Worker consuming the
emails/reports/default queues over Redis (see app/core/queues.py).
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
    # with_scheduler=True: RQ's built-in scheduler thread, which is what actually re-enqueues a
    # job after a Retry's backoff interval elapses -- without it, a failed job's `retry` would
    # be recorded but nothing would ever wake it back up.
    worker.work(with_scheduler=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
