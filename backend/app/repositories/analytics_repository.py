"""Aggregation queries backing the examiner/admin analytics dashboards."""
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.attempt import ExamResult, StudentExamAttempt
from app.models.exam import Exam
from app.models.examiner import Examiner
from app.models.proctor_event import ProctorEvent
from app.models.student import Student


def _unwrap(value):
    """Enum columns round-trip as Python enum members; plain strings pass through."""
    return value.value if hasattr(value, "value") else value


def exam_attempt_status_counts(db: Session, exam_id: int) -> dict[str, int]:
    rows = (
        db.query(StudentExamAttempt.status, func.count(StudentExamAttempt.id))
        .filter(StudentExamAttempt.exam_id == exam_id)
        .group_by(StudentExamAttempt.status)
        .all()
    )
    return {_unwrap(status): count for status, count in rows}


def exam_score_stats(db: Session, exam_id: int):
    return (
        db.query(
            func.avg(ExamResult.percentage), func.min(ExamResult.percentage),
            func.max(ExamResult.percentage), func.count(ExamResult.id),
        )
        .join(StudentExamAttempt, ExamResult.attempt_id == StudentExamAttempt.id)
        .filter(StudentExamAttempt.exam_id == exam_id)
        .first()
    )


def exam_result_percentages(db: Session, exam_id: int) -> list[float]:
    """Raw percentages so the service layer can bucket them in Python -- keeps
    the bucketing portable across SQLite (tests) and Postgres (prod)."""
    rows = (
        db.query(ExamResult.percentage)
        .join(StudentExamAttempt, ExamResult.attempt_id == StudentExamAttempt.id)
        .filter(StudentExamAttempt.exam_id == exam_id)
        .all()
    )
    return [row[0] for row in rows]


def exam_violation_counts_by_type(db: Session, exam_id: int) -> dict[str, int]:
    rows = (
        db.query(ProctorEvent.event_type, func.count(ProctorEvent.id))
        .join(StudentExamAttempt, ProctorEvent.attempt_id == StudentExamAttempt.id)
        .filter(StudentExamAttempt.exam_id == exam_id)
        .group_by(ProctorEvent.event_type)
        .all()
    )
    return {_unwrap(event_type): count for event_type, count in rows}


def exam_violation_counts_by_severity(db: Session, exam_id: int) -> dict[str, int]:
    rows = (
        db.query(ProctorEvent.severity, func.count(ProctorEvent.id))
        .join(StudentExamAttempt, ProctorEvent.attempt_id == StudentExamAttempt.id)
        .filter(StudentExamAttempt.exam_id == exam_id)
        .group_by(ProctorEvent.severity)
        .all()
    )
    return {_unwrap(severity): count for severity, count in rows}


def platform_counts(db: Session) -> dict:
    total_students = db.query(func.count(Student.id)).scalar() or 0
    total_examiners = db.query(func.count(Examiner.id)).scalar() or 0
    exams_by_status = {_unwrap(status): count for status, count in db.query(Exam.status, func.count(Exam.id)).group_by(Exam.status).all()}
    total_attempts = db.query(func.count(StudentExamAttempt.id)).scalar() or 0
    attempts_by_status = {_unwrap(status): count for status, count in db.query(StudentExamAttempt.status, func.count(StudentExamAttempt.id)).group_by(StudentExamAttempt.status).all()}
    total_violations = db.query(func.count(ProctorEvent.id)).scalar() or 0
    violations_by_type = {_unwrap(event_type): count for event_type, count in db.query(ProctorEvent.event_type, func.count(ProctorEvent.id)).group_by(ProctorEvent.event_type).all()}
    average_percentage = db.query(func.avg(ExamResult.percentage)).scalar()
    return {
        "total_students": total_students,
        "total_examiners": total_examiners,
        "exams_by_status": exams_by_status,
        "total_attempts": total_attempts,
        "attempts_by_status": attempts_by_status,
        "total_violations": total_violations,
        "violations_by_type": violations_by_type,
        "average_percentage": round(average_percentage, 2) if average_percentage is not None else None,
    }
