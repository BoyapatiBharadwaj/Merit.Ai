from datetime import datetime, timedelta, timezone
from typing import Literal
from pydantic import BaseModel, EmailStr, Field, model_validator

# A start/end time within this window of "now" is treated as "now" rather than rejected.
SCHEDULE_GRACE = timedelta(seconds=30)


def _as_aware_utc(value: datetime) -> datetime:
    """Normalizes a datetime for comparison regardless of whether the client sent an offset."""
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _check_schedule_window(start_time: datetime | None, end_time: datetime | None, *, enforce_start_not_past: bool) -> None:
    """Shared cross-field schedule validation, used by both ExamCreate (a new exam, whose start."""
    now = datetime.now(timezone.utc)
    if start_time is not None and enforce_start_not_past:
        if _as_aware_utc(start_time) < now - SCHEDULE_GRACE:
            raise ValueError("Start date and time cannot be earlier than the current date and time.")
    if end_time is not None:
        effective_start = _as_aware_utc(start_time) if start_time is not None else now
        if _as_aware_utc(end_time) <= effective_start:
            raise ValueError("End date and time must be after the start date and time.")


class ExamCreate(BaseModel):
    title: str = Field(min_length=2, max_length=200)
    description: str | None = Field(default=None, max_length=5000)
    # Optional even here (not required at creation) so an examiner can start a draft with the
    # bare minimum and add candidate-facing guidance later via PUT /exams/{id}.
    instructions: str | None = Field(default=None, max_length=5000)
    duration_minutes: int = Field(ge=1, le=480)
    pass_percentage: int = Field(default=40, ge=0, le=100)
    randomize_questions: bool = True
    randomize_options: bool = True
    proctoring_enabled: bool = True
    # Per-exam requirements, settable at creation as well as on edit. None means
    # "follow proctoring_enabled" -- see Exam.requires().
    require_camera: bool | None = None
    require_microphone: bool | None = None
    require_screen_share: bool | None = None
    require_fullscreen: bool | None = None
    # When candidates may see the answer key. None = immediately, which is what
    # every exam did before this existed.
    release_results_at: datetime | None = None
    show_answers_on_release: bool = True
    # Whether a candidate ever sees their own score/pass-fail for
    # this exam, and if so, when -- see Exam.results_released.
    show_results: bool = True
    results_release_mode: Literal["immediate", "after_end_time"] = "immediate"
    start_time: datetime | None = None
    end_time: datetime | None = None
    # Optional address told when this exam is published and again shortly before it starts.
    notify_email: EmailStr | None = None

    @model_validator(mode="after")
    def validate_window(self):
        # A brand-new exam can never already be "started", so a start time
        # in the past is always invalid here, unlike ExamScheduleUpdate below.
        _check_schedule_window(self.start_time, self.end_time, enforce_start_not_past=True)
        return self


class ExamDetailsUpdate(BaseModel):
    """PUT /exams/{id}'s body -- every draft-editable exam-level setting EXCEPT the schedule,
    which is deliberately out of scope here and has its own endpoint (PATCH /{id}/schedule,
    see ExamScheduleUpdate) with different, state-dependent rules.
    """
    title: str = Field(min_length=2, max_length=200)
    description: str | None = Field(default=None, max_length=5000)
    instructions: str | None = Field(default=None, max_length=5000)
    duration_minutes: int = Field(ge=1, le=480)
    pass_percentage: int = Field(default=40, ge=0, le=100)
    randomize_questions: bool = True
    randomize_options: bool = True
    proctoring_enabled: bool = True
    # Per-exam requirements. None means "follow proctoring_enabled", so an exam
    # created before these existed behaves exactly as it did.
    require_camera: bool | None = None
    require_microphone: bool | None = None
    require_screen_share: bool | None = None
    require_fullscreen: bool | None = None
    # When candidates may see their marks. None = immediately, the old behaviour.
    release_results_at: datetime | None = None
    show_answers_on_release: bool = True
    show_results: bool = True
    results_release_mode: Literal["immediate", "after_end_time"] = "immediate"
    notify_email: EmailStr | None = None


class ExamScheduleUpdate(BaseModel):
    """PATCH /exams/{id}/schedule's body -- a full replace of both fields (not a partial patch),
    matching this codebase's existing convention for "edit" endpoints (see
    exam_repository.replace_question).
    """
    start_time: datetime | None = None
    end_time: datetime | None = None

    @model_validator(mode="after")
    def validate_window(self):
        _check_schedule_window(self.start_time, self.end_time, enforce_start_not_past=False)
        return self


class ExamOut(BaseModel):
    id: int
    title: str
    description: str | None
    instructions: str | None = None
    duration_minutes: int
    pass_percentage: int
    status: str
    randomize_questions: bool
    randomize_options: bool = True
    proctoring_enabled: bool
    start_time: datetime | None
    end_time: datetime | None
    release_results_at: datetime | None = None
    show_answers_on_release: bool = True
    show_results: bool = True
    results_release_mode: str = "immediate"
    notify_email: str | None = None

    class Config:
        from_attributes = True


class ExamDetailOut(ExamOut):
    sections: list["SectionOut"] = []


from app.schemas.question import SectionOut  # noqa: E402
ExamDetailOut.model_rebuild()