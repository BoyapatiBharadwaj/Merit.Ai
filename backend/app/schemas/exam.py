from datetime import datetime, timedelta, timezone
from pydantic import BaseModel, EmailStr, Field, model_validator

# A start/end time within this window of "now" is treated as "now" rather
# than rejected -- a form submitted the instant a user clicks "now" would
# otherwise flip from valid to invalid purely from network/render latency
# between when they picked the time and when the server validates it.
SCHEDULE_GRACE = timedelta(seconds=30)


def _as_aware_utc(value: datetime) -> datetime:
    """Normalizes a datetime for comparison regardless of whether the client
    sent an offset. In practice the frontend always sends timezone-aware ISO
    strings (JS's `Date#toISOString()`), but defends against a naive
    datetime (no offset) being treated as "already in the past" or blowing up
    a comparison against an aware one -- one of the timezone edge cases this
    feature is required to handle correctly."""
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _check_schedule_window(start_time: datetime | None, end_time: datetime | None, *, enforce_start_not_past: bool) -> None:
    """Shared cross-field schedule validation, used by both ExamCreate (a new
    exam, whose start -- if any -- can never be in the past) and
    ExamScheduleUpdate (an edit to an existing exam's schedule, where "start
    can't be in the past" only applies if the exam hasn't started yet -- a
    check that needs DB state, so it's applied by exam_service instead and
    deliberately skipped here via enforce_start_not_past=False)."""
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
    # Optional even here (not required at creation) so an examiner can start
    # a draft with the bare minimum and add candidate-facing guidance later
    # via PUT /exams/{id} -- see ExamDetailsUpdate below.
    instructions: str | None = Field(default=None, max_length=5000)
    duration_minutes: int = Field(ge=1, le=480)
    pass_percentage: int = Field(default=40, ge=0, le=100)
    randomize_questions: bool = True
    randomize_options: bool = True
    proctoring_enabled: bool = True
    start_time: datetime | None = None
    end_time: datetime | None = None
    # Optional address told when this exam is published and again shortly
    # before it starts. Validated as an email so a typo is a 422 at creation
    # rather than a silent non-delivery hours later.
    notify_email: EmailStr | None = None

    @model_validator(mode="after")
    def validate_window(self):
        # A brand-new exam can never already be "started", so a start time
        # in the past is always invalid here, unlike ExamScheduleUpdate below.
        _check_schedule_window(self.start_time, self.end_time, enforce_start_not_past=True)
        return self


class ExamDetailsUpdate(BaseModel):
    """PUT /exams/{id}'s body -- every draft-editable exam-level setting
    EXCEPT the schedule, which is deliberately out of scope here and has its
    own endpoint (PATCH /{id}/schedule, see ExamScheduleUpdate) with
    different, state-dependent rules. Keeping the two separate means saving
    "just the pass percentage" can never accidentally wipe or re-validate
    the stored start/end time -- a full-replace body that omitted them, or
    resent an already-past one, would otherwise either null them out or
    fail validation for a save that had nothing to do with scheduling.

    Draft-only (see exam_service.update_exam_details / _get_editable_exam):
    once published, every one of these settings is frozen.
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
    # When candidates may see their marks. None = immediately, the old
    # behaviour. Until then the answer key is redacted -- see
    # attempt_service.build_full_report.
    release_results_at: datetime | None = None
    show_answers_on_release: bool = True
    notify_email: EmailStr | None = None


class ExamScheduleUpdate(BaseModel):
    """PATCH /exams/{id}/schedule's body -- a full replace of both fields
    (not a partial patch), matching this codebase's existing convention for
    "edit" endpoints (see exam_repository.replace_question). Either field may
    be null (an open-ended start or end). Whether `start_time` may actually
    be changed from its current value is a state-dependent question the
    schema can't answer on its own (it doesn't know the exam's current
    status) -- that check lives in exam_service.update_exam_schedule."""
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
    notify_email: str | None = None

    class Config:
        from_attributes = True


class ExamDetailOut(ExamOut):
    sections: list["SectionOut"] = []


from app.schemas.question import SectionOut  # noqa: E402
ExamDetailOut.model_rebuild()