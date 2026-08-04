from pydantic import BaseModel


class ScoreBucket(BaseModel):
    range: str
    count: int


class ExamAnalyticsOut(BaseModel):
    total_attempts: int
    attempts_by_status: dict[str, int]
    completed_results: int
    average_percentage: float | None
    min_percentage: float | None
    max_percentage: float | None
    score_distribution: list[ScoreBucket]
    violations_by_type: dict[str, int]
    violations_by_severity: dict[str, int]


class PlatformAnalyticsOut(BaseModel):
    total_students: int
    total_examiners: int
    exams_by_status: dict[str, int]
    total_attempts: int
    attempts_by_status: dict[str, int]
    total_violations: int
    violations_by_type: dict[str, int]
    average_percentage: float | None = None
