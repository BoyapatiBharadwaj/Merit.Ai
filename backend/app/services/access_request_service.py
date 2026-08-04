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
from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models.access_request import AccessRequest
from app.models.enums import AccessRequestStatus
from app.models.user import User
from app.repositories import access_request_repository, user_repository
from app.services import auth_service


def _now() -> datetime:
    return datetime.now(timezone.utc)


def submit(db: Session, *, first_name: str, last_name: str, email: str,
           organization_name: str, purpose: str) -> AccessRequest:
    """Record a request from the public form.

    Returns the existing row when this email already has one pending, so a
    double-click (or a refresh-and-resubmit) doesn't fill the admin's queue
    with duplicates. Deliberately does NOT check whether the email already has
    an account: telling an anonymous caller "that address is already
    registered" would turn this form into an account-enumeration oracle.
    """
    existing = access_request_repository.get_pending_by_email(db, email)
    if existing:
        return existing

    return access_request_repository.create(
        db,
        first_name=first_name,
        last_name=last_name,
        email=email,
        organization_name=organization_name,
        purpose=purpose,
    )


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


def approve(db: Session, request_id: int, admin: User, password: str, review_note: str | None) -> AccessRequest:
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

    user, examiner = auth_service.create_examiner(
        db,
        admin.id,
        request.first_name,
        request.last_name,
        request.email,
        password,
        request.organization_name,
        commit=False,
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
