"""
Per-account activity trail: who did what to which account, and when.

Deliberately separate from proctor_events. That table records what happened
*inside an exam* and is owned by the proctoring pipeline; this one records what
happened *to an account* — signing up, signing in, verifying identity, having a
password reset, being disabled or deleted. Merging them would put two very
different retention profiles, access rules and growth rates in one table.

Two rules this table exists under:

**It never stores a secret.** No passwords, no OTP codes, no tokens, no
embeddings. A password-change entry records that one happened and who did it,
never the value. An audit log is read by more people than the data it describes,
so it is the last place a secret should end up.

**Rows are append-only.** Nothing in the application updates or deletes an entry.
`actor_user_id` and `subject_user_id` both use ON DELETE SET NULL rather than
CASCADE for exactly this reason: deleting an account must not erase the record
that the account existed and was deleted — which is usually the single most
important line in the whole trail.
"""
import enum

from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.orm import relationship

from app.database.session import Base
from app.models.enums import db_enum


class ActivityType(str, enum.Enum):
    """What happened.

    Scoped to security and identity events. Exam conduct is already covered in
    far more detail by proctor_events, and duplicating it here would bury the
    handful of lines an administrator actually needs under thousands of frames.
    """
    SIGNED_UP = "signed_up"
    LOGGED_IN = "logged_in"
    LOGIN_FAILED = "login_failed"

    FACE_REGISTERED = "face_registered"
    ID_VERIFIED = "id_verified"
    ID_VERIFICATION_FAILED = "id_verification_failed"
    IDENTITY_LOCKED = "identity_locked"
    IDENTITY_UNLOCKED = "identity_unlocked"
    BIOMETRICS_ERASED = "biometrics_erased"

    PASSWORD_CHANGED = "password_changed"
    PASSWORD_RESET_BY_ADMIN = "password_reset_by_admin"
    PASSWORD_RESET_BY_EMAIL = "password_reset_by_email"

    ACCOUNT_CREATED_BY_ADMIN = "account_created_by_admin"
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
    # Who DID it. Usually the same as the subject (a student logging in), but
    # differs for anything administrative -- an admin resetting a password,
    # disabling an account, unlocking an identity. Keeping them apart is what
    # makes "who reset this student's password?" answerable at all.
    actor_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)

    activity_type = Column(db_enum(ActivityType), nullable=False, index=True)

    # Free-text, human-readable, and safe to show verbatim in the admin UI.
    # Never templated from user input without the caller sanitising it first.
    description = Column(String(255), nullable=True)

    # Denormalised copies, kept ON PURPOSE.
    #
    # The FKs above go NULL when an account is deleted, which is what preserves
    # the row -- but a timeline of nulls is useless. Recording the email and role
    # as they were at the time means an admin can still read "who was this?"
    # after the account is gone, which is precisely when they most need to.
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
        # The only query shape the admin UI issues: one account's timeline,
        # newest first. A plain index on subject_user_id would still leave the
        # sort to be done in memory.
        Index("ix_activity_logs_subject_created", "subject_user_id", "created_at"),
    )
