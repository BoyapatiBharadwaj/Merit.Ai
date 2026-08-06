"""
Student profile — 1:1 extension of User for student-specific data.
"""
from sqlalchemy import Boolean, Column, Integer, String, ForeignKey, DateTime, func
from sqlalchemy.orm import relationship

from app.database.session import Base


class Student(Base):
    __tablename__ = "students"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False, index=True)
    roll_number = Column(String(50), unique=True, nullable=True)

    # Resolved organization membership -- the authoritative answer used on
    # every exam access check. NULL means "registered but not enrolled by any
    # examiner yet", which is a normal state for a fresh self-registration and
    # correctly grants access to nothing. Derived from the OrganizationMember
    # roster (see organization_service.link_student), cached here so the hot
    # path is one indexed integer comparison rather than an email join.
    organization_id = Column(Integer, ForeignKey("organizations.id", ondelete="SET NULL"),
                             nullable=True, index=True)

    # Identity-verification state. Face registration lives in FaceProfile
    # (1:1, below); the ID-card OCR result is recorded here because it is a
    # pass/fail fact about the student rather than a stored biometric.
    id_verified = Column(Boolean, default=False, nullable=False)
    id_verified_at = Column(DateTime(timezone=True), nullable=True)
    id_verified_name = Column(String(150), nullable=True)
    # The ID card photo submitted to /proctoring/id-card/verify, kept on disk
    # (most recent attempt wins) so staff can see what was actually
    # presented -- previously this image was decoded for OCR and discarded
    # in the same request, so nobody could review it afterwards. Saved
    # regardless of whether the name matched; see identity_service.
    id_card_image_path = Column(String(255), nullable=True)
    # Consent captured alongside the ID-card upload. Separate from the face
    # profile's own consent because the two are captured at different moments
    # and a candidate may complete one and abandon the other.
    id_consent_version = Column(String(50), nullable=True)
    id_consented_at = Column(DateTime(timezone=True), nullable=True)

    # Set once face + ID are both confirmed. From that point the student's
    # legal name and email are frozen -- otherwise a student could verify as
    # one person and then rename the account to sit an exam as someone else.
    identity_locked = Column(Boolean, default=False, nullable=False)
    identity_locked_at = Column(DateTime(timezone=True), nullable=True)

    # An administrator has asked this candidate to prove who they are again.
    #
    # Deliberately NOT the same thing as erasing their biometrics, which
    # already exists. The two answer different questions and destroying data is
    # the wrong tool for this one:
    #
    #   * Erasure is a privacy action. Someone asked for their data to be
    #     removed, and it goes.
    #   * Re-verification is an integrity action. Somebody looked at the
    #     enrolled face or ID and doubted it -- which is exactly when the
    #     existing photos must be KEPT, because they are the evidence that
    #     prompted the doubt and may be needed in a review. Wiping them first
    #     would destroy the record of what the candidate originally presented.
    #
    # So the stored face and ID stay on file until the candidate replaces them.
    # This flag is what closes the exam gate in the meantime.
    reverification_required_at = Column(DateTime(timezone=True), nullable=True)
    # Shown to the candidate verbatim. A re-verification demand with no stated
    # reason is indistinguishable, from the receiving end, from a malfunction.
    reverification_reason = Column(String(500), nullable=True)
    reverification_requested_by_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"),
                                            nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    user = relationship("User", back_populates="student_profile", foreign_keys=[user_id])
    # The administrator who asked for re-verification. Deliberately no
    # back_populates: an admin does not need a collection of "candidates I have
    # doubted" hanging off their user row, and adding one would load it on
    # every admin fetch.
    reverification_requested_by = relationship("User", foreign_keys=[reverification_requested_by_id])
    organization = relationship("Organization", back_populates="students")
    memberships = relationship("OrganizationMember", back_populates="student")
    exam_participations = relationship("ExamParticipant", back_populates="student",
                                       cascade="all, delete-orphan")
    face_profile = relationship("FaceProfile", back_populates="student", uselist=False, cascade="all, delete-orphan")
    attempts = relationship("StudentExamAttempt", back_populates="student", cascade="all, delete-orphan")
