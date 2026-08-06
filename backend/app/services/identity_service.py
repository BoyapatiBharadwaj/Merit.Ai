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
from app.services import biometric_service


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
    clear_reverification(db, student, commit=True)
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
        biometric_service.record_id_consent(student)
        changed = True
    if name_matched and not student.id_verified:
        student.id_verified = True
        student.id_verified_at = _now()
        student.id_verified_name = (extracted_name or "").strip()[:150] or None
        changed = True
    if changed:
        db.commit()
        db.refresh(student)
    clear_reverification(db, student, commit=True)
    maybe_lock_identity(db, student)


def require_reverification(db: Session, student: Student, *, reason: str | None,
                            requested_by_id: int | None, commit: bool = True) -> Student:
    """Ask a candidate to prove their identity again before their next exam.

    Does not touch the stored face embedding or ID image -- see the model
    comment on Student.reverification_required_at for why keeping them is the
    whole point. `unlock_identity` is also left alone: this is about the
    biometric evidence, not about whether the account's name may be edited,
    and conflating them would silently hand back a permission nobody asked to
    grant.
    """
    student.reverification_required_at = _now()
    student.reverification_reason = (reason or "").strip()[:500] or None
    student.reverification_requested_by_id = requested_by_id
    # Both halves must be redone, so both are marked outstanding again. The old
    # photo stays on disk until the new one overwrites it.
    student.id_verified = False
    if commit:
        db.commit()
        db.refresh(student)
    return student


def clear_reverification(db: Session, student: Student, *, commit: bool = False) -> bool:
    """Drop the flag once BOTH halves have actually been redone.

    Called after each verification step, not from the endpoint that asks for
    re-verification -- the request is satisfied by the candidate's work, not by
    anyone declaring it satisfied. Requiring both halves is deliberate: clearing
    after only the face would let a candidate whose ID card was the problem walk
    straight back through the gate.
    """
    if student.reverification_required_at is None:
        return False
    if not (student.id_verified and has_face_profile(db, student)):
        return False
    student.reverification_required_at = None
    student.reverification_reason = None
    student.reverification_requested_by_id = None
    if commit:
        db.commit()
        db.refresh(student)
    return True


def verification_state(db: Session, student: Student | None, *,
                        face_registered_ids: set[int] | None = None) -> dict:
    """Shape consumed by GET /users/me and the exam-entry gate.

    `face_registered_ids` lets a caller rendering a LIST pre-compute the face
    lookup once for the whole page (see
    proctor_repository.students_with_face_profiles) instead of paying a query
    per row. Optional so the single-student callers -- which are the majority --
    stay unchanged and can't accidentally pass a stale set.
    """
    if student is None:
        return {"face_registered": False, "id_verified": False, "identity_locked": False,
                "exam_ready": False, "reverification_required": False,
                "reverification_reason": None, "reverification_required_at": None}
    face_registered = (
        student.id in face_registered_ids
        if face_registered_ids is not None
        else has_face_profile(db, student)
    )
    # An outstanding re-verification request closes the gate on its own, even
    # though the stored face and ID are still technically present and valid.
    # That is the point: the administrator is saying "I do not currently accept
    # this evidence", and the candidate must supply new evidence before sitting
    # anything. Keeping the old records readable while refusing to rely on them
    # is what lets a reviewer compare the two afterwards.
    reverification_required = student.reverification_required_at is not None

    return {
        "face_registered": face_registered,
        "id_verified": bool(student.id_verified),
        "identity_locked": bool(student.identity_locked),
        "exam_ready": face_registered and bool(student.id_verified) and not reverification_required,
        "reverification_required": reverification_required,
        "reverification_reason": student.reverification_reason,
        "reverification_required_at": student.reverification_required_at,
    }


def require_exam_ready(db: Session, student: Student) -> None:
    """Hard gate for proctored exams. Raises with a message naming the exact
    step still outstanding, so the UI never has to guess."""
    state = verification_state(db, student)
    if state["exam_ready"]:
        return

    # Named separately rather than folded into "incomplete". Telling somebody
    # whose face and ID are both on file that their verification is incomplete
    # is simply false, and sends them to a Profile page that shows two green
    # ticks -- so they conclude the platform is broken rather than that
    # something was asked of them.
    if state["reverification_required"]:
        reason = (student.reverification_reason or "").strip()
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Your institution has asked you to verify your identity again before your next "
            "proctored exam. Please re-register your face and re-submit your ID card on your "
            "Profile page."
            + (f" Reason given: {reason}" if reason else ""),
        )

    missing = []
    if not state["face_registered"]:
        missing.append("register your face")
    if not state["id_verified"]:
        missing.append("verify your ID card")

    raise HTTPException(
        status.HTTP_403_FORBIDDEN,
        f"Identity verification incomplete. Please {' and '.join(missing)} on your Profile page before starting a proctored exam.",
    )
