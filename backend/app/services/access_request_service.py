"""
Examiner access request workflow.

    submit  -> pending
    approve -> creates the examiner account, marks approved
    reject  -> marks rejected, creates nothing

The public submit endpoint is unauthenticated by necessity (the whole point is
that the requester has no account yet), so it is written defensively: it never
reveals whether an email already belongs to a user, and it silently de-dupes
repeat submissions instead of erroring, so the form can't be used to probe for
registered addresses.
"""
import logging
from datetime import datetime, timezone

import secrets

from fastapi import BackgroundTasks, HTTPException, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.access_request import AccessRequest
from app.models.enums import AccessRequestStatus
from app.models.user import User
from app.repositories import access_request_repository, user_repository
from app.services import auth_service, email_service, otp_service

logger = logging.getLogger("app")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _admin_recipients(db: Session) -> list[str]:
    """Who to tell about a new access request.

    An explicitly configured ADMIN_NOTIFICATION_EMAIL wins outright -- that is
    the shared operational mailbox, and once it is set, fanning out to every
    individual admin as well would just duplicate the message. Only when it is
    unset does this fall back to the admin accounts in the database, so a
    deployment that configures nothing still gets the notification somewhere
    rather than nowhere.
    """
    configured = settings.ADMIN_NOTIFICATION_EMAIL.strip()
    if configured:
        return [configured]
    return user_repository.list_admin_emails(db)


def submit(db: Session, *, first_name: str, last_name: str, email: str,
           organization_name: str, purpose: str,
           background: BackgroundTasks | None = None) -> AccessRequest:
    """Record a request from the public form.

    Returns the existing row when this email already has one pending, so a
    double-click (or a refresh-and-resubmit) doesn't fill the admin's queue
    with duplicates. Deliberately does NOT check whether the email already has
    an account: telling an anonymous caller "that address is already
    registered" would turn this form into an account-enumeration oracle.
    """
    existing = access_request_repository.get_pending_by_email(db, email)
    if existing:
        # No email on this branch, on purpose. A repeat submission is usually a
        # double-click or a refresh, and re-notifying every admin each time
        # would turn the de-dupe that protects their queue into a way to spam
        # their inbox instead -- the same abuse this function's account-
        # enumeration defence already anticipates, through a different door.
        return existing

    request = access_request_repository.create(
        db,
        first_name=first_name,
        last_name=last_name,
        email=email,
        organization_name=organization_name,
        purpose=purpose,
    )

    subject, text, html = email_service.access_request_message(
        first_name=first_name, last_name=last_name, email=email,
        organization_name=organization_name, purpose=purpose,
    )
    for recipient in _admin_recipients(db):
        email_service.queue(background, to=recipient, subject=subject, text_body=text, html_body=html)

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
    """Approve a request and mint the examiner account it asked for.

    The account creation and the status flip are one commit, not two.
    Previously create_examiner committed the new user/examiner rows on its
    own, then the status update below committed separately -- if the process
    died (or anything else raised) in between, the examiner account existed
    but the request stayed "pending" forever, with no way to re-approve it
    (the email-already-exists check above would now block a retry) and no
    record of who it belonged to. commit=False keeps the account creation
    flushed-but-uncommitted so the single db.commit() below either lands both
    changes or, on failure, rolls back both -- no orphaned account, no
    permanently stuck request.
    """
    request = _load_pending(db, request_id)

    if user_repository.get_user_by_email(db, request.email.lower()):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "An account already exists for this email. Reject this request instead.",
        )

    # A password nobody has, nobody typed and nobody will ever use.
    #
    # The approving admin used to choose this and it was mailed to the new
    # examiner in plain text. Two things were wrong with that beyond the email
    # itself: the admin knew the password, so "only this examiner could have
    # done that" was never true of anything the account did; and the credential
    # outlived its usefulness in an inbox indefinitely. Now the account is born
    # with 256 bits of noise as its password and the only way in is the
    # activation link -- which expires, works once, and is stored as a hash.
    placeholder = secrets.token_urlsafe(32)

    user, examiner = auth_service.create_examiner(
        db,
        admin.id,
        request.first_name,
        request.last_name,
        request.email,
        placeholder,
        request.organization_name,
        commit=False,
    )
    # Flagged from the start: nothing this account does is attributable to its
    # owner until they have set their own password.
    user.must_change_password = True

    try:
        request.status = AccessRequestStatus.APPROVED
        request.reviewed_at = _now()
        request.reviewed_by_id = admin.id
        request.review_note = review_note
        request.created_user_id = user.id
        # Minted inside the transaction so an approval that rolls back leaves no
        # usable link behind; handed to the mailer strictly after the commit,
        # because an email cannot be rolled back.
        token, expires_at = otp_service.issue_activation_token(db, email=request.email)
        db.commit()
        db.refresh(request)
        db.refresh(user)
        db.refresh(examiner)
    except Exception:
        db.rollback()
        raise

    # Strictly AFTER the commit. Queuing the mail inside the try block would
    # mean a rollback still sends someone a working-looking link for an account
    # that does not exist -- and unlike the database, an email cannot be rolled
    # back once it is on its way.
    subject, text, html = email_service.activation_message(
        full_name=request.full_name,
        email=request.email,
        activation_url=otp_service.activation_link(token, request.email),
        expires_hours=settings.ACTIVATION_TTL_HOURS,
        organization_name=request.organization_name,
    )
    email_service.queue(background, to=request.email, subject=subject, text_body=text, html_body=html)

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
