"""
Work that should not happen inside a request.

`enqueue()` is the only entry point. It has one property everything else depends
on: **it always runs the job.** With Redis it hands the work to the worker
service and returns immediately; without Redis it runs it inline and returns
when it's done. A caller never has to branch on whether a queue exists, and no
deployment loses a feature by not running the extra container.

What belongs here is work that is slow, retriable, and whose result nobody is
waiting on:

    PDF result reports     seconds for a large cohort
    bulk email             one SMTP handshake per recipient
    post-exam analysis     scans every proctor event for an attempt

What does NOT belong here, and is deliberately still synchronous:

    face verification      the candidate is staring at a spinner
    ID-card OCR            gates entry to the exam
    code execution         the student pressed "Run"

Queuing those would trade a two-second wait for an unbounded one plus a polling
endpoint to find out when it finished -- worse on every axis that matters to the
person waiting.
"""
import logging

from app.core import shared_state
from app.core.config import settings

logger = logging.getLogger("app")


def _queue():
    """The RQ queue, or None when Redis isn't configured."""
    client = shared_state.get_client()
    if client is None:
        return None
    try:
        from rq import Queue

        return Queue(
            settings.JOB_QUEUE_NAME,
            connection=client,
            default_timeout=settings.JOB_TIMEOUT_SECONDS,
            result_ttl=settings.JOB_RESULT_TTL_SECONDS,
        )
    except ImportError:
        # rq is an optional dependency for exactly this reason: a deployment
        # that never runs the worker container should not be forced to install
        # it. Logged once at debug, because on those deployments it is the
        # expected state rather than a problem.
        logger.debug("rq is not installed; background jobs will run inline.")
        return None
    except Exception:
        logger.warning("Could not open the job queue; running the job inline.", exc_info=True)
        return None


def enqueue(func, *args, description: str | None = None, **kwargs):
    """Run `func` on the worker if possible, inline otherwise.

    Returns the RQ job when queued, or the function's return value when it ran
    inline. Callers that care about the difference can check `is_async()`; most
    should not, because "it happened" is the only thing they actually need.

    `func` must be importable by the worker process by module path -- RQ
    serialises a reference, not the closure. Anything defined locally, or a
    lambda, will queue successfully and then fail inside the worker with an
    import error, which is a genuinely confusing way to lose work.
    """
    label = description or getattr(func, "__name__", "job")
    queue = _queue()

    if queue is None:
        logger.debug("No job queue available; running %s inline.", label)
        return func(*args, **kwargs)

    try:
        job = queue.enqueue(func, *args, description=label, **kwargs)
        logger.info("Queued %s (job %s)", label, job.id)
        return job
    except Exception:
        # A queue that accepts the job and then loses it is worse than one that
        # was never there -- falling back to inline execution means the work
        # still happens, just slower and on this thread.
        logger.warning("Could not queue %s; running it inline instead.", label, exc_info=True)
        return func(*args, **kwargs)


def is_async() -> bool:
    """Whether enqueue() will actually defer. Useful for a status endpoint."""
    return _queue() is not None


# ------------------------------------------------------------------------------
# Job functions
#
# Module-level and importable by path, because that is what RQ serialises. Each
# opens its own database session: the worker runs in a different process from
# the request that queued the job, so nothing about the caller's session, its
# transaction or its identity map is available here.
# ------------------------------------------------------------------------------

def send_bulk_email(recipients: list[str], subject: str, text_body: str,
                    html_body: str | None = None) -> dict:
    """Send one message to many recipients, one connection at a time.

    Worth queuing precisely because it is slow: Gmail wants a full SMTP
    handshake per message, so a 200-candidate announcement is minutes of wall
    clock. Inline, that is a request that times out; queued, it is a job.

    Failures are counted, not raised. One bad address must not abandon the other
    199 -- and email_service.send already swallows its own errors, so this is
    just reporting what it returned.
    """
    from app.services import email_service

    sent = failed = 0
    for address in recipients:
        if email_service.send(to=address, subject=subject, text_body=text_body, html_body=html_body):
            sent += 1
        else:
            failed += 1

    logger.info("Bulk email %r: %s sent, %s failed", subject, sent, failed)
    return {"sent": sent, "failed": failed, "total": len(recipients)}


def build_attempt_report_pdf(attempt_id: int) -> dict:
    """Render one attempt's result PDF and return where it was written.

    Queued rather than generated on request because reportlab time scales with
    the number of questions, and an examiner exporting a whole cohort would
    otherwise hold a worker thread per report.
    """
    from app.database.session import SessionLocal
    from app.repositories import attempt_repository
    from app.services import attempt_service

    db = SessionLocal()
    try:
        attempt = attempt_repository.get_attempt(db, attempt_id)
        if attempt is None:
            logger.warning("Report requested for attempt %s, which no longer exists", attempt_id)
            return {"attempt_id": attempt_id, "generated": False, "reason": "attempt not found"}

        report = attempt_service.build_full_report(db, attempt)
        return {"attempt_id": attempt_id, "generated": True, "questions": len(report.get("questions", []))}
    finally:
        db.close()


def purge_expired_data() -> dict:
    """One housekeeping sweep, callable on demand as well as on a schedule.

    The scheduler runs these on an interval; exposing them as a job too means an
    administrator can trigger a purge immediately after changing a retention
    setting, instead of waiting for the next tick.
    """
    from app.database.session import SessionLocal
    from app.services import biometric_service, otp_service

    db = SessionLocal()
    try:
        return {
            "otp_codes_deleted": otp_service.purge_expired(db),
            "students_erased": biometric_service.purge_expired(db),
        }
    finally:
        db.close()
