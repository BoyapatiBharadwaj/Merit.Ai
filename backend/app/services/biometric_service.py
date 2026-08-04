"""
Lifecycle for biometric data: consent, erasure, and retention.

This platform stores four kinds of biometric or biometric-adjacent artefact:

    face_profiles.encoding        512-d ArcFace embedding
    face_profiles.image_path      the registration photo on disk
    students.id_card_image_path   the uploaded ID card
    proctor_events.screenshot     violation screenshots (image of the candidate)

Until this module existed none of it had a lifecycle at all -- no recorded
consent, no expiry, and no deletion path short of hand-written SQL. For a
product processing identifiable candidates' faces that is the finding most
likely to become a legal problem rather than a technical one.

Two design decisions worth being explicit about:

**Erasure blanks the payload; it does not delete the row.** A face_profiles row
is referenced by a student's attempt history, and `ON DELETE CASCADE` from
students would take assessment records with it. So `erase_student_biometrics`
clears the embedding, unlinks the files from disk, and stamps `deleted_at` --
leaving a durable, auditable record that the erasure happened and when, which is
itself usually a compliance requirement. Scores, attempts and violation
timelines are untouched.

**Automatic retention is opt-in and defaults to off.** BIOMETRIC_RETENTION_DAYS
is 0 out of the box, meaning nothing is ever purged automatically. The right
period is an institutional and legal decision, and a default that quietly
destroyed evidence during an open appeal would be a worse failure than one that
keeps too much. Manual deletion works regardless of the setting.
"""
import logging
import os
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.attempt import StudentExamAttempt
from app.models.face_profile import FaceProfile
from app.models.student import Student

logger = logging.getLogger("app")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def current_consent_version() -> str:
    return settings.BIOMETRIC_CONSENT_VERSION


def record_face_consent(profile: FaceProfile) -> None:
    """Stamp the consent that was in force when this capture happened.

    Called by the registration path rather than being inferred later, because
    "which wording did this person actually agree to" is only knowable at the
    moment of capture.
    """
    profile.consent_version = settings.BIOMETRIC_CONSENT_VERSION
    profile.consented_at = _now()


def record_id_consent(student: Student) -> None:
    student.id_consent_version = settings.BIOMETRIC_CONSENT_VERSION
    student.id_consented_at = _now()


def consent_status(db: Session, student: Student) -> dict:
    """What this student has consented to, and whether it is still current.

    `stale` matters: bumping BIOMETRIC_CONSENT_VERSION is how an institution
    marks previously-collected consent as no longer covering the current terms,
    and this is what surfaces that to the UI so re-consent can be requested.
    """
    profile = db.query(FaceProfile).filter(FaceProfile.student_id == student.id).first()
    current = settings.BIOMETRIC_CONSENT_VERSION
    face_version = profile.consent_version if profile else None
    return {
        "current_version": current,
        "face": {
            "captured": bool(profile and not profile.deleted_at),
            "consent_version": face_version,
            "consented_at": profile.consented_at if profile else None,
            "stale": bool(face_version and face_version != current),
        },
        "id_card": {
            "captured": bool(student.id_card_image_path),
            "consent_version": student.id_consent_version,
            "consented_at": student.id_consented_at,
            "stale": bool(student.id_consent_version and student.id_consent_version != current),
        },
        "erased_at": profile.deleted_at if profile else None,
    }


def _unlink(path: str | None) -> bool:
    """Remove a file, tolerating one that is already gone.

    Best-effort by design: a missing or unremovable file must not abort an
    erasure request half-way, leaving the embedding in the database because a
    stale path could not be unlinked. The database side is what actually
    matters for re-identification; a stranded JPEG is a smaller problem than a
    deletion that reports failure and leaves everything in place.
    """
    if not path:
        return False
    try:
        os.remove(path)
        return True
    except FileNotFoundError:
        return False
    except OSError:
        logger.warning("Could not remove biometric file %s", path)
        return False


