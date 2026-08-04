"""
One-time passcodes for email verification and password reset.

Deliberately keyed on an email address rather than a user id, because the two
flows it serves sit on opposite sides of an account existing:

  * SIGNUP verification runs *before* there is a user row to point at.
  * PASSWORD_RESET runs for an address that may or may not have an account, and
    must behave identically either way -- see otp_service.request_code for why
    revealing which is which turns this into an account-enumeration oracle.

The code itself is never stored. Only an HMAC-SHA256 digest is kept (see
otp_service._digest), so a leaked database row cannot be replayed into someone
else's account. That is the same reasoning as hashing passwords, applied to a
credential that happens to be short-lived rather than long-lived.
"""
import enum

from sqlalchemy import Column, DateTime, Index, Integer, String, func

from app.database.session import Base
from app.models.enums import db_enum


class OtpPurpose(str, enum.Enum):
    """What an issued code entitles the holder to do.

    Purpose is part of the lookup key, not just an annotation: a code mailed
    out to verify a signup must not be replayable against the password-reset
    endpoint. Binding the two together at the query level makes that
    substitution impossible rather than merely unlikely.
    """
    SIGNUP = "signup"
    PASSWORD_RESET = "password_reset"


class OtpCode(Base):
    __tablename__ = "otp_codes"

    id = Column(Integer, primary_key=True, index=True)

    email = Column(String(150), nullable=False, index=True)
    purpose = Column(db_enum(OtpPurpose), nullable=False)

    # HMAC-SHA256 of the code, keyed with SECRET_KEY. Hex, so 64 characters.
    code_hash = Column(String(64), nullable=False)

    expires_at = Column(DateTime(timezone=True), nullable=False)
    # Set the moment a code is accepted. A non-null value is what makes a code
    # single-use -- checked on every verify, so a code that has already been
    # spent is rejected even while it is still inside its TTL.
    consumed_at = Column(DateTime(timezone=True), nullable=True)
    attempts = Column(Integer, default=0, nullable=False)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)

    __table_args__ = (
        # Every lookup in otp_repository is "the newest live code for this
        # (email, purpose)". Without this composite index that is a full scan of
        # a table which, by design, only ever grows -- expired rows are kept
        # until pruned so that "you already have a code, wait before asking for
        # another" stays enforceable.
        Index("ix_otp_codes_email_purpose_created", "email", "purpose", "created_at"),
    )
