"""
Examiner profile — 1:1 extension of User, created by Admin.
"""
from sqlalchemy import Column, Integer, String, ForeignKey, DateTime, func
from sqlalchemy.orm import relationship

from app.database.session import Base


class Examiner(Base):
    __tablename__ = "examiners"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False, index=True)
    # Kept as the human-facing label the examiner typed at sign-up,
    # and as the source the 0008 migration derived organizations
    # from. organization_id below is the authoritative tenancy link.
    organization_name = Column(String(150), nullable=True)
    organization_id = Column(Integer, ForeignKey("organizations.id", ondelete="RESTRICT"),
                             nullable=True, index=True)
    created_by_admin_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    user = relationship("User", back_populates="examiner_profile", foreign_keys=[user_id])
    organization = relationship("Organization", back_populates="examiners")
    exams = relationship("Exam", back_populates="examiner", cascade="all, delete-orphan")
