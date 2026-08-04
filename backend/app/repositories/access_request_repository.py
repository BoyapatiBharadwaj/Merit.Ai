"""Data access for the AccessRequest table."""
from sqlalchemy.orm import Session

from app.models.access_request import AccessRequest
from app.models.enums import AccessRequestStatus


def create(db: Session, *, first_name: str, last_name: str, email: str,
           organization_name: str, purpose: str) -> AccessRequest:
    request = AccessRequest(
        first_name=first_name.strip(),
        last_name=last_name.strip(),
        email=email.strip().lower(),
        organization_name=organization_name.strip(),
        purpose=purpose.strip(),
        status=AccessRequestStatus.PENDING,
    )
    db.add(request)
    db.commit()
    db.refresh(request)
    return request


def get(db: Session, request_id: int) -> AccessRequest | None:
    return db.query(AccessRequest).filter(AccessRequest.id == request_id).first()


def get_pending_by_email(db: Session, email: str) -> AccessRequest | None:
    return (
        db.query(AccessRequest)
        .filter(AccessRequest.email == email.strip().lower(), AccessRequest.status == AccessRequestStatus.PENDING)
        .first()
    )


def list_all(db: Session, status: AccessRequestStatus | None = None) -> list[AccessRequest]:
    query = db.query(AccessRequest)
    if status is not None:
        query = query.filter(AccessRequest.status == status)
    # Newest first -- an admin reviewing a queue wants the latest at the top.
    return query.order_by(AccessRequest.created_at.desc(), AccessRequest.id.desc()).all()


def count_pending(db: Session) -> int:
    return db.query(AccessRequest).filter(AccessRequest.status == AccessRequestStatus.PENDING).count()
