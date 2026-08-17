"""Stores the face-identity embedding captured during the student's
face-registration step (ArcFace, via the AI worker or the local
in-process ArcFace model), used later for live matching.
"""
from sqlalchemy import Column, Integer, String, ForeignKey, DateTime, Text, func
from sqlalchemy.orm import relationship

from app.database.session import Base


class FaceProfile(Base):
    __tablename__ = "face_profiles"

    id = Column(Integer, primary_key=True, index=True)
    student_id = Column(Integer, ForeignKey("students.id", ondelete="CASCADE"), unique=True, nullable=False, index=True)
    image_path = Column(String(255), nullable=False)
    # JSON-encoded 512-d L2-normalised ArcFace embedding. Same format from both the ai_worker
    # and the in-process model, compared with cosine distance.
    encoding = Column(Text, nullable=False)
    registered_at = Column(DateTime(timezone=True), server_default=func.now())

    # Consent recorded at capture time.
    consent_version = Column(String(50), nullable=True)
    consented_at = Column(DateTime(timezone=True), nullable=True)

    # Set when the embedding and image are erased (see biometric_service).
    deleted_at = Column(DateTime(timezone=True), nullable=True)
    deletion_reason = Column(String(100), nullable=True)

    student = relationship("Student", back_populates="face_profile")
