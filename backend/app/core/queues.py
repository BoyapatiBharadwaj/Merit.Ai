"""RQ queues, backed by the same Redis this module's siblings (rate_limit, otp_redis_store, locks) use."""
import logging

import redis
from rq import Queue, Retry

from app.core.config import settings
from app.core.redis_client import get_raw_client, RedisUnavailableError

logger = logging.getLogger("app")

QUEUE_EMAILS = "emails"
QUEUE_REPORTS = "reports"
QUEUE_DEFAULT = "default"

ALL_QUEUES = (QUEUE_EMAILS, QUEUE_REPORTS, QUEUE_DEFAULT)

# Test-only: makes enqueue() run the job body immediately, in this process, instead of pushing
# it onto Redis for a separate worker to pick up.
_eager = False


def set_eager_for_tests(value: bool) -> None:
    global _eager
    _eager = value


def backoff_intervals() -> list[int]:
    """Seconds between retries: EMAIL_OUTBOX_RETRY_BASE_SECONDS doubling each time, capped at
    EMAIL_OUTBOX_RETRY_MAX_SECONDS -- the same curve email_service._backoff already used for
    the Postgres-polling worker this replaces, so the wall-clock retry behaviour an operator
    already tuned for does not change just because the mechanism did.
    """
    base = settings.EMAIL_OUTBOX_RETRY_BASE_SECONDS
    cap = settings.EMAIL_OUTBOX_RETRY_MAX_SECONDS
    return [min(base * (2 ** i), cap) for i in range(max(settings.JOB_MAX_RETRIES, 0))]


def get_queue(name: str) -> Queue:
    try:
        return Queue(name, connection=get_raw_client(), is_async=not _eager)
    except redis.RedisError as exc:
        raise RedisUnavailableError(f"Could not reach Redis to open the {name!r} queue.") from exc


def enqueue(queue_name: str, func, *args, retry: bool = True, job_timeout: int | None = None, **kwargs):
    """Queue `func(*args, **kwargs)` on `queue_name`."""
    try:
        queue = get_queue(queue_name)
        return queue.enqueue_call(
            func=func,
            args=args,
            kwargs=kwargs,
            timeout=job_timeout or settings.JOB_TIMEOUT_SECONDS,
            retry=Retry(max=settings.JOB_MAX_RETRIES, interval=backoff_intervals()) if retry else None,
        )
    except redis.RedisError as exc:
        raise RedisUnavailableError(f"Could not queue {getattr(func, '__name__', func)!r} on "
                                    f"{queue_name!r}.") from exc
    except RedisUnavailableError:
        raise
    except Exception:
        # Belt-and-suspenders: RQ's own eager-mode execution (see
        # set_eager_for_tests) catches a job function's exception itself and
        # records the job as failed rather than propagating it here, so this branch
        # is not expected to run against the RQ version this was built against.
        logger.exception("Job %r raised while enqueueing/running eagerly.",
                         getattr(func, "__name__", func))
        return None
