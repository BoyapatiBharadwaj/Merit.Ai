"""
Work that should not happen inside a request, run by RQ over Redis (see
app/core/queues.py) and executed by the `worker` service (app/worker/main.py).

Every job here opens its own database session: a queued job runs on a worker
process that shares no state (session, transaction, identity map) with
whatever request enqueued it.

What belongs here is work that is slow, retriable, and whose result nobody in
the request is waiting on:

    transactional email     one SMTP handshake, retried on failure
    PDF result reports      seconds for a large cohort
    bulk email               one SMTP handshake per recipient
    housekeeping purges      on-demand, in addition to the scheduler's own

What does NOT belong here, and is deliberately still synchronous:

    face verification      the candidate is staring at a spinner
    ID-card OCR            gates entry to the exam
    code execution         the student pressed "Run"

Queuing those would trade a two-second wait for an unbounded one plus a
polling endpoint to find out when it finished -- worse on every axis that
matters to the person waiting.

Unlike the Postgres-outbox poller this replaces, a job here is not looked up
by a string key in a registry: `app/core/queues.enqueue()` is only ever called
by this application's own code with a real function reference, the same way
any other Python call is made, so there is no untrusted-input path that could
select an arbitrary job the way a hand-rolled poller reading job names out of
a database table would need to guard against.

Retries are RQ's, not hand-rolled: every `enqueue()` call attaches a `Retry`
with exponential backoff (see app/core/queues.backoff_intervals), and a job
function signals "try again" simply by raising. A job's own code is still
responsible for recording ITS OWN durable outcome (e.g. an email_outbox row's
status/attempts/error/sent_at) before it returns or raises -- Postgres is
what a human or another service ever reads back, not RQ's internal job state,
which is a queue implementation detail with no guaranteed retention.
"""
import logging
from datetime import datetime, timezone

from app.core import queues
from app.core.config import settings
from app.core.locks import distributed_lock
from app.database.session import SessionLocal

logger = logging.getLogger("app")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ------------------------------------------------------------------------------
# Email delivery
# ------------------------------------------------------------------------------

def deliver_outbox_email(outbox_id: int) -> dict:
    """Attempt delivery of one already-created email_outbox row, and record
    the real outcome.

    Idempotent by construction: a row already `sent` (this attempt raced a
    previous one, or the job is being retried after the send actually
    succeeded but something failed recording it) is skipped rather than
    mailed twice. Raises on a failed send that has retries left, which is
    what asks RQ's Worker to try again after the next backoff interval; once
    the row's own attempt budget is exhausted it returns normally instead of
    raising, since a FAILED row has nothing further to retry towards and a
    stuck job in RQ's failed-job registry would just be noise.
    """
    from app.models.email_outbox import EmailOutboxStatus
    from app.repositories import email_outbox_repository
    from app.services import email_service

    db = SessionLocal()
    try:
        row = email_outbox_repository.get(db, outbox_id)
        if row is None:
            logger.warning("deliver_outbox_email: outbox row %s no longer exists", outbox_id)
            return {"outbox_id": outbox_id, "skipped": "row not found"}
        if row.status == EmailOutboxStatus.SENT:
            return {"outbox_id": outbox_id, "skipped": "already sent"}

        if email_service.send(to=row.to_address, subject=row.subject,
                              text_body=row.text_body, html_body=row.html_body):
            email_outbox_repository.mark_sent(db, row, when=_utcnow())
            return {"outbox_id": outbox_id, "sent": True}

        email_outbox_repository.mark_attempt_failed(
            db, row,
            error="SMTP send failed; see the core-api/worker log for the underlying error "
                  "(authentication, connection, or the server refused the message).",
            next_retry_at=None,  # RQ's own Retry schedules the re-run; no Postgres-side timer needed
        )
        if row.status == EmailOutboxStatus.FAILED:
            # Attempt budget exhausted -- this is the terminal, recorded
            # outcome the whole outbox exists to produce. Nothing left to gain
            # from asking RQ for yet another attempt.
            return {"outbox_id": outbox_id, "sent": False, "final": True}
        raise RuntimeError(f"Email delivery failed for outbox #{outbox_id}; will retry.")
    finally:
        db.close()