def erase_student_biometrics(db: Session, student: Student, *, reason: str) -> dict:
    """Erase this student's biometric payload. Idempotent.

    Returns a summary of what was actually removed, so an admin acting on a
    deletion request has something concrete to record rather than a bare 204.
    """
    removed = {"embedding": False, "face_image": False, "id_card_image": False}

    profile = db.query(FaceProfile).filter(FaceProfile.student_id == student.id).first()
    if profile and not profile.deleted_at:
        removed["face_image"] = _unlink(profile.image_path)
        # An empty JSON array rather than NULL: the column is NOT NULL, and
        # face_service._parse_stored_encoding already treats anything that is
        # not a 512-length vector as unusable, so an erased profile fails
        # verification the same way a legacy one does -- with the message that
        # tells the student to register again.
        profile.encoding = "[]"
        profile.image_path = ""
        profile.deleted_at = _now()
        profile.deletion_reason = reason[:100]
        removed["embedding"] = True

    if student.id_card_image_path:
        removed["id_card_image"] = _unlink(student.id_card_image_path)
        student.id_card_image_path = None

    # Identity verification is unwound too. Leaving `identity_locked` set after
    # erasing the very data it was derived from would lock the student out of
    # their own name and email edits forever, with nothing on file to justify
    # it -- and would let them start a proctored exam that can no longer
    # actually verify them.
    student.identity_locked = False
    student.identity_locked_at = None

    try:
        db.commit()
    except Exception:
        db.rollback()
        raise

    logger.info("Erased biometrics for student %s (reason=%s): %s", student.id, reason, removed)
    return removed


def students_past_retention(db: Session, *, now: datetime | None = None) -> list[Student]:
    """Students whose biometric data is eligible for automatic deletion.

    Eligibility is measured from the student's LAST ATTEMPT, not from when the
    data was captured. Retention exists to cover the window in which a result
    might be disputed, and that window opens when they last sat an exam -- a
    candidate who registered two years ago and sat a paper last week must not
    have their identity evidence purged mid-appeal.

    Students who have never attempted anything fall back to their registration
    date, so an abandoned signup does not keep a face embedding forever.
    """
    if settings.BIOMETRIC_RETENTION_DAYS <= 0:
        return []

    now = now or _now()
    cutoff = now - timedelta(days=settings.BIOMETRIC_RETENTION_DAYS)

    candidates = (
        db.query(Student)
        .join(FaceProfile, FaceProfile.student_id == Student.id)
        .filter(FaceProfile.deleted_at.is_(None))
        .all()
    )

    due = []
    for student in candidates:
        last_attempt = (
            db.query(StudentExamAttempt.started_at)
            .filter(StudentExamAttempt.student_id == student.id)
            .order_by(StudentExamAttempt.started_at.desc())
            .first()
        )
        reference = _as_utc(last_attempt[0]) if last_attempt else _as_utc(student.created_at)
        if reference is not None and reference < cutoff:
            due.append(student)
    return due


def purge_expired(db: Session, *, now: datetime | None = None) -> int:
    """One retention sweep. Returns how many students were erased.

    A no-op returning 0 when BIOMETRIC_RETENTION_DAYS is 0, which is the
    default -- see the module docstring on why automatic deletion is opt-in.
    """
    if settings.BIOMETRIC_RETENTION_DAYS <= 0:
        return 0

    erased = 0
    for student in students_past_retention(db, now=now):
        try:
            erase_student_biometrics(
                db, student,
                reason=f"retention:{settings.BIOMETRIC_RETENTION_DAYS}d",
            )
            erased += 1
        except Exception:
            # One student's failure must not abort the whole sweep -- the next
            # run would restart from the same row and never get past it.
            logger.exception("Retention purge failed for student %s; continuing", student.id)
    if erased:
        logger.info("Retention sweep erased biometrics for %s student(s)", erased)
    return erased
