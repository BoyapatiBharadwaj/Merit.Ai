from datetime import datetime
from pydantic import BaseModel, Field


class StartAttemptResponse(BaseModel):
    attempt_id: int
    exam_title: str
    duration_minutes: int
    started_at: datetime
    remaining_seconds: int
    proctoring_enabled: bool
    question_ids_in_order: list[int]
    # Valid until this attempt's deadline plus a grace period, and accepted
    # only on this attempt's own endpoints -- so a three-hour exam cannot be
    # ended by a two-hour session token expiring. See
    # security.create_attempt_token.
    attempt_token: str


class AttemptStatusOut(BaseModel):
    """Authoritative attempt state, for the client's periodic re-sync.

    The exam page used to re-sync its timer by calling POST /attempts/start
    again and reading remaining_seconds off the response. That worked, but it
    meant the most frequent call in a live exam was the one endpoint that can
    also CREATE an attempt, and it returned the full question order every time
    to read one integer. This is the read-only version.
    """
    attempt_id: int
    status: str
    remaining_seconds: int
    submitted_at: datetime | None = None
    server_time: datetime


class _AutosaveEnvelope(BaseModel):
    """Optimistic-concurrency fields shared by every autosave request.

    Both are OPTIONAL. A client that omits them gets the old last-write-wins
    behaviour, which is what keeps a candidate already mid-exam on a cached
    bundle working through a server upgrade -- see
    attempt_repository._should_apply.

    `answer_version` is a per-question counter the CLIENT increments on every
    local change; the server refuses anything older than what it holds.
    `idempotency_key` makes a retry of a request that already landed a no-op
    rather than a second write.
    """
    answer_version: int | None = Field(default=None, ge=0)
    idempotency_key: str | None = Field(default=None, max_length=64)


class SaveAnswerRequest(_AutosaveEnvelope):
    question_id: int
    selected_option_id: int | None = None


class SaveMultiAnswerRequest(_AutosaveEnvelope):
    question_id: int
    selected_option_ids: list[int] = []


class AttemptCommentRequest(BaseModel):
    """PATCH /attempts/{id}/comment -- which of examiner_comment/admin_comment
    actually gets written is decided server-side from the caller's role, not
    from anything in this body (see attempt_service.set_attempt_comment)."""
    comment: str = Field(default="", max_length=2000)


class ResetAttemptRequest(BaseModel):
    """A reason is required, not optional -- it's the whole point of the
    audit trail (see AttemptReset); an examiner resetting a student's exam
    without recording why would defeat it."""
    reason: str = Field(min_length=3, max_length=500)


class CodeAnswerRequest(_AutosaveEnvelope):
    question_id: int
    source_code: str = ""


class CodeRunRequest(BaseModel):
    question_id: int
    source_code: str = ""


class FinalAnswerItem(_AutosaveEnvelope):
    """One answer in the snapshot sent with a submission.

    All three value fields are optional and mutually exclusive in practice --
    which one is read is decided by the question's own type on the server, not
    by which one the client happened to fill in, so a coding answer cannot be
    smuggled into an MCQ question. `None` means "no value for this question in
    this snapshot" and leaves whatever is already stored untouched, so a client
    that only tracks the questions the candidate actually touched does not blank
    out the rest.
    """
    question_id: int
    selected_option_id: int | None = None
    selected_option_ids: list[int] | None = None
    source_code: str | None = None


class FinalizeAttemptRequest(BaseModel):
    """POST /attempts/{id}/finalize.

    `final_answers` is the candidate's own current state, sent WITH the
    submission rather than raced against it -- see
    attempt_service.finalize_attempt for why that ordering was losing answers.

    There is deliberately no `auto` field. Whether this counts as a timeout or a
    deliberate submission is read from the server's clock; it used to be a query
    parameter, which let the candidate label their own submission either way.
    """
    final_answers: list[FinalAnswerItem] = Field(default_factory=list, max_length=1000)


class AttemptQuestionView(BaseModel):
    question_id: int
    text: str
    marks: int
    options: list[dict]
    selected_option_id: int | None = None


class ExamResultOut(BaseModel):
    attempt_id: int
    total_marks: int
    scored_marks: int
    percentage: float
    correct_count: int
    incorrect_count: int
    unattempted_count: int

    class Config:
        from_attributes = True



class QuestionReportOut(BaseModel):
    question_id: int
    question_type: str
    text: str
    marks: int
    marks_awarded: int
    options: list[dict] = []
    selected_option_id: int | None = None
    selected_option_ids: list[int] | None = None
    correct_option_ids: list[int] | None = None
    selected_answer: str | None = None
    correct_answer: str | None = None
    outcome: str  # "correct" | "incorrect" | "unattempted"
    explanation: str | None = None
    code_test_results: dict | None = None


class AttemptReportOut(BaseModel):
    """The comprehensive post-exam report: exam + candidate details, timing,
    pass/fail, and a full per-question breakdown. See
    attempt_service.build_full_report."""
    attempt_id: int
    exam_id: int
    exam_title: str
    exam_description: str | None
    candidate_name: str
    candidate_email: str
    roll_number: str | None
    duration_minutes: int
    started_at: datetime | None
    submitted_at: datetime | None
    time_taken_seconds: int | None
    status: str
    total_marks: int
    scored_marks: int
    percentage: float
    pass_percentage: int
    passed: bool | None
    answers_released: bool = True
    release_results_at: datetime | None = None
    correct_count: int
    incorrect_count: int
    unattempted_count: int
    questions: list[QuestionReportOut]