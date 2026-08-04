"""
Merit.Ai Worker -- consumes the background job queue.

A plain RQ worker, run as `python -m app.worker.main`. It shares the core-api
image because the jobs it runs import the same services, models and settings --
a separate image would mean maintaining two copies of the same dependency set
and hoping they stay identical.

Scale this one freely: unlike the scheduler, more replicas simply means more
jobs in flight. RQ hands each job to exactly one worker, so there is no
coordination to get wrong.

Exits immediately when REDIS_URL is unset rather than idling, because a worker
with no queue has nothing to do and a container that looks healthy while doing
nothing is worse than one that stops and says why.
"""
import logging
import sys

from app.core import shared_state
from app.core.config import settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("worker")


def main() -> int:
    client = shared_state.get_client()
    if client is None:
        logger.error(
            "REDIS_URL is not set or Redis is unreachable, so there is no queue to consume. "
            "Jobs will run inline inside core-api instead (see app/worker/jobs.py). "
            "Set REDIS_URL to use this service."
        )
        return 1

    try:
        from rq import Queue, Worker
    except ImportError:
        logger.error("rq is not installed in this image. Add it to requirements.txt.")
        return 1

    queue = Queue(settings.JOB_QUEUE_NAME, connection=client)
    logger.info("Worker listening on %r", settings.JOB_QUEUE_NAME)
    # with_scheduler=False on purpose: periodic work belongs to the scheduler
    # service, which is single-replica. Letting every worker also run RQ's
    # scheduler would reintroduce exactly the duplicate-firing problem that
    # splitting the scheduler out was meant to remove.
    Worker([queue], connection=client).work(with_scheduler=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
