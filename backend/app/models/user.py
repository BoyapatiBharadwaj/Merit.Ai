"""
Core user + role tables. Students / Examiners extend this with a 1-1 profile table.
"""
from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, func
from sqlalchemy.orm import relationship

from app.database.session import Base


class Role(Base):
    __tablename__ = "roles"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(20), unique=True, nullable=False, index=True)  # admin/examiner/student

    users = relationship("User", back_populates="role")


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    # first/last are the authoritative fields captured at registration. full_name is a
    # denormalized cache of "first last" kept in sync by set_name().
    first_name = Column(String(75), nullable=False, default="")
    last_name = Column(String(75), nullable=False, default="")
    full_name = Column(String(150), nullable=False)
    email = Column(String(150), unique=True, nullable=False, index=True)
    hashed_password = Column(String(255), nullable=False)
    role_id = Column(Integer, ForeignKey("roles.id", ondelete="RESTRICT"), nullable=False, index=True)
    is_active = Column(Boolean, default=True, nullable=False)

    # When this address was proved to belong to whoever
    # holds the account, by entering a code mailed to it.
    email_verified_at = Column(DateTime(timezone=True), nullable=True)

    # What the person agreed to, and which version of it.
    terms_accepted_at = Column(DateTime(timezone=True), nullable=True)
    terms_version = Column(String(20), nullable=True)
    proctoring_consent_at = Column(DateTime(timezone=True), nullable=True)

    # The moment the password last changed. Stamped into every token issued afterwards;
    # app/api/deps.py rejects any token minted before it.
    password_changed_at = Column(DateTime(timezone=True), nullable=True)

    # True while the account holds a password its owner never chose.
    must_change_password = Column(Boolean, default=False, nullable=False)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    role = relationship("Role", back_populates="users")
    # foreign_keys is required, not optional: students now hold a second FK
    # into users (reverification_requested_by_id, the admin who asked), so
    # "the student profile belonging to this user" is genuinely ambiguous
    # without it -- exactly as it already was for Examiner.user_id below.
    student_profile = relationship("Student", back_populates="user", uselist=False,
                                   cascade="all, delete-orphan", foreign_keys="Student.user_id")
    examiner_profile = relationship("Examiner", back_populates="user", uselist=False, cascade="all, delete-orphan", foreign_keys="Examiner.user_id")

    def set_name(self, first_name: str, last_name: str) -> None:
        """Single place that writes a name, so full_name can never drift out
        of sync with first_name/last_name."""
        self.first_name = (first_name or "").strip()
        self.last_name = (last_name or "").strip()
        self.full_name = f"{self.first_name} {self.last_name}".strip()

    def set_email(self, email: str) -> None:
        """Single place that writes an email, so verification cannot survive it."""
        new_email = (email or "").strip().lower()
        if new_email != (self.email or "").lower():
            self.email_verified_at = None
        self.email = new_email

    @property
    def is_email_verified(self) -> bool:
        return self.email_verified_at is not None

