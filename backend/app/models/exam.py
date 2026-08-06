"""
Exam and Section tables. An Exam belongs to one Examiner and contains
one or more Sections, which group Questions.
"""
from datetime import datetime, timezone

from sqlalchemy import Column, Integer, String, Text, ForeignKey, DateTime, Boolean, func
from sqlalchemy.orm import relationship

from app.database.session import Base
from app.models.enums import ExamStatus, ResultsReleaseMode, db_enum


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

    # Individual requirements, rather than one boolean deciding everything.
    #
    # proctoring_enabled gated the AI signals, but the exam page demanded
    # camera, microphone, screen sharing AND fullscreen regardless -- and then
    # told the candidate "this exam is not proctored". So an unproctored quiz
    # still required someone to hand over their webcam and share their screen
    # for no purpose anyone could name, which is both a privacy intrusion and a
    # reason for a candidate to distrust everything else the page says.
    #
    # NULL means "follow proctoring_enabled", so every existing exam keeps its
    # current behaviour and an examiner only sets these when they want something
    # different. require_* below resolve that.
    require_camera = Column(Boolean, nullable=True)
    require_microphone = Column(Boolean, nullable=True)
    require_screen_share = Column(Boolean, nullable=True)
    require_fullscreen = Column(Boolean, nullable=True)

    # When candidates may see their marks, and how much of the paper.
    #
    # The results API returned correct options, correct-answer text and
    # explanations the moment an attempt was submitted -- so the first candidate
    # to finish held the complete answer key while everyone else was still
    # writing, and could simply send it to them.
    release_results_at = Column(DateTime(timezone=True), nullable=True)
    show_answers_on_release = Column(Boolean, default=True, nullable=False)
    # Whether a candidate ever sees their own score/pass-fail at all. False
    # means never -- staff (the owning examiner, or an admin) can always see
    # it; only the candidate's own view is affected. Defaults to True so
    # every exam created before this existed keeps showing results exactly
    # as it always has.
    show_results = Column(Boolean, default=True, nullable=False)
    # Only meaningful when show_results is True -- see ResultsReleaseMode
    # and Exam.results_released.
    results_release_mode = Column(db_enum(ResultsReleaseMode), default=ResultsReleaseMode.IMMEDIATE, nullable=False)
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

    def requires(self, capability: str) -> bool:
        """Whether this exam needs camera / microphone / screen_share / fullscreen.

        One place that resolves the per-exam override against the old boolean,
        so the exam page, the pre-flight check and the server cannot disagree
        about what a candidate is being asked for.
        """
        override = getattr(self, f"require_{capability}", None)
        return self.proctoring_enabled if override is None else bool(override)

    @property
    def answer_key_released(self) -> bool:
        """Whether the answer KEY (correct options, correct-answer text,
        explanations) may be shown yet -- release_results_at alone, exactly
        the original behaviour. Deliberately independent of results_released
        below: an examiner can show a candidate their score immediately while
        still holding back which specific answers were correct until
        release_results_at, or (per results_released) withhold the score
        itself entirely regardless of this setting.
        """
        if self.release_results_at is None:
            return True
        moment = self.release_results_at
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) >= moment

    @property
    def results_released(self) -> bool:
        """Whether a candidate may see their own score/pass-fail outcome at
        all yet -- show_results and results_release_mode (see
        ResultsReleaseMode), unrelated to release_results_at/
        answer_key_released above.

        show_results=False is absolute: never, for anyone sitting this exam,
        however long they wait. Otherwise: AFTER_END_TIME waits for the
        exam's own end_time (so the first candidate to finish can never hand
        around their result while others are still writing) and treats no
        end_time as "not yet, since there is no line to wait for"; IMMEDIATE
        (the default) releases the score as soon as that candidate submits,
        which combined with show_results defaulting True is the original
        behaviour for every exam created before either setting existed.
        """
        if not self.show_results:
            return False
        if self.results_release_mode == ResultsReleaseMode.AFTER_END_TIME:
            if self.end_time is None:
                return False
            moment = self.end_time
            if moment.tzinfo is None:
                moment = moment.replace(tzinfo=timezone.utc)
            return datetime.now(timezone.utc) >= moment
        return True

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

