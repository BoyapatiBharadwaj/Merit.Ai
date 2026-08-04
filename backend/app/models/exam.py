"""
Exam and Section tables. An Exam belongs to one Examiner and contains
one or more Sections, which group Questions.
"""
from sqlalchemy import Column, Integer, String, Text, ForeignKey, DateTime, Boolean, func
from sqlalchemy.orm import relationship

from app.database.session import Base
from app.models.enums import ExamStatus, db_enum


class Exam(Base):
    __tablename__ = "exams"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(200), nullable=False)
    description = Column(Text, nullable=True)
    # Longer-form, optional guidance shown to candidates before they begin --
    # distinct from `description` (a short summary shown on the exam card).
    # Separate column rather than overloading description so an examiner can
    # have a short card blurb and detailed candidate instructions
    # independently, matching how professional assessment platforms
    # (HackerRank, Mettl) separate the two.
    instructions = Column(Text, nullable=True)
    examiner_id = Column(Integer, ForeignKey("examiners.id", ondelete="CASCADE"), nullable=False, index=True)
    # Denormalized from the owning examiner at creation time, deliberately.
    # Access checks and the student exam list filter on this constantly, and
    # reaching through examiner_id would put a join on the hottest query in
    # the app. It also freezes tenancy at creation: moving an examiner between
    # organizations must not silently relocate exams students have already
    # sat. exam_service.create_exam is the only writer.
    organization_id = Column(Integer, ForeignKey("organizations.id", ondelete="RESTRICT"),
                             nullable=True, index=True)
    duration_minutes = Column(Integer, nullable=False)
    # Minimum percentage to be marked "Passed" on the result/report. A plain
    # column (not derived) because it is a policy choice the examiner makes
    # per exam, not something computable from the questions themselves.
    pass_percentage = Column(Integer, default=40, nullable=False)
    status = Column(db_enum(ExamStatus), default=ExamStatus.DRAFT, nullable=False, index=True)
    randomize_questions = Column(Boolean, default=True, nullable=False)
    # Independent of randomize_questions: shuffles each MCQ/multi_select
    # question's own option order, per attempt (see
    # attempt_service.start_attempt / get_option_order), so candidates
    # sitting side by side can't just copy "the answer in position B".
    # Grading is always by option id, never by position, so this has no
    # effect on correctness -- only on-screen order.
    randomize_options = Column(Boolean, default=True, nullable=False)
    proctoring_enabled = Column(Boolean, default=True, nullable=False)
    start_time = Column(DateTime(timezone=True), nullable=True)
    end_time = Column(DateTime(timezone=True), nullable=True)

    # Address the examiner nominates to be told about this exam: once when it
    # is published, and again shortly before it starts. Free-form and optional
    # rather than a foreign key to users, because the useful recipient is
    # usually not a Merit.Ai account -- a department mailing list, an invigilator
    # rota, the examiner's own work address.
    notify_email = Column(String(150), nullable=True)
    # Stamped by reminder_service the moment the pre-exam reminder is sent.
    #
    # This is what makes the reminder exactly-once rather than every-poll. The
    # scheduler looks for exams inside a time window and runs on a short
    # interval, so without a durable marker the same exam matches on every pass
    # and the recipient gets a reminder a minute until the exam starts. Stored
    # on the row rather than in memory so a restart mid-window doesn't resend.
    reminder_sent_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    examiner = relationship("Examiner", back_populates="exams")
    organization = relationship("Organization")
    # Empty means "open to the whole organization" -- the common case. Any
    # rows present flip this exam into allow-list mode.
    participants = relationship("ExamParticipant", back_populates="exam",
                                cascade="all, delete-orphan")
    sections = relationship("Section", back_populates="exam", cascade="all, delete-orphan", order_by="Section.order_index")
    attempts = relationship("StudentExamAttempt", back_populates="exam", cascade="all, delete-orphan")


class Section(Base):
    __tablename__ = "sections"

    id = Column(Integer, primary_key=True, index=True)
    exam_id = Column(Integer, ForeignKey("exams.id", ondelete="CASCADE"), nullable=False, index=True)
    title = Column(String(150), nullable=False)
    order_index = Column(Integer, default=0, nullable=False)

    exam = relationship("Exam", back_populates="sections")
    questions = relationship("Question", back_populates="section", cascade="all, delete-orphan", order_by="Question.order_index")