def notify_access_request(request_id: int, outbox_ids: list[int]) -> dict:
    """Deliver every admin-notification email for one access request, and
    stamp `last_notified_at` only once ALL of them have actually gone out.

    One job covers every recipient rather than one job per recipient so the
    "all delivered" check has a single, obviously-correct place to live: a
    request notified to two of three admins is not "notified" in the sense
    that matters (the third).
    """
    from app.models.access_request import AccessRequest
    from app.models.email_outbox import EmailOutboxStatus
    from app.repositories import email_outbox_repository
    from app.services import email_service

    db = SessionLocal()
    try:
        rows = [email_outbox_repository.get(db, oid) for oid in outbox_ids]
        rows = [r for r in rows if r is not None]

        for row in rows:
            if row.status == EmailOutboxStatus.SENT:
                continue
            if email_service.send(to=row.to_address, subject=row.subject,
                                  text_body=row.text_body, html_body=row.html_body):
                email_outbox_repository.mark_sent(db, row, when=_utcnow())
            else:
                email_outbox_repository.mark_attempt_failed(
                    db, row, error="SMTP send failed while notifying admins of an access request.",
                    next_retry_at=None,
                )

        all_sent = all(r.status == EmailOutboxStatus.SENT for r in rows)
        if all_sent:
            request = db.query(AccessRequest).filter(AccessRequest.id == request_id).first()
            if request is not None:
                request.last_notified_at = _utcnow()
                db.commit()
            return {"request_id": request_id, "notified": True}

        still_retryable = any(r.status == EmailOutboxStatus.PENDING for r in rows)
        if still_retryable:
            raise RuntimeError(f"Access request {request_id}: not every admin notification "
                               "delivered yet; will retry.")
        # Every row is either sent or has exhausted its own attempt budget --
        # terminal, and last_notified_at correctly stays unset (see
        # access_request_service._notify_admins for why that must never be
        # true unless every recipient really was told).
        return {"request_id": request_id, "notified": False, "reason": "one or more recipients never delivered"}
    finally:
        db.close()


def send_bulk_email(recipients: list[str], subject: str, text_body: str,
                    html_body: str | None = None) -> dict:
    """Send one message to many recipients, one connection at a time.

    Worth queuing precisely because it is slow: Gmail wants a full SMTP
    handshake per message, so a 200-candidate announcement is minutes of wall
    clock. Failures are counted, not raised -- one bad address must not
    abandon the other 199.
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


# ------------------------------------------------------------------------------
# Reports
# ------------------------------------------------------------------------------

def build_attempt_report_pdf(attempt_id: int) -> dict:
    """Render one attempt's result PDF.

    Locked per attempt_id: an examiner double-clicking "export" (or a retry
    racing the original attempt) must not run report generation twice
    concurrently for the same attempt. Skips rather than waits when the lock
    is already held, since a second, redundant generation of the exact same
    report has no value over the one already in flight.
    """
    from app.repositories import attempt_repository
    from app.services import attempt_service

    with distributed_lock(f"report-generation:{attempt_id}", blocking_timeout=0) as acquired:
        if not acquired:
            return {"attempt_id": attempt_id, "generated": False, "reason": "already generating"}

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


# ------------------------------------------------------------------------------
# Housekeeping
# ------------------------------------------------------------------------------

def purge_expired_data() -> dict:
    """One housekeeping sweep, callable on demand as well as on the
    scheduler's own interval -- exposing it as a job means an administrator
    can trigger a purge immediately after changing a retention setting,
    instead of waiting for the next tick."""
    from app.services import biometric_service, otp_service

    db = SessionLocal()
    try:
        return {
            "otp_codes_deleted": otp_service.purge_expired(db),
            "students_erased": biometric_service.purge_expired(db),
        }
    finally:
        db.close()


# ------------------------------------------------------------------------------
# Enqueue helpers -- the only functions the rest of the app should call
# ------------------------------------------------------------------------------

def enqueue_email(outbox_id: int):
    return queues.enqueue(queues.QUEUE_EMAILS, deliver_outbox_email, outbox_id)


def enqueue_access_request_notification(request_id: int, outbox_ids: list[int]):
    return queues.enqueue(queues.QUEUE_EMAILS, notify_access_request, request_id, outbox_ids)


def enqueue_bulk_email(recipients: list[str], subject: str, text_body: str, html_body: str | None = None):
    return queues.enqueue(queues.QUEUE_DEFAULT, send_bulk_email, recipients, subject, text_body, html_body)


def enqueue_report(attempt_id: int):
    return queues.enqueue(queues.QUEUE_REPORTS, build_attempt_report_pdf, attempt_id,
                          job_timeout=settings.JOB_TIMEOUT_SECONDS, retry=False)


def enqueue_purge() -> None:
    queues.enqueue(queues.QUEUE_DEFAULT, purge_expired_data, retry=False)
