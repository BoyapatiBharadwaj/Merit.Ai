"""
Data access for ProctorEvent and FaceProfile tables.
"""
from sqlalchemy.orm import Session, joinedload
from app.models.proctor_event import ProctorEvent
from app.models.face_profile import FaceProfile


def create_event(db: Session, attempt_id: int, event_type: str, severity: str,
                  description: str | None, screenshot_path: str | None = None) -> ProctorEvent:
    event = ProctorEvent(
        attempt_id=attempt_id, event_type=event_type, severity=severity,
        description=description, screenshot_path=screenshot_path,
    )
    db.add(event)
    db.commit()
    db.refresh(event)
    return event


def set_event_screenshot(db: Session, event_id: int, screenshot_path: str) -> None:
    event = db.query(ProctorEvent).filter(ProctorEvent.id == event_id).first()
    if not event:
        return
    event.screenshot_path = screenshot_path
    db.commit()


def get_event(db: Session, event_id: int) -> ProctorEvent | None:
    return db.query(ProctorEvent).filter(ProctorEvent.id == event_id).first()


def list_events_for_attempt(db: Session, attempt_id: int) -> list[ProctorEvent]:
    return db.query(ProctorEvent).filter(ProctorEvent.attempt_id == attempt_id).order_by(ProctorEvent.created_at).all()


def events_for_attempts(db: Session, attempt_ids: list[int]) -> dict[int, list[ProctorEvent]]:
    """Every event for these attempts, grouped by attempt id, in ONE query."""
    if not attempt_ids:
        return {}
    rows = (
        db.query(ProctorEvent)
        .filter(ProctorEvent.attempt_id.in_(attempt_ids))
        .order_by(ProctorEvent.created_at)
        .all()
    )
    grouped: dict[int, list[ProctorEvent]] = {}
    for row in rows:
        grouped.setdefault(row.attempt_id, []).append(row)
    return grouped


def list_events_for_exam(db: Session, exam_id: int) -> list[ProctorEvent]:
    from app.models.attempt import StudentExamAttempt
    return (
        db.query(ProctorEvent)
        .join(StudentExamAttempt, ProctorEvent.attempt_id == StudentExamAttempt.id)
        .filter(StudentExamAttempt.exam_id == exam_id)
        .order_by(ProctorEvent.created_at)
        .all()
    )


def list_events_by_types(db: Session, attempt_id: int, event_types: list[str]) -> list[ProctorEvent]:
    """Chronological events of the given types for one attempt. Used by the
    lockdown strike counter, which must survive a page refresh -- so the count
    is always derived from persisted rows, never from client state."""
    return (
        db.query(ProctorEvent)
        .filter(ProctorEvent.attempt_id == attempt_id, ProctorEvent.event_type.in_(event_types))
        .order_by(ProctorEvent.created_at)
        .all()
    )


def get_face_profile(db: Session, student_id: int) -> FaceProfile | None:
    return db.query(FaceProfile).filter(FaceProfile.student_id == student_id).first()


def students_with_face_profiles(db: Session, student_ids: list[int]) -> set[int]:
    """Which of these students have a face profile, in ONE query."""
    if not student_ids:
        return set()
    rows = (
        db.query(FaceProfile.student_id)
        .filter(FaceProfile.student_id.in_(student_ids))
        .all()
    )
    return {student_id for (student_id,) in rows}


def save_face_profile(db: Session, student_id: int, image_path: str, encoding_json: str) -> FaceProfile:
    profile = get_face_profile(db, student_id)
    if profile:
        profile.image_path = image_path
        profile.encoding = encoding_json
    else:
        profile = FaceProfile(student_id=student_id, image_path=image_path, encoding=encoding_json)
        db.add(profile)
    db.commit()
    db.refresh(profile)
    return profile


def paginated_events_for_exam(db: Session, exam_id: int, *, offset: int, limit: int,
                              severity: str = "") -> tuple[list[ProctorEvent], int]:
    """One page of an exam's violations, newest first, plus the total."""
    from app.models.attempt import StudentExamAttempt
    from app.models.student import Student

    query = (
        db.query(ProctorEvent)
        .join(StudentExamAttempt, StudentExamAttempt.id == ProctorEvent.attempt_id)
        .options(joinedload(ProctorEvent.attempt)
                 .joinedload(StudentExamAttempt.student)
                 .joinedload(Student.user))
        .filter(StudentExamAttempt.exam_id == exam_id)
    )
    if severity.strip():
        query = query.filter(ProctorEvent.severity == severity.strip())

    total = query.order_by(None).count()
    rows = query.order_by(ProctorEvent.created_at.desc()).offset(offset).limit(limit).all()
    return rows, total
