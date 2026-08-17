"""The one place an examiner account is created."""
import logging
import secrets

from fastapi import BackgroundTasks, HTTPException, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.locks import distributed_lock
from app.models.user import User
from app.repositories import user_repository
from app.services import auth_service, email_service, otp_service

logger = logging.getLogger("app")


def create_examiner_pending_activation(db: Session, *, admin_id: int, first_name: str, last_name: str,
                                       email: str, organization_name: str | None, commit: bool = True):
    """Mint the account and its activation token. Does NOT send the email."""
    normalized_email = email.strip().lower()
    with distributed_lock(f"examiner-create:{normalized_email}", blocking_timeout=2.0) as acquired:
        if not acquired:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "An account for this email is already being created. Please wait a moment and check again.",
            )
        return _create_examiner_pending_activation_locked(
            db, admin_id=admin_id, first_name=first_name, last_name=last_name,
            email=email, organization_name=organization_name, commit=commit,
        )


def _create_examiner_pending_activation_locked(db: Session, *, admin_id: int, first_name: str, last_name: str,
                                               email: str, organization_name: str | None, commit: bool):
    placeholder = secrets.token_urlsafe(32)

    user, examiner = auth_service.create_examiner(
        db, admin_id, first_name, last_name, email, placeholder, organization_name, commit=False,
    )
    user.must_change_password = True

    try:
        # Minted inside the same transaction so a rollback leaves no usable link behind.
        token, _expires_at = otp_service.issue_activation_token(db, email=user.email)
        if commit:
            db.commit()
            db.refresh(user)
            db.refresh(examiner)
        else:
            db.flush()
    except Exception:
        db.rollback()
        raise

    return user, examiner, token


def provision_examiner(db: Session, *, admin_id: int, first_name: str, last_name: str,
                       email: str, organization_name: str | None,
                       background: BackgroundTasks | None = None) -> tuple[User, bool]:
    """Create an examiner account and queue the activation email, in one call."""
    user, _examiner, token = create_examiner_pending_activation(
        db, admin_id=admin_id, first_name=first_name, last_name=last_name,
        email=email, organization_name=organization_name, commit=True,
    )
    activation_sent = send_activation_email(
        user, organization_name=organization_name, token=token, background=background,
    )
    return user, activation_sent


def send_activation_email(user: User, *, organization_name: str | None, token: str,
                          background: BackgroundTasks | None = None) -> bool:
    """Queue one activation email for an already-issued token, and report whether it was
    successfully QUEUED -- not whether it has been delivered.
    """
    subject, text, html = email_service.activation_message(
        full_name=user.full_name,
        email=user.email,
        activation_url=otp_service.activation_link(token, user.email),
        expires_hours=settings.ACTIVATION_TTL_HOURS,
        organization_name=organization_name,
    )
    from app.database.session import SessionLocal

    # enqueue_tracked needs a session to write the outbox row.
    db = SessionLocal()
    try:
        email_service.enqueue_tracked(db, to=user.email, subject=subject, text_body=text, html_body=html)
    finally:
        db.close()

    return True


def resend_activation(db: Session, examiner_user_id: int,
                      background: BackgroundTasks | None = None) -> bool:
    """Issue a fresh activation link for an existing, not-yet-activated examiner and mail it."""
    user = user_repository.get_user_by_id(db, examiner_user_id)
    if not user or (user.role.name or "").lower() != "examiner":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Examiner not found.")
    if not user.must_change_password:
        # Already activated -- a stale link has nothing left to do, and
        # sending one would just confuse someone who can already sign in.
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "This examiner has already activated their account.")

    token, _ = otp_service.issue_activation_token(db, email=user.email)
    db.commit()

    organization_name = user.examiner_profile.organization_name if user.examiner_profile else None
    return send_activation_email(user, organization_name=organization_name, token=token, background=background)
