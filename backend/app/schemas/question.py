from pydantic import BaseModel, Field, model_validator


class OptionCreate(BaseModel):
    text: str = Field(min_length=1, max_length=500)
    is_correct: bool = False


class OptionOut(BaseModel):
    id: int
    text: str
    is_correct: bool | None = None

    class Config:
        from_attributes = True


class TestCaseIn(BaseModel):
    input: str = Field(default="", max_length=10000)
    expected_output: str = Field(max_length=10000)
    is_sample: bool = False  # sample cases are shown to the student; hidden ones are only used for grading


class QuestionCreate(BaseModel):
    text: str = Field(min_length=1, max_length=5000)
    marks: int = Field(default=1, ge=1, le=100)
    order_index: int = Field(default=0, ge=0)
    question_type: str = Field(default="mcq", pattern="^(mcq|multi_select|coding)$")

    # MCQ / multi_select
    options: list[OptionCreate] = Field(default_factory=list)

    # Coding-only
    language: str | None = None
    starter_code: str | None = Field(default=None, max_length=20000)
    test_cases: list[TestCaseIn] = Field(default_factory=list)
    time_limit_seconds: int = Field(default=6, ge=1, le=20)

    # Shown only on the post-exam report -- optional for every question type.
    explanation: str | None = Field(default=None, max_length=5000)

    @model_validator(mode="after")
    def validate_by_type(self):
        if self.question_type in ("mcq", "multi_select"):
            if len(self.options) < 2:
                raise ValueError("This question needs at least two options.")
        else:
            if self.language not in ("python", "javascript"):
                raise ValueError("A coding question needs language 'python' or 'javascript'.")
            if not self.test_cases:
                raise ValueError("A coding question needs at least one test case.")
            if not any(case.is_sample for case in self.test_cases):
                raise ValueError("A coding question needs at least one sample test case visible to students.")
        return self


class QuestionOut(BaseModel):
    """Examiner/admin-facing view -- includes every test case (sample and
    hidden), since the examiner is the one who authored them. The
    student-facing question view (attempts.py's get_question_for_attempt)
    is built separately and only ever exposes sample test cases."""
    id: int
    text: str
    marks: int
    order_index: int
    question_type: str
    options: list[OptionOut]
    language: str | None = None
    starter_code: str | None = None
    time_limit_seconds: int | None = None
    test_cases: list[dict] = []
    explanation: str | None = None

    class Config:
        from_attributes = True


class SectionCreate(BaseModel):
    title: str = Field(min_length=1, max_length=150)
    order_index: int = Field(default=0, ge=0)


class SectionUpdate(BaseModel):
    """Rename a section. Only the title is editable -- order_index is owned by
    the reorder endpoints, and a section's exam can never be reassigned."""
    title: str = Field(min_length=1, max_length=150)


class QuestionReorderRequest(BaseModel):
    """Ordered list of every question ID in a section -- position in the
    list becomes the new order_index. Powers drag-and-drop reordering in the
    Examiner Dashboard's question builder."""
    question_ids: list[int] = Field(min_length=1)


class SectionOut(BaseModel):
    id: int
    title: str
    order_index: int
    questions: list[QuestionOut] = []

    class Config:
        from_attributes = True