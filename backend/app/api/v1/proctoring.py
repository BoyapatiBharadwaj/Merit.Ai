"""AI proctoring endpoints."""
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import (
    attempt_student, exam_student, get_current_user, rate_limit_user, require_student,
)
from app.database.session import get_db
from app.schemas.pagination import Page, PageParams, build_page
from app.models.enums import RoleName
from app.models.user import User
from app.repositories import attempt_repository, exam_repository, proctor_repository, user_repository
from app.schemas.admin import ViolationDecisionUpdate
from app.schemas.proctor import (
    FaceMatchResponse, IDCardVerifyResponse, ObjectDetectionResponse,
    PoseCheckResponse, ProctorEventBatchCreate, ProctorEventCreate, ProctorEventOut,
)
from app.models.activity_log import ActivityType
from app.services import (activity_service, admin_service, identity_service, lockdown_service,
                          organization_service, proctor_service)

router = APIRouter(prefix="/proctoring", tags=["AI Proctoring"])


class ImagePayload(BaseModel):
    image_base64: str = Field(min_length=20, max_length=7_000_000)


def _can_view_attempt(user: User, attempt) -> bool:
    if user.role.name == "admin":
        return True
    if user.role.name == "student":
        return bool(user.student_profile and user.student_profile.id == attempt.student_id)
    return bool(user.examiner_profile and user.examiner_profile.id == attempt.exam.examiner_id)


@router.post("/face/register", dependencies=[Depends(rate_limit_user("ai_face_register"))])
def register_face(payload: ImagePayload, request: Request, db: Session = Depends(get_db), user: User = Depends(require_student)):
    student = user_repository.get_student_by_user_id(db, user.id)
    if student.identity_locked:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Your identity is already verified and locked. Contact an administrator to re-register your face.",
        )
    _, message = proctor_service.register_face(db, student.id, payload.image_base64)
    was_locked = bool(student.identity_locked)
    identity_service.record_face_registration(db, student)
    activity_service.record(db, activity_type=ActivityType.FACE_REGISTERED, subject=user, request=request)
    if student.identity_locked and not was_locked:
        activity_service.record(db, activity_type=ActivityType.IDENTITY_LOCKED, subject=user, request=request)
    return {"registered": True, "message": message, **identity_service.verification_state(db, student)}


@router.post("/face/verify", response_model=FaceMatchResponse,
             dependencies=[Depends(rate_limit_user("ai_face_verify"))])
def verify_face(payload: ImagePayload, db: Session = Depends(get_db), user: User = Depends(exam_student)):
    student = user_repository.get_student_by_user_id(db, user.id)
    return FaceMatchResponse(**proctor_service.verify_live_face(db, student.id, payload.image_base64))


@router.post("/id-card/verify", response_model=IDCardVerifyResponse,
             dependencies=[Depends(rate_limit_user("ai_id_card"))])
def verify_id_card(payload: ImagePayload, request: Request, db: Session = Depends(get_db), user: User = Depends(require_student)):
    student = user_repository.get_student_by_user_id(db, user.id)
    result, image_path = proctor_service.verify_id_card(db, student.id, user.full_name, payload.image_base64)
    matched = bool(result.get("name_matched"))
    was_locked = bool(student.identity_locked)
    identity_service.record_id_verification(
        db, student, matched, result.get("extracted_text"), image_path,
    )
    activity_service.record(
        db,
        activity_type=ActivityType.ID_VERIFIED if matched else ActivityType.ID_VERIFICATION_FAILED,
        subject=user, request=request,
    )
    if student.identity_locked and not was_locked:
        activity_service.record(db, activity_type=ActivityType.IDENTITY_LOCKED, subject=user, request=request)
    return IDCardVerifyResponse(**result)


@router.get("/identity/status")
def identity_status(db: Session = Depends(get_db), user: User = Depends(require_student)):
    """Cheap poll for the Profile page and the dashboard's exam-entry gate."""
    student = user_repository.get_student_by_user_id(db, user.id)
    return identity_service.verification_state(db, student)


def _can_view_student_identity(db: Session, user: User, student_id: int) -> bool:
    """Shared gate for every endpoint serving a student's identity material
    (registered face photo, ID card photo) -- one authority so the two
    checks can never drift apart, the same reasoning
    organization_service.can_student_access_exam documents for exam access.

    Three routes in: the student themselves, any admin, or an examiner
    connected to this student via organization_service.examiner_can_view_student
    (same organization, or a direct per-exam invite). Everyone else is
    denied -- see get_face_photo's docstring for why this used to be public
    to any authenticated request at all.
    """
    if user.role.name == RoleName.ADMIN.value:
        return True
    if user.student_profile and user.student_profile.id == student_id:
        return True
    if user.role.name == RoleName.EXAMINER.value:
        examiner = user_repository.get_examiner_by_user_id(db, user.id)
        student = user_repository.get_student_by_id(db, student_id)
        return organization_service.examiner_can_view_student(db, examiner, student)
    return False


@router.get("/face/photo/{student_id}")
def get_face_photo(student_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Serve a student's registered face photo.

    Replaces the old `app.mount("/uploads", StaticFiles(...))` in main.py,
    which served every file under UPLOAD_DIR -- including every student's
    face photo -- to anyone on the network with no authentication at all;
    the filename's only "secret" was an 8-char random suffix. Nothing in the
    frontend actually linked to that mount (confirmed by grep), so it was
    pure exposed surface with no user relying on it being public. This
    endpoint restores the one legitimate use (staff or the student
    themselves viewing the photo on file) behind real auth and authorization
    instead.
    """
    if not _can_view_student_identity(db, user, student_id):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You do not have access to this photo.")

    profile = proctor_repository.get_face_profile(db, student_id)
    if not profile or not profile.image_path:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No face photo registered for this student.")

    path = Path(profile.image_path)
    if not path.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "The photo file is missing on disk.")
    return FileResponse(path, media_type="image/jpeg")


@router.get("/id-card/photo/{student_id}")
def get_id_card_photo(student_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Serve the ID card photo captured during this student's identity
    verification (saved to disk by proctor_service.verify_id_card since
    migration 0014 added Student.id_card_image_path as somewhere to put it).
    Same authorization gate as GET /face/photo/{id} -- this is the same
    class of sensitive, identity-confirming material.
    """
    if not _can_view_student_identity(db, user, student_id):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You do not have access to this photo.")

    student = user_repository.get_student_by_id(db, student_id)
    if not student or not student.id_card_image_path:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No ID card on file for this student.")

    path = Path(student.id_card_image_path)
    if not path.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "The ID card file is missing on disk.")
    return FileResponse(path, media_type="image/jpeg")


@router.post("/objects/detect", response_model=ObjectDetectionResponse,
             dependencies=[Depends(rate_limit_user("ai_objects"))])
def detect_objects(payload: ImagePayload, user: User = Depends(exam_student)):
    return ObjectDetectionResponse(**proctor_service.detect_objects(payload.image_base64))


@router.post("/pose/check", response_model=PoseCheckResponse,
             dependencies=[Depends(rate_limit_user("ai_pose"))])
def check_pose(payload: ImagePayload, user: User = Depends(exam_student)):
    return PoseCheckResponse(**proctor_service.analyze_pose(payload.image_base64))


@router.post("/events", response_model=ProctorEventOut, status_code=201)
def log_event(payload: ProctorEventCreate, background_tasks: BackgroundTasks,
              db: Session = Depends(get_db), user: User = Depends(exam_student)):
    student = user_repository.get_student_by_user_id(db, user.id)
    attempt = attempt_repository.get_attempt(db, payload.attempt_id)
    if not attempt or attempt.student_id != student.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Attempt not found.")
    return proctor_service.log_event(
        db, payload.attempt_id, payload.event_type.value, payload.description,
        payload.screenshot_base64, background_tasks=background_tasks,
    )


@router.post("/events/batch", status_code=201)
def log_events_batch(payload: ProctorEventBatchCreate, background_tasks: BackgroundTasks,
                      db: Session = Depends(get_db), user: User = Depends(exam_student)):
    """Batched counterpart to POST /events -- see frontend/src/lib/eventLogger.js,
    which queues violations from lockdown.js and proctoring.js and flushes
    them together instead of one request per violation.

    Ownership is re-checked per item, not once for the whole payload: unlike
    the single-event endpoint (one attempt_id up front), a batch is client-
    assembled, so a tampered or buggy client could otherwise smuggle events
    for an attempt_id it doesn't own past a single up-front check. An
    unowned/missing attempt_id is silently skipped rather than failing the
    whole batch -- this is best-effort telemetry, not a transactional write,
    and a mixed batch (some valid, one stale from a just-finished attempt)
    should still land the valid events.
    """
    student = user_repository.get_student_by_user_id(db, user.id)
    created = 0
    for item in payload.events:
        attempt = attempt_repository.get_attempt(db, item.attempt_id)
        if not attempt or attempt.student_id != student.id:
            continue
        proctor_service.log_event(
            db, item.attempt_id, item.event_type.value, item.description,
            item.screenshot_base64, background_tasks=background_tasks,
        )
        created += 1
    return {"created": created, "received": len(payload.events)}


class LockdownStrikeRequest(BaseModel):
    attempt_id: int = Field(gt=0)
    event_type: str = Field(pattern="^(fullscreen_exit|tab_switch|screen_share_stopped)$")
    description: str | None = Field(default=None, max_length=255)


@router.post("/lockdown/strike")
def record_lockdown_strike(payload: LockdownStrikeRequest, db: Session = Depends(get_db),
                           user: User = Depends(exam_student)):
    """Report a lockdown breach and get back the authoritative strike state.

    Deliberately separate from /events: the response drives whether the exam
    keeps running, so it must be a synchronous, server-computed decision rather
    than something the client tallies for itself.
    """
    student = user_repository.get_student_by_user_id(db, user.id)
    return lockdown_service.record_strike(db, student.id, payload.attempt_id, payload.event_type, payload.description)


