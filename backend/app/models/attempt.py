"""
Tracks a student's attempt at an exam, their per-question answers,
and the final computed result.
"""
from sqlalchemy import Column, Integer, String, Text, ForeignKey, DateTime, Float, UniqueConstraint, func
from sqlalchemy.orm import relationship

from app.database.session import Base
from app.models.enums import AttemptStatus, db_enum


class StudentExamAttempt(Base):
    __tablename__ = "student_exam_attempts"
    __table_args__ = (UniqueConstraint("student_id", "exam_id", name="uq_student_exam"),)

    id = Column(Integer, primary_key=True, index=True)
    student_id = Column(Integer, ForeignKey("students.id", ondelete="CASCADE"), nullable=False, index=True)
    exam_id = Column(Integer, ForeignKey("exams.id", ondelete="CASCADE"), nullable=False, index=True)
    status = Column(db_enum(AttemptStatus), default=AttemptStatus.IN_PROGRESS, nullable=False, index=True)
    started_at = Column(DateTime(timezone=True), server_default=func.now())
    submitted_at = Column(DateTime(timezone=True), nullable=True)
    question_order = Column(String, nullable=True)  # comma-separated shuffled question IDs for this attempt
    # JSON {question_id: [option_id, ...]} -- each MCQ/multi_select
    # question's own option display order for this attempt, fixed once at
    # start_attempt (see attempt_service._build_option_order) when the exam
    # has randomize_options enabled. Null questions/exams fall back to the
    # options' natural (authored) order. Display-only: grading always keys
    # on option id, never position, so this has no effect on scoring.
    option_order_json = Column(Text, nullable=True)
    # Free-text notes for the admin drill-down's candidate exam report.
    # Deliberately two separate columns rather than one shared thread: each
    # role annotates independently (the examiner explaining what they saw
    # while proctoring live, the admin recording a review decision), and
    # neither should silently overwrite the other's note. See
    # attempt_service.set_attempt_comment for who may write which one.
    examiner_comment = Column(Text, nullable=True)
    admin_comment = Column(Text, nullable=True)

    student = relationship("Student", back_populates="attempts")
    exam = relationship("Exam", back_populates="attempts")
    answers = relationship("StudentAnswer", back_populates="attempt", cascade="all, delete-orphan")
    result = relationship("ExamResult", back_populates="attempt", uselist=False, cascade="all, delete-orphan")
    proctor_events = relationship("ProctorEvent", back_populates="attempt", cascade="all, delete-orphan")


class StudentAnswer(Base):
    __tablename__ = "student_answers"
    __table_args__ = (UniqueConstraint("attempt_id", "question_id", name="uq_attempt_question"),)

    id = Column(Integer, primary_key=True, index=True)
    attempt_id = Column(Integer, ForeignKey("student_exam_attempts.id", ondelete="CASCADE"), nullable=False, index=True)
    question_id = Column(Integer, ForeignKey("questions.id", ondelete="CASCADE"), nullable=False, index=True)
    selected_option_id = Column(Integer, ForeignKey("options.id", ondelete="SET NULL"), nullable=True)
    # JSON list of option ids, for MULTI_SELECT questions only -- a single FK
    # column can't represent "zero or more" options, and this app's existing
    # convention for that shape is a JSON-in-Text column (see
    # Question.test_cases_json) rather than a join table. selected_option_id
    # above stays single-valued and is simply unused for this question type.
    selected_option_ids_json = Column(Text, nullable=True)
    code_submission = Column(Text, nullable=True)  # student's source code, for coding questions
    code_test_results_json = Column(Text, nullable=True)  # JSON list of per-test-case results, computed at submit time
    answered_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    attempt = relationship("StudentExamAttempt", back_populates="answers")
    question = relationship("Question", back_populates="answers")
    selected_option = relationship("Option")


class ExamResult(Base):
    __tablename__ = "exam_results"

    id = Column(Integer, primary_key=True, index=True)
    attempt_id = Column(Integer, ForeignKey("student_exam_attempts.id", ondelete="CASCADE"), unique=True, nullable=False, index=True)
    total_marks = Column(Integer, nullable=False)
    scored_marks = Column(Integer, nullable=False)
    percentage = Column(Float, nullable=False)
    correct_count = Column(Integer, nullable=False)
    incorrect_count = Column(Integer, nullable=False)
    unattempted_count = Column(Integer, nullable=False)
    generated_at = Column(DateTime(timezone=True), server_default=func.now())

    attempt = relationship("StudentExamAttempt", back_populates="result")


class AttemptReset(Base):
    """Audit trail for attempt_service.reset_student_attempt.

    Resetting an attempt (e.g. after a browser crash or a proctoring
    interruption) deletes the StudentExamAttempt row outright -- the
    (student_id, exam_id) unique constraint on that table means a fresh
    retake can only exist once the old row is gone, not alongside it -- so
    the details worth keeping (who reset it, when, why, and what the attempt
    looked like beforehand) are snapshotted here first as plain historical
    columns rather than live foreign keys into a row that is about to stop
    existing.
    """
    __tablename__ = "attempt_resets"

    id = Column(Integer, primary_key=True, index=True)
    exam_id = Column(Integer, ForeignKey("exams.id", ondelete="CASCADE"), nullable=False, index=True)
    student_id = Column(Integer, ForeignKey("students.id", ondelete="CASCADE"), nullable=False, index=True)
    # SET NULL (not CASCADE): if the examiner account is ever deleted, the
    # historical fact "this attempt was reset, for this reason, at this
    # time" should still stand -- only the "by whom" attribution goes blank.
    examiner_id = Column(Integer, ForeignKey("examiners.id", ondelete="SET NULL"), nullable=True)
    previous_attempt_id = Column(Integer, nullable=False)  # historical reference only -- that row is deleted as part of the reset
    previous_status = Column(String(30), nullable=False)
    reason = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    exam = relationship("Exam")
    student = relationship("Student")
    examiner = relationship("Examiner")

