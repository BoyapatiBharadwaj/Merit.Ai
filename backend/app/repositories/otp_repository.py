"""Persistence for one-time passcodes. No policy lives here -- see otp_service."""
from datetime import datetime

from sqlalchemy.orm import Session

from app.models.otp import OtpCode, OtpPurpose


def create(db: Session, *, email: str, purpose: OtpPurpose, code_hash: str,
           expires_at: datetime, commit: bool = True) -> OtpCode:
    row = OtpCode(email=email, purpose=purpose, code_hash=code_hash, expires_at=expires_at)
    db.add(row)
    if commit:
        db.commit()
        db.refresh(row)
    else:
        db.flush()
    return row


def get_latest(db: Session, *, email: str, purpose: OtpPurpose) -> OtpCode | None:
    """The most recently issued code for this address and purpose.

    Only the newest one is ever considered. Issuing a new code therefore
    supersedes the previous one without needing to delete it, which keeps the
    audit trail intact while making sure exactly one code is live at a time --
    a user who requests a second code and then types the first must be rejected,
    not quietly let through.
    """
    return (
        db.query(OtpCode)
        .filter(OtpCode.email == email, OtpCode.purpose == purpose)
        .order_by(OtpCode.created_at.desc(), OtpCode.id.desc())
        .first()
    )


def mark_consumed(db: Session, row: OtpCode, *, when: datetime, commit: bool = True) -> OtpCode:
    row.consumed_at = when
    if commit:
        db.commit()
        db.refresh(row)
    return row


def record_attempt(db: Session, row: OtpCode, *, commit: bool = True) -> OtpCode:
    """Count one wrong guess.

    Committed immediately and separately from the verification result, so a
    caller that aborts afterwards still leaves the attempt counted. A failed
    guess that isn't durably recorded is a free guess.
    """
    row.attempts = (row.attempts or 0) + 1
    if commit:
        db.commit()
        db.refresh(row)
    return row


def delete_expired(db: Session, *, before: datetime, commit: bool = True) -> int:
    """Housekeeping: drop codes that expired before `before`.

    Not called on the request path. Rows here are small but the table only ever
    grows, so this exists for a periodic sweep (the reminder loop calls it) to
    keep it from becoming unbounded on a long-lived deployment.
    """
    deleted = db.query(OtpCode).filter(OtpCode.expires_at < before).delete(synchronize_session=False)
    if commit:
        db.commit()
    return deleted
