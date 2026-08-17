"""Examiner access request workflow."""
import logging
from datetime import datetime, timezone

from fastapi import BackgroundTasks, HTTPException, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.locks import distributed_lock
from app.models.access_request import AccessRequest
from app.models.enums import AccessRequestStatus
from app.models.user import User
from app.repositories import access_request_repository, user_repository
from app.services import email_service, examiner_provisioning_service

logger = logging.getLogger("app")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _admin_recipients(db: Session) -> list[str]:
    """Who to tell about a new access request."""
    configured = settings.ADMIN_NOTIFICATION_EMAIL.strip()
    if configured:
        return [configured]
    return user_repository.list_admin_emails(db)


def _as_utc(value: datetime | None) -> datetime | None:
    """SQLite returns naive datetimes even for timezone=True columns, so a direct comparison
    against an aware _now() raises TypeError.
    """
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _notification_is_due(request: AccessRequest) -> bool:
    """Has the cooldown elapsed since admins were last told about this request?"""
    last = _as_utc(request.last_notified_at)
    if last is None:
        return True
    elapsed = (_now() - last).total_seconds()
    return elapsed >= settings.ACCESS_REQUEST_RENOTIFY_SECONDS


def _notify_admins(db: Session, request: AccessRequest,
                   background: BackgroundTasks | None) -> bool:
    """Queue one admin-notification email per recipient and enqueue the job that delivers them
    and stamps `last_notified_at` only once every one of them has actually gone out.
    """
    subject, text, html = email_service.access_request_message(
        first_name=request.first_name, last_name=request.last_name,
        email=request.email, organization_name=request.organization_name,
        purpose=request.purpose,
    )
    recipients = _admin_recipients(db)
    if not recipients:
        # Worth saying out loud. Silence here means a request landed in the queue and nobody was
        # told, which looks from the outside exactly like the platform working.
        logger.warning("Access request %s recorded but no admin recipient is configured; "
                       "set ADMIN_NOTIFICATION_EMAIL or create an admin account.", request.email)
        return False

    from app.repositories import email_outbox_repository
    from app.worker import jobs

    outbox_ids = []
    for recipient in recipients:
        row = email_outbox_repository.create(
            db, to_address=recipient, subject=subject, text_body=text, html_body=html,
            max_attempts=settings.EMAIL_OUTBOX_MAX_ATTEMPTS,
        )
        outbox_ids.append(row.id)

    # One lock per request id: a resubmission racing the worker's own retry of an earlier
    # notification for the SAME request must not enqueue two overlapping notification
    # jobs that could both eventually try to stamp last_notified_at (harmless on its own,
    # since both would just set the same field to close timestamps, but the lock is what
    # makes "exactly one notification job in flight per request at a time" a guarantee
    # rather than a usually-true accident of timing).
    with distributed_lock(f"access-request-notify:{request.id}", blocking_timeout=0) as acquired:
        if acquired:
            jobs.enqueue_access_request_notification(request.id, outbox_ids)
        else:
            logger.info("A notification job for access request %s is already queued; "
                       "not enqueueing a second one.", request.id)
    return True


def submit(db: Session, *, first_name: str, last_name: str, email: str,
           organization_name: str, purpose: str,
           background: BackgroundTasks | None = None) -> AccessRequest:
    """Record a request from the public form."""
    existing = access_request_repository.get_pending_by_email(db, email)
    if existing:
        # Re-notify, but no more often than the cooldown. This branch used to return here
        # unconditionally, sending nothing.
        if _notification_is_due(existing):
            # last_notified_at is stamped by jobs.notify_access_request itself, only once every
            # recipient has actually been delivered -- see _notify_admins.
            _notify_admins(db, existing, background)
        return existing

    request = access_request_repository.create(
        db,
        first_name=first_name,
        last_name=last_name,
        email=email,
        organization_name=organization_name,
        purpose=purpose,
    )

    _notify_admins(db, request, background)

    return request


def _load_pending(db: Session, request_id: int) -> AccessRequest:
    request = access_request_repository.get(db, request_id)
    if not request:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Access request not found.")
    if request.status != AccessRequestStatus.PENDING:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"This request has already been {request.status.value}.",
        )
    return request


def approve(db: Session, request_id: int, admin: User, review_note: str | None,
            background: BackgroundTasks | None = None) -> AccessRequest:
    """Approve a request and mint the examiner account it asked for."""
    # Locked per request id for the life of the approval.
    with distributed_lock(f"access-request-approve:{request_id}", blocking_timeout=2.0) as acquired:
        if not acquired:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "This request is already being approved. Refresh the page in a moment.",
            )
        return _approve_locked(db, request_id, admin, review_note, background)


def _approve_locked(db: Session, request_id: int, admin: User, review_note: str | None,
                    background: BackgroundTasks | None) -> AccessRequest:
    request = _load_pending(db, request_id)

    if user_repository.get_user_by_email(db, request.email.lower()):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "An account already exists for this email. Reject this request instead.",
        )

    # Account creation and the request's status flip happen in ONE commit, via commit=False +
    # this function's own commit below.
    user, examiner, token = examiner_provisioning_service.create_examiner_pending_activation(
        db, admin_id=admin.id, first_name=request.first_name, last_name=request.last_name,
        email=request.email, organization_name=request.organization_name, commit=False,
    )

    try:
        request.status = AccessRequestStatus.APPROVED
        request.reviewed_at = _now()
        request.reviewed_by_id = admin.id
        request.review_note = review_note
        request.created_user_id = user.id
        db.commit()
        db.refresh(request)
        db.refresh(user)
        db.refresh(examiner)
    except Exception:
        db.rollback()
        raise

    # Strictly AFTER the commit. Sending inside the try block would mean a rollback still sends
    # someone a working-looking link for an account that does not exist.
    examiner_provisioning_service.send_activation_email(
        user, organization_name=request.organization_name, token=token, background=background,
    )

    return request


def reject(db: Session, request_id: int, admin: User, review_note: str | None) -> AccessRequest:
    request = _load_pending(db, request_id)
    try:
        request.status = AccessRequestStatus.REJECTED
        request.reviewed_at = _now()
        request.reviewed_by_id = admin.id
        request.review_note = review_note
        db.commit()
        db.refresh(request)
    except Exception:
        db.rollback()
        raise
    return request
