"""One-time passcodes for email verification and password reset."""
import enum

from sqlalchemy import Column, DateTime, Index, Integer, String, func

from app.database.session import Base
from app.models.enums import db_enum


class OtpPurpose(str, enum.Enum):
    """What an issued code entitles the holder to do."""
    SIGNUP = "signup"
    PASSWORD_RESET = "password_reset"
    # An account someone else created for you, which you have not set a password on yet.
    ACTIVATION = "activation"


class OtpCode(Base):
    __tablename__ = "otp_codes"

    id = Column(Integer, primary_key=True, index=True)

    email = Column(String(150), nullable=False, index=True)
    purpose = Column(db_enum(OtpPurpose), nullable=False)

    # HMAC-SHA256 of the code or token, keyed with SECRET_KEY. Hex, so 64
    # characters regardless of how long the secret itself is.
    code_hash = Column(String(64), nullable=False)

    expires_at = Column(DateTime(timezone=True), nullable=False)
    # Set the moment a code is accepted. A non-null value is what makes a code single-use.
    consumed_at = Column(DateTime(timezone=True), nullable=True)
    attempts = Column(Integer, default=0, nullable=False)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)

    __table_args__ = (
        # Every lookup in otp_repository is "the newest live code for this (email, purpose)".
        Index("ix_otp_codes_email_purpose_created", "email", "purpose", "created_at"),
    )
