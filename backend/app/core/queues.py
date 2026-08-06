"""
RQ queues, backed by the same Redis this module's siblings (rate_limit,
otp_redis_store, locks) use.

Three named queues, not one, so a burst of cheap OTP/notification emails can
never sit behind a slow PDF report job:

    emails    OTP codes, examiner activation + resend, access-request
              notifications, exam notices, report-ready emails
    reports   PDF result report generation
    default   bulk email, purge sweeps -- anything else

`enqueue()` is the one place that calls into RQ, for two reasons. First, it is
what makes "Redis is unreachable" fail the same controlled way as every other
Redis-backed operation in this app (see app/core/redis_client.py) instead of
each call site handling connection errors differently. Second, it is what
makes the test suite able to run the whole application with no real Redis and
no separate worker process: in "eager" mode (see set_eager_for_tests) RQ runs
the job function immediately, in-process, instead of pushing it onto a queue a
worker would need to be polling. A job's own code is responsible for recording
its outcome (e.g. in the `email_outbox` table) before it returns or raises, so
eager mode -- which has no Worker around to act on a raised exception -- never
loses that record; it only loses the automatic requeue, which is fine for a
single test asserting "attempt one failed and was recorded", and irrelevant to
production, which always runs with a real Worker (see app/worker/main.py).
"""
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

# Test-only: makes enqueue() run the job body immediately, in this process,
# instead of pushing it onto Redis for a separate worker to pick up. See the
# module docstring.
_eager = False


def set_eager_for_tests(value: bool) -> None:
    global _eager
    _eager = value


def backoff_intervals() -> list[int]:
    """Seconds between retries: EMAIL_OUTBOX_RETRY_BASE_SECONDS doubling each
    time, capped at EMAIL_OUTBOX_RETRY_MAX_SECONDS -- the same curve
    email_service._backoff already used for the Postgres-polling worker this
    replaces, so the wall-clock retry behaviour an operator already tuned for
    does not change just because the mechanism did.
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
    """Queue `func(*args, **kwargs)` on `queue_name`.

    `retry=True` (the default) attaches RQ's built-in `Retry`, which is what
    drives the exponential-backoff re-run of a job that raised -- the Worker
    re-enqueues it after `backoff_intervals()[attempt]` seconds, up to
    JOB_MAX_RETRIES times, with no extra bookkeeping needed here. Set it False
    for jobs that already do their own retry accounting (there are none of
    those yet, but the option exists rather than forcing every future job
    through the email-shaped retry curve).
    """
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
        # records the job as failed rather than propagating it here, so this
        # branch is not expected to run against the RQ version this was built
        # against. It stays as a backstop against a future RQ version
        # changing that, since the job's own code has already recorded its
        # failed attempt wherever it durably tracks one (e.g. email_outbox)
        # before raising -- there would be nothing left to do here except
        # make sure the caller that queued the work does not crash because of it.
        logger.exception("Job %r raised while enqueueing/running eagerly.",
                         getattr(func, "__name__", func))
        return None
