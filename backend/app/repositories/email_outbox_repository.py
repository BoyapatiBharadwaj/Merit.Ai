"""Data access for the email outbox. Policy (attempt counts, backoff timing)
lives in app/services/email_service.py; this module only reads and writes rows."""
from datetime import datetime

from sqlalchemy.orm import Session

from app.models.email_outbox import EmailOutbox, EmailOutboxStatus


def create(db: Session, *, to_address: str, subject: str, text_body: str,
          html_body: str | None, max_attempts: int, commit: bool = True) -> EmailOutbox:
    row = EmailOutbox(
        to_address=to_address, subject=subject, text_body=text_body, html_body=html_body,
        status=EmailOutboxStatus.PENDING, attempts=0, max_attempts=max_attempts,
    )
    db.add(row)
    db.flush()
    if commit:
        db.commit()
        db.refresh(row)
    return row


def mark_sent(db: Session, row: EmailOutbox, *, when: datetime, commit: bool = True) -> None:
    row.status = EmailOutboxStatus.SENT
    row.sent_at = when
    row.last_error = None
    row.next_retry_at = None
    if commit:
        db.commit()


def mark_attempt_failed(db: Session, row: EmailOutbox, *, error: str, next_retry_at: datetime | None,
                        commit: bool = True) -> None:
    """One attempt failed. Stays `pending` (eligible for another try) unless the
    attempt budget is now exhausted, in which case it becomes `failed` and stops
    being picked up -- a human needs to look at `last_error`."""
    row.attempts += 1
    row.last_error = error[:500]
    if row.attempts >= row.max_attempts:
        row.status = EmailOutboxStatus.FAILED
        row.next_retry_at = None
    else:
        row.next_retry_at = next_retry_at
    if commit:
        db.commit()


def get(db: Session, outbox_id: int) -> EmailOutbox | None:
    return db.query(EmailOutbox).filter(EmailOutbox.id == outbox_id).first()


def list_due(db: Session, *, when: datetime, limit: int = 50) -> list[EmailOutbox]:
    """Rows worth retrying right now: still pending, and either never attempted
    or past their backoff window."""
    return (
        db.query(EmailOutbox)
        .filter(EmailOutbox.status == EmailOutboxStatus.PENDING)
        .filter((EmailOutbox.next_retry_at.is_(None)) | (EmailOutbox.next_retry_at <= when))
        .order_by(EmailOutbox.created_at.asc())
        .limit(limit)
        .all()
    )


def list_all(db: Session, *, status: str | None = None, limit: int = 200) -> list[EmailOutbox]:
    query = db.query(EmailOutbox)
    if status:
        query = query.filter(EmailOutbox.status == status)
    return query.order_by(EmailOutbox.created_at.desc()).limit(limit).all()
