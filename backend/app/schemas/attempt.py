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


class SaveAnswerRequest(BaseModel):
    question_id: int
    selected_option_id: int | None = None


class SaveMultiAnswerRequest(BaseModel):
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


class CodeAnswerRequest(BaseModel):
    question_id: int
    source_code: str = ""


class CodeRunRequest(BaseModel):
    question_id: int
    source_code: str = ""


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


class AttemptSummaryOut(BaseModel):
    attempt_id: int
    student_name: str
    exam_title: str
    status: str
    started_at: datetime
    submitted_at: datetime | None
    scored_marks: int | None = None
    total_marks: int | None = None


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
    correct_count: int
    incorrect_count: int
    unattempted_count: int
    questions: list[QuestionReportOut]