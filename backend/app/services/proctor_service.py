"""
Business logic for AI proctoring: face registration/matching, ID OCR,
and violation event logging + severity escalation.
"""
import base64
import binascii
import logging
import os
import uuid

import numpy as np
from fastapi import BackgroundTasks, HTTPException, status
from sqlalchemy.orm import Session

from app.ai import ai_worker_client, face_service, object_service, ocr_service, pose_service
from app.ai.image_utils import decode_image
from app.core.config import settings
from app.database.session import SessionLocal
from app.repositories import proctor_repository, attempt_repository
from app.services import biometric_service

logger = logging.getLogger("app")


SEVERITY_MAP = {
    "no_face": "medium",
    "multiple_faces": "high",
    "face_mismatch": "high",
    "fullscreen_exit": "medium",
    "tab_switch": "medium",
    "copy_paste_attempt": "low",
    "right_click_attempt": "low",
    "noise_detected": "low",
    "noise_detected_loud": "high",
    "id_name_mismatch": "high",
    "spoof_detected": "high",
    "phone_detected": "high",
    "book_detected": "medium",
    "multiple_persons_detected": "high",
    "looking_away": "medium",
    "gaze_deviation": "low",
    "external_monitor_detected": "high",
    "screenshot_attempt": "high",
    "screen_share_stopped": "high",
    "lockdown_terminated": "high",
}


def register_face(db: Session, student_id: int, base64_image: str):
    os.makedirs(settings.FACES_DIR, exist_ok=True)
    filename = f"{settings.FACES_DIR}/student_{student_id}_{uuid.uuid4().hex[:8]}.jpg"
    success, message, encoding_json = face_service.register_face(base64_image, filename)
    if not success:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, message)
    profile = proctor_repository.save_face_profile(db, student_id, filename, encoding_json)
    # Stamp the consent wording in force at capture time. Done here rather than
    # in the repository because it is a policy fact about this capture, not a
    # storage detail -- see biometric_service for why it is per-profile.
    biometric_service.record_face_consent(profile)
    db.commit()
    db.refresh(profile)
    return profile, message


def verify_live_face(db: Session, student_id: int, base64_image: str) -> dict:
    if not settings.FACE_MATCHING_ENABLED:
        # Switched off operationally. Reported as "not collected" rather than as
        # a pass or a failure: claiming a match we never computed would be a lie
        # on a proctoring report, and claiming a mismatch would accuse an
        # innocent candidate. The exam client skips the check on available=False.
        return {
            "available": False, "face_count": 0, "match": None, "distance": None,
            "spoof_suspected": False, "liveness_score": None,
            "message": "Face matching is currently disabled by the administrator.",
        }
    profile = proctor_repository.get_face_profile(db, student_id)
    if not profile:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Face not registered yet.")
    return {"available": True, **face_service.verify_live_frame(base64_image, profile.encoding)}


def verify_id_card(db: Session, student_id: int, student_full_name: str, base64_image: str) -> tuple[dict, str | None]:
    """Runs the OCR name-match and separately persists the submitted photo to
    disk, mirroring register_face's own image save. Returns the OCR result
    dict alongside the saved image path (None if saving failed), for the
    caller to hand to identity_service.record_id_verification.
    """
    result = ocr_service.verify_id_card(base64_image, student_full_name)
    image_path = _save_id_card_image(student_id, base64_image)
    return result, image_path


def _save_id_card_image(student_id: int, base64_image: str) -> str | None:
    """Best-effort: a disk problem here must never fail identity
    verification itself, whose pass/fail result already stands on its own
    (see ocr_service.verify_id_card, called just before this)."""
    try:
        image = decode_image(base64_image)
        os.makedirs(settings.ID_CARDS_DIR, exist_ok=True)
        filename = f"{settings.ID_CARDS_DIR}/student_{student_id}_{uuid.uuid4().hex[:8]}.jpg"
        image.save(filename, quality=90)
        return filename
    except Exception:
        logger.exception("Failed to save ID card image for student %s", student_id)
        return None