@router.get("/lockdown/status/{attempt_id}")
def get_lockdown_status(attempt_id: int, db: Session = Depends(get_db), user: User = Depends(attempt_student)):
    """Read the strike count without adding one -- used on exam resume so a
    reload shows the true remaining budget instead of starting over at zero."""
    student = user_repository.get_student_by_user_id(db, user.id)
    attempt = attempt_repository.get_attempt(db, attempt_id)
    if not attempt or attempt.student_id != student.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Attempt not found.")
    return lockdown_service.strike_status(db, attempt_id)


@router.get("/events/attempt/{attempt_id}", response_model=list[ProctorEventOut])
def get_events_for_attempt(attempt_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    attempt = attempt_repository.get_attempt(db, attempt_id)
    if not attempt or not _can_view_attempt(user, attempt):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Attempt not found.")
    return proctor_repository.list_events_for_attempt(db, attempt_id)


@router.get("/events/exam/{exam_id}", response_model=Page[dict])
def get_events_for_exam(exam_id: int, params: PageParams = Depends(), severity: str = "",
                        db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """One page of this exam's violations, with enough context to review them.

    Two problems, both fixed here. It returned bare event rows -- an attempt id,
    a type, a severity and a timestamp -- so reviewing "attempt 47 flagged for
    multiple_faces" meant leaving the page to find out whose attempt 47 was, and
    the description and screenshot the proctoring system had already captured
    were never surfaced. And it returned ALL of them, which for a full hall is
    thousands of rows sent to render twenty-five.
    """
    exam = exam_repository.get_exam(db, exam_id)
    if not exam or (user.role.name != "admin" and (not user.examiner_profile or user.examiner_profile.id != exam.examiner_id)):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Exam not found.")

    events, total = proctor_repository.paginated_events_for_exam(
        db, exam_id, offset=params.offset, limit=params.page_size, severity=severity,
    )
    items = [{
        "id": event.id,
        "attempt_id": event.attempt_id,
        "student_name": (event.attempt.student.user.full_name
                         if event.attempt and event.attempt.student and event.attempt.student.user
                         else None),
        "event_type": event.event_type,
        "severity": event.severity,
        "description": event.description,
        "has_screenshot": bool(event.screenshot_path),
        "admin_decision": getattr(event, "admin_decision", None),
        "created_at": event.created_at,
    } for event in events]
    return build_page(items, total, params)


@router.get("/events/{event_id}/screenshot")
def get_violation_screenshot(event_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Serves one violation's captured screenshot -- the candidate exam
    report's violation-timeline "Evidence" action. Staff-only (admin, or the
    exam's owning examiner): this is the same class of sensitive webcam-
    captured material as the identity photos, gated the same way rather than
    reusing _can_view_attempt's broader (student-inclusive) access, since the
    violation timeline itself is only ever shown to staff in the first place
    (see attempt_service.build_staff_report / GET /attempts/{id}/staff-report).
    """
    event = proctor_repository.get_event(db, event_id)
    if not event:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Violation not found.")
    attempt = event.attempt
    is_staff = user.role.name == RoleName.ADMIN.value or (
        user.role.name == RoleName.EXAMINER.value
        and user.examiner_profile
        and user.examiner_profile.id == attempt.exam.examiner_id
    )
    if not is_staff:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You do not have access to this evidence.")
    if not event.screenshot_path:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No screenshot captured for this violation.")
    path = Path(event.screenshot_path)
    if not path.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "The screenshot file is missing on disk.")
    return FileResponse(path, media_type="image/jpeg")


@router.patch("/events/{event_id}/decision")
def update_violation_decision(event_id: int, payload: ViolationDecisionUpdate,
                              db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Sets a reviewer's verdict (pending / confirmed / misleading -- stored
    as AdminDecision.DISMISSED) on one logged violation, in the candidate
    exam report's violation timeline.

    Opened up to the exam's own owning examiner, not just an admin: every
    violation for their candidates now lives only inside that per-attempt
    review (see GET /attempts/{id}/staff-report), and there is no separate
    admin-only worklist for them to hand adjudication off to. Same ownership
    check as every other staff-scoped endpoint here (get_events_for_exam,
    get_violation_screenshot) -- an examiner may only decide on violations
    from an exam they themselves own; an admin may decide on any.
    """
    event = proctor_repository.get_event(db, event_id)
    if not event:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Violation not found.")
    is_staff = user.role.name == RoleName.ADMIN.value or (
        user.role.name == RoleName.EXAMINER.value
        and user.examiner_profile
        and user.examiner_profile.id == event.attempt.exam.examiner_id
    )
    if not is_staff:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You do not have access to this violation.")
    return admin_service.set_violation_decision(db, event_id, payload.decision)