"""
Logs every AI-proctoring violation raised during an exam attempt.
"""
from sqlalchemy import Column, Integer, String, ForeignKey, DateTime, func
from sqlalchemy.orm import relationship

from app.database.session import Base
from app.models.enums import AdminDecision, EventType, Severity, db_enum


class ProctorEvent(Base):
    __tablename__ = "proctor_events"

    id = Column(Integer, primary_key=True, index=True)
    attempt_id = Column(Integer, ForeignKey("student_exam_attempts.id", ondelete="CASCADE"), nullable=False, index=True)
    event_type = Column(db_enum(EventType), nullable=False, index=True)
    severity = Column(db_enum(Severity), default=Severity.LOW, nullable=False, index=True)
    description = Column(String(255), nullable=True)
    screenshot_path = Column(String(255), nullable=True)
    # An admin's review verdict for the candidate exam report's violation
    # timeline -- see AdminDecision. Defaults to PENDING so every violation
    # starts out needing a look, rather than silently reading as "already
    # handled" the moment it's logged.
    admin_decision = Column(db_enum(AdminDecision), default=AdminDecision.PENDING, nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)

    attempt = relationship("StudentExamAttempt", back_populates="proctor_events")