def detect_objects(base64_image: str) -> dict:
    """Phone/book/multi-person detection.

    Prefers the ai_worker when AI_SERVICE_URL is configured, and otherwise runs
    YOLO11s in-process (app/ai/object_service.py). That local path is new: with
    the worker deferred this endpoint used to return "unavailable" every time,
    which meant phone/book/second-person detection was not degraded but simply
    off. Face matching already had an in-process fallback for the same reason.

    Still never raises -- an unavailable detector returns the same shape with
    available=False, so the frontend stops polling quietly instead of erroring.
    That existing contract is exactly what OBJECT_DETECTION_ENABLED reuses: an
    operationally disabled detector is indistinguishable, to the client, from
    one that could not load -- and should be, because the correct client
    behaviour is identical.
    """
    if not settings.OBJECT_DETECTION_ENABLED:
        return {"available": False, "detections": [], "person_count": 0,
                "message": "Object detection is currently disabled by the administrator."}

    worker_result = ai_worker_client.detect_objects(base64_image)
    if worker_result is not None:
        return {"available": True, **worker_result, "message": "ok"}

    image = np.asarray(decode_image(base64_image))
    return object_service.detect(image)


def analyze_pose(base64_image: str) -> dict:
    """Local head-pose + gaze-deviation analysis (no AI worker required)."""
    if not settings.POSE_DETECTION_ENABLED:
        return {"available": False, "face_count": 0,
                "message": "Pose and gaze analysis is currently disabled by the administrator."}
    return pose_service.analyze_frame(base64_image)


def log_event(db: Session, attempt_id: int, event_type: str, description: str | None,
              screenshot_base64: str | None = None, background_tasks: BackgroundTasks | None = None):
    attempt = attempt_repository.get_attempt(db, attempt_id)
    if not attempt:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Attempt not found.")

    severity = SEVERITY_MAP.get(event_type, "low")
    # Create the event immediately without the screenshot so violation logging
    # never blocks on disk I/O; the (optional) screenshot is decoded and written
    # in a background task and attached to the event afterwards.
    event = proctor_repository.create_event(db, attempt_id, event_type, severity, description, None)

    if screenshot_base64:
        if background_tasks is not None:
            background_tasks.add_task(_save_violation_screenshot, event.id, attempt_id, screenshot_base64)
        else:
            _save_violation_screenshot(event.id, attempt_id, screenshot_base64)

    return event


MAX_SCREENSHOT_BYTES = 5 * 1024 * 1024


def _save_violation_screenshot(event_id: int, attempt_id: int, screenshot_base64: str) -> None:
    """Decode and persist a violation screenshot, then attach its path to the event.

    Runs as a FastAPI background task (or synchronously as a fallback), so it opens
    its own DB session rather than relying on the request-scoped one, which may
    already be closed by the time this executes.
    """
    try:
        img_data = screenshot_base64.split(",", 1)[1] if "," in screenshot_base64 else screenshot_base64
        raw = base64.b64decode(img_data, validate=True)
    except (ValueError, binascii.Error):
        logger.warning("Discarding invalid base64 screenshot for proctor event %s", event_id)
        return
    if len(raw) > MAX_SCREENSHOT_BYTES:
        logger.warning("Discarding oversized screenshot (%d bytes) for proctor event %s", len(raw), event_id)
        return

    try:
        os.makedirs(settings.VIOLATIONS_DIR, exist_ok=True)
        screenshot_path = f"{settings.VIOLATIONS_DIR}/attempt_{attempt_id}_{uuid.uuid4().hex[:8]}.jpg"
        with open(screenshot_path, "wb") as f:
            f.write(raw)
    except OSError:
        logger.exception("Failed to write violation screenshot to disk for event %s", event_id)
        return

    db = SessionLocal()
    try:
        proctor_repository.set_event_screenshot(db, event_id, screenshot_path)
    except Exception:
        logger.exception("Failed to persist screenshot path for proctor event %s", event_id)
        db.rollback()
    finally:
        db.close()
