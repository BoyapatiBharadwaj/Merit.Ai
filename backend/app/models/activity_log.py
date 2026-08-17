"""Per-account activity trail: who did what to which account, and when."""
import enum

from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.orm import relationship

from app.database.session import Base
from app.models.enums import db_enum


class ActivityType(str, enum.Enum):
    """What happened."""
    SIGNED_UP = "signed_up"
    LOGGED_IN = "logged_in"
    LOGIN_FAILED = "login_failed"

    FACE_REGISTERED = "face_registered"
    ID_VERIFIED = "id_verified"
    ID_VERIFICATION_FAILED = "id_verification_failed"
    IDENTITY_LOCKED = "identity_locked"
    IDENTITY_UNLOCKED = "identity_unlocked"
    # An administrator asked a candidate to re-prove their identity.
    REVERIFICATION_REQUIRED = "reverification_required"
    REVERIFICATION_COMPLETED = "reverification_completed"
    BIOMETRICS_ERASED = "biometrics_erased"

    PASSWORD_CHANGED = "password_changed"
    PASSWORD_RESET_BY_ADMIN = "password_reset_by_admin"
    PASSWORD_RESET_BY_EMAIL = "password_reset_by_email"

    ACCOUNT_CREATED_BY_ADMIN = "account_created_by_admin"
    # Edits were the gap: creation, password resets and deletions were recorded,
    # but not changes -- so "who moved this examiner into our organization?" had
    # no answer, which is exactly the question a surprise tenancy change raises.
    EXAMINER_UPDATED = "examiner_updated"
    CANDIDATE_UPDATED = "candidate_updated"
    # An export is the moment a slice of this platform's data leaves it. "Who
    # took a copy of the candidate list, and when?" had no answer before.
    DATA_EXPORTED = "data_exported"
    ACCOUNT_DISABLED = "account_disabled"
    ACCOUNT_ENABLED = "account_enabled"
    ACCOUNT_DELETED = "account_deleted"
    PROFILE_UPDATED = "profile_updated"


class ActivityLog(Base):
    __tablename__ = "activity_logs"

    id = Column(Integer, primary_key=True, index=True)

    # Whose account this entry is ABOUT. This is the column the admin's
    # per-account timeline filters on.
    subject_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    # Who DID it. Usually the same as the subject (a student
    # logging in), but differs for anything administrative.
    actor_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)

    activity_type = Column(db_enum(ActivityType), nullable=False, index=True)

    # Free-text, human-readable, and safe to show verbatim in the admin UI.
    # Never templated from user input without the caller sanitising it first.
    description = Column(String(255), nullable=True)

    # Denormalised copies, kept ON PURPOSE. The FKs above go NULL when an account is deleted,
    # which is what preserves the row.
    subject_email = Column(String(150), nullable=True)
    actor_email = Column(String(150), nullable=True)

    ip_address = Column(String(45), nullable=True)  # 45 = INET6_ADDRSTRLEN
    # Ties an entry back to the request that produced it, using the same id
    # main.py already stamps on every response and log line.
    request_id = Column(String(32), nullable=True)

    # Optional structured detail (JSON text, matching this schema's existing
    # convention). Must never contain a secret -- see the module docstring.
    context_json = Column(Text, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)

    subject = relationship("User", foreign_keys=[subject_user_id])
    actor = relationship("User", foreign_keys=[actor_user_id])

    __table_args__ = (
        # The only query shape the admin UI issues: one account's timeline, newest first.
        Index("ix_activity_logs_subject_created", "subject_user_id", "created_at"),
    )
