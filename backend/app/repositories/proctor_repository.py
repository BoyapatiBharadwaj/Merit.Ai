"""
Data access for ProctorEvent and FaceProfile tables.
"""
from sqlalchemy.orm import Session
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
