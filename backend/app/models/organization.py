"""Organizations and their student rosters -- the tenancy boundary for exams."""
from sqlalchemy import (Boolean, Column, DateTime, ForeignKey, Integer, String, Text,
                        UniqueConstraint, func)
from sqlalchemy.orm import relationship

from app.database.session import Base


class Organization(Base):
    __tablename__ = "organizations"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(150), unique=True, nullable=False, index=True)
    # Free-text contact/description for the admin console. Not used in any
    # access decision.
    notes = Column(Text, nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    examiners = relationship("Examiner", back_populates="organization")
    students = relationship("Student", back_populates="organization")
    members = relationship("OrganizationMember", back_populates="organization",
                           cascade="all, delete-orphan")


class OrganizationMember(Base):
    """One enrolled student email within an organization."""

    __tablename__ = "organization_members"
    # An email may legitimately appear in several organizations (a visiting
    # examinee, a contractor), but never twice in the same one.
    __table_args__ = (UniqueConstraint("organization_id", "email", name="uq_org_member_email"),)

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id", ondelete="CASCADE"),
                             nullable=False, index=True)
    # Always stored lower-cased -- see organization_service.normalize_email.
    email = Column(String(150), nullable=False, index=True)

    # Set once a real account with this email exists. Null means the invitation
    # is outstanding, which is a normal, expected state -- not an error.
    student_id = Column(Integer, ForeignKey("students.id", ondelete="SET NULL"),
                        nullable=True, index=True)
    linked_at = Column(DateTime(timezone=True), nullable=True)

    invited_by_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    organization = relationship("Organization", back_populates="members")
    student = relationship("Student", back_populates="memberships")
    invited_by = relationship("User", foreign_keys=[invited_by_id])


class ExamParticipant(Base):
    """Optional per-exam allow-list."""

    __tablename__ = "exam_participants"
    __table_args__ = (UniqueConstraint("exam_id", "email", name="uq_exam_participant_email"),)

    id = Column(Integer, primary_key=True, index=True)
    exam_id = Column(Integer, ForeignKey("exams.id", ondelete="CASCADE"), nullable=False, index=True)
    # Always lower-cased; see organization_service.normalize_email.
    email = Column(String(150), nullable=False, index=True)
    student_id = Column(Integer, ForeignKey("students.id", ondelete="SET NULL"),
                        nullable=True, index=True)
    linked_at = Column(DateTime(timezone=True), nullable=True)
    added_by_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    exam = relationship("Exam", back_populates="participants")
    student = relationship("Student", back_populates="exam_participations")
