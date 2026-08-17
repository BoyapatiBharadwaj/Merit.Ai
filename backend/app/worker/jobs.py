"""Work that should not happen inside a request, run by RQ over Redis (see app/core/queues.py)
and executed by the `worker` service (app/worker/main.py).
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


# --- - ---
# Email delivery ----------------------------------------------------------------------------

def deliver_outbox_email(outbox_id: int) -> dict:
    """Attempt delivery of one already-created email_outbox row, and record the real outcome."""
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
            # Attempt budget exhausted -- this is the terminal,
            # recorded outcome the whole outbox exists to produce.
            return {"outbox_id": outbox_id, "sent": False, "final": True}
        raise RuntimeError(f"Email delivery failed for outbox #{outbox_id}; will retry.")
    finally:
        db.close()


def notify_access_request(request_id: int, outbox_ids: list[int]) -> dict:
    """Deliver every admin-notification email for one access request, and stamp
    `last_notified_at` only once ALL of them have actually gone out.
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
        # Every row is either sent or has exhausted its own attempt budget.
        return {"request_id": request_id, "notified": False, "reason": "one or more recipients never delivered"}
    finally:
        db.close()


def send_bulk_email(recipients: list[str], subject: str, text_body: str,
                    html_body: str | None = None) -> dict:
    """Send one message to many recipients, one connection at a time."""
    from app.services import email_service

    sent = failed = 0
    for address in recipients:
        if email_service.send(to=address, subject=subject, text_body=text_body, html_body=html_body):
            sent += 1
        else:
            failed += 1

    logger.info("Bulk email %r: %s sent, %s failed", subject, sent, failed)
    return {"sent": sent, "failed": failed, "total": len(recipients)}


# --- - ---
# Reports ----------------------------------------------------------------------------

def build_attempt_report_pdf(attempt_id: int) -> dict:
    """Render one attempt's result PDF."""
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


# --- - ---
# Housekeeping ----------------------------------------------------------------------------

def purge_expired_data() -> dict:
    """One housekeeping sweep, callable on demand as well as on the scheduler's own interval."""
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
