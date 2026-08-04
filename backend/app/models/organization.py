"""
Organizations and their student rosters -- the tenancy boundary for exams.

THE PROBLEM THIS SOLVES
-----------------------
Before this existed, `list_published_exams` returned every published exam to
every student, and -- far worse -- `start_attempt` checked only status and
timing. Any authenticated student could sit any exam in the system just by
naming its id. Organization membership is the thing that makes "whose exam is
this?" answerable at all.

TWO TABLES, DELIBERATELY
------------------------
`Organization` is the tenant. An examiner belongs to exactly one, and every
exam they create inherits it.

`OrganizationMember` is a roster of *email addresses*, not a join table
between orgs and existing student rows. That distinction is the whole design:

  * Students self-register. An examiner needs to enrol a cohort before those
    accounts exist, so the roster has to key on something the examiner knows
    in advance -- an email address -- rather than on a student id.
  * A roster entry is therefore a standing invitation. When someone registers
    with a listed email, or when an examiner enrols an email that already has
    an account, the two are linked (see organization_service.link_student).
  * Keeping the row after linking preserves the audit trail: who was enrolled,
    by whom, and when, even if the student never signs up or is later removed.

`Student.organization_id` is the resolved, authoritative answer used on every
request. The roster decides membership; that column caches the outcome so the
hot path is one indexed integer comparison rather than an email join.
"""
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
    # Address comparison is case-insensitive in practice, and storing the raw
    # form would let "A@x.com" and "a@x.com" enrol as two different people.
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
    """Optional per-exam allow-list.

    An exam with no participant rows is open to its whole organization, which
    is the common case and stays a single click. Adding even one row flips the
    exam to allow-list mode: only the listed people may see or start it.

    Keyed on **email**, like OrganizationMember and for the same reason: an
    examiner sets up an exam before the cohort has signed up, so a list that
    could only reference existing student rows was unusable for exactly the
    case it was needed for -- you had to enrol everyone org-wide, wait for
    them all to register, and only then restrict the exam.

    `student_id` is a cache filled in once a matching account exists. It is
    NOT what authorises access -- can_student_access_exam matches on either
    the id or the email, so a pending invite works the moment its owner
    registers, with no resolution step needing to have run first.
    """

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
