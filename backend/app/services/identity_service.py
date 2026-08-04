"""
Student identity verification state machine.

A student self-registers with a name and email, and can browse the site
immediately. Before their *first* proctored exam they must clear two gates:

    1. Face registration  -> stored as a FaceProfile row (biometric embedding)
    2. ID card OCR match  -> stored as Student.id_verified

Once both are satisfied the account is *identity-locked*: the student's legal
name and email become immutable, because those are exactly the fields the ID
card was matched against. Allowing an edit afterwards would let someone verify
as themselves and then rename the account to sit an exam as somebody else.
Password remains changeable -- it is a credential, not an identity claim.

Everything here is deliberately server-side. The frontend mirrors this state
for UX, but the authoritative check runs in start_attempt().
"""
from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models.student import Student
from app.repositories import proctor_repository


def _now() -> datetime:
    return datetime.now(timezone.utc)


def has_face_profile(db: Session, student: Student) -> bool:
    return proctor_repository.get_face_profile(db, student.id) is not None


def maybe_lock_identity(db: Session, student: Student) -> bool:
    """Lock the account the moment both gates are satisfied. Idempotent, and
    safe to call after either verification step completes -- the student may
    do face and ID in either order."""
    if student.identity_locked:
        return True
    if not (student.id_verified and has_face_profile(db, student)):
        return False
    student.identity_locked = True
    student.identity_locked_at = _now()
    db.commit()
    db.refresh(student)
    return True


def record_face_registration(db: Session, student: Student) -> None:
    """Called after a FaceProfile is successfully written."""
    maybe_lock_identity(db, student)


def record_id_verification(db: Session, student: Student, name_matched: bool, extracted_name: str | None,
                            image_path: str | None = None) -> None:
    """Persist the ID-card OCR outcome, and keep the submitted photo on file.

    A failed match is intentionally NOT recorded as a rejection flag -- the
    student can simply retry with a better photo. Only a successful match
    mutates the verified fields, so a bad scan can never permanently block an
    account. The photo itself is saved either way (most recent attempt wins):
    an admin or examiner looking at a student who keeps failing verification
    needs to see what they're actually submitting, not just "OCR said no".
    """
    changed = False
    if image_path and student.id_card_image_path != image_path:
        student.id_card_image_path = image_path
        changed = True
    if name_matched and not student.id_verified:
        student.id_verified = True
        student.id_verified_at = _now()
        student.id_verified_name = (extracted_name or "").strip()[:150] or None
        changed = True
    if changed:
        db.commit()
        db.refresh(student)
    maybe_lock_identity(db, student)


def verification_state(db: Session, student: Student | None) -> dict:
    """Shape consumed by GET /users/me and the exam-entry gate."""
    if student is None:
        return {"face_registered": False, "id_verified": False, "identity_locked": False, "exam_ready": False}
    face_registered = has_face_profile(db, student)
    return {
        "face_registered": face_registered,
        "id_verified": bool(student.id_verified),
        "identity_locked": bool(student.identity_locked),
        "exam_ready": face_registered and bool(student.id_verified),
    }


def require_exam_ready(db: Session, student: Student) -> None:
    """Hard gate for proctored exams. Raises with a message naming the exact
    step still outstanding, so the UI never has to guess."""
    state = verification_state(db, student)
    if state["exam_ready"]:
        return

    missing = []
    if not state["face_registered"]:
        missing.append("register your face")
    if not state["id_verified"]:
        missing.append("verify your ID card")

    raise HTTPException(
        status.HTTP_403_FORBIDDEN,
        f"Identity verification incomplete. Please {' and '.join(missing)} on your Profile page before starting a proctored exam.",
    )
