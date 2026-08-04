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
    # first/last are the authoritative fields captured at registration.
    # full_name is a denormalized cache of "first last" kept in sync by
    # set_name() -- it stays a real column because the ID-card OCR matcher,
    # PDF certificates, and every existing read path use it directly, and
    # because a stored column can be indexed/searched later if needed.
    first_name = Column(String(75), nullable=False, default="")
    last_name = Column(String(75), nullable=False, default="")
    full_name = Column(String(150), nullable=False)
    email = Column(String(150), unique=True, nullable=False, index=True)
    hashed_password = Column(String(255), nullable=False)
    role_id = Column(Integer, ForeignKey("roles.id", ondelete="RESTRICT"), nullable=False, index=True)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    role = relationship("Role", back_populates="users")
    student_profile = relationship("Student", back_populates="user", uselist=False, cascade="all, delete-orphan")
    examiner_profile = relationship("Examiner", back_populates="user", uselist=False, cascade="all, delete-orphan", foreign_keys="Examiner.user_id")

    def set_name(self, first_name: str, last_name: str) -> None:
        """Single place that writes a name, so full_name can never drift out
        of sync with first_name/last_name."""
        self.first_name = (first_name or "").strip()
        self.last_name = (last_name or "").strip()
        self.full_name = f"{self.first_name} {self.last_name}".strip()

