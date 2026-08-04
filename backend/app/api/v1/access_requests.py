"""
Examiner access requests.

One public endpoint (the marketing site's request form) and three admin-only
ones for reviewing the queue. Examiners cannot self-register, so this is the
supported route from "interested institution" to "examiner account".
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import require_admin
from app.database.session import get_db
from app.models.enums import AccessRequestStatus
from app.models.user import User
from app.repositories import access_request_repository
from app.schemas.access_request import (
    AccessRequestApprove, AccessRequestCreate, AccessRequestOut, AccessRequestReject,
)
from app.services import access_request_service

router = APIRouter(prefix="/access-requests", tags=["Access Requests"])


@router.post("", status_code=201)
def submit_access_request(payload: AccessRequestCreate, db: Session = Depends(get_db)):
    """Public. Intentionally returns the same response whether this is a new
    request or a repeat of a pending one -- see access_request_service.submit
    for why it must not reveal whether an email is already known."""
    access_request_service.submit(
        db,
        first_name=payload.first_name,
        last_name=payload.last_name,
        email=payload.email,
        organization_name=payload.organization_name,
        purpose=payload.purpose,
    )
    return {
        "submitted": True,
        "message": "Request received. You'll receive an email with your login credentials once an administrator approves your account.",
    }


@router.get("", response_model=list[AccessRequestOut])
def list_access_requests(status: AccessRequestStatus | None = None,
                         db: Session = Depends(get_db), _: User = Depends(require_admin)):
    return access_request_repository.list_all(db, status)


@router.get("/pending-count")
def pending_count(db: Session = Depends(get_db), _: User = Depends(require_admin)):
    """Cheap poll for the admin sidebar badge."""
    return {"pending": access_request_repository.count_pending(db)}


@router.post("/{request_id}/approve", response_model=AccessRequestOut)
def approve_access_request(request_id: int, payload: AccessRequestApprove,
                           db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    """Creates the examiner account and marks the request approved."""
    return access_request_service.approve(db, request_id, admin, payload.password, payload.review_note)


@router.post("/{request_id}/reject", response_model=AccessRequestOut)
def reject_access_request(request_id: int, payload: AccessRequestReject,
                          db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    return access_request_service.reject(db, request_id, admin, payload.review_note)
