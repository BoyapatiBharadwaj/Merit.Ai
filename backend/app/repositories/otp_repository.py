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


def get_latest(db: Session, *, email: str, purpose: OtpPurpose, lock: bool = False) -> OtpCode | None:
    """The most recently issued code for this address and purpose."""
    query = (
        db.query(OtpCode)
        .filter(OtpCode.email == email, OtpCode.purpose == purpose)
        .order_by(OtpCode.created_at.desc(), OtpCode.id.desc())
    )
    if lock and _supports_row_locks(db):
        query = query.with_for_update()
    return query.first()


# SQLite has no row locks and does not need them here: it serialises write transactions, so the
# read-check-write cannot interleave.
_ROW_LOCKING_DIALECTS = frozenset({"postgresql", "mysql", "mariadb", "oracle", "mssql"})


def _supports_row_locks(db: Session) -> bool:
    try:
        return db.get_bind().dialect.name in _ROW_LOCKING_DIALECTS
    except Exception:  # pragma: no cover - a session with no bind cannot lock
        return False


def mark_consumed(db: Session, row: OtpCode, *, when: datetime, commit: bool = True) -> OtpCode:
    """`commit=False` leaves the consumption pending so the caller can commit it
    together with whatever the code authorised -- see otp_service.verify_code."""
    row.consumed_at = when
    if commit:
        db.commit()
        db.refresh(row)
    else:
        db.flush()
    return row


def record_attempt(db: Session, row: OtpCode, *, commit: bool = True) -> OtpCode:
    """Count one wrong guess."""
    row.attempts = (row.attempts or 0) + 1
    if commit:
        db.commit()
        db.refresh(row)
    return row


def consume_outstanding(db: Session, *, email: str, purpose: OtpPurpose,
                        when: datetime, commit: bool = False) -> int:
    """Mark every live code for this (email, purpose) as spent."""
    updated = (
        db.query(OtpCode)
        .filter(
            OtpCode.email == email,
            OtpCode.purpose == purpose,
            OtpCode.consumed_at.is_(None),
        )
        .update({OtpCode.consumed_at: when}, synchronize_session=False)
    )
    if commit:
        db.commit()
    else:
        db.flush()
    return updated


def delete_expired(db: Session, *, before: datetime, commit: bool = True) -> int:
    """Housekeeping: drop codes that expired before `before`."""
    deleted = db.query(OtpCode).filter(OtpCode.expires_at < before).delete(synchronize_session=False)
    if commit:
        db.commit()
    return deleted
