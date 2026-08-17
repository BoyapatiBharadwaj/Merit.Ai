"""Face identity service."""
import json
import logging
import threading
from functools import lru_cache

import mediapipe as mp
import numpy as np
from fastapi import HTTPException, status
from PIL import Image

from app.ai import ai_worker_client
from app.ai.anti_spoof import assess_liveness
from app.ai.image_utils import decode_image
from app.core.config import settings

logger = logging.getLogger("app")

_mp_face_detection = mp.solutions.face_detection
_face_detector = None

# FastAPI runs each sync `def` route in its own worker thread, and this service's proctoring
# endpoints (face/register, face/verify) are hit repeatedly while an exam is in progress.
_face_detector_lock = threading.Lock()
_arcface_lock = threading.Lock()

# ArcFace embeddings are unit vectors, so cosine distance (1 - dot) lands in
# [0, 2]. Anything that is not 512 long did not come from this model.
ARCFACE_DIM = 512

# Minimum share of the frame a face must occupy, during REGISTRATION only, to be treated as a
# second person rather than a bystander in the background.
MIN_ENROLMENT_FACE_AREA = 0.02

LEGACY_PROFILE_MESSAGE = (
    "Your saved face profile was created with the previous face-recognition model and is no "
    "longer compatible. Please register your face again on your Profile page."
)


def _get_face_detector():
    global _face_detector
    if _face_detector is None:
        # min_detection_confidence lowered from 0.6: this same detector is
        # what counts *how many* faces are in frame, and a second person is
        # often partially out of frame, angled away, or worse-lit than the
        # primary face -- exactly the case a high confidence floor misses.
        _face_detector = _mp_face_detection.FaceDetection(model_selection=1, min_detection_confidence=0.5)
    return _face_detector


def _decode_base64_image(value: str) -> np.ndarray:
    return np.asarray(decode_image(value))


def _relative_detection_area(detection) -> float:
    """Fraction of the frame covered by one MediaPipe detection's bounding box."""
    try:
        box = detection.location_data.relative_bounding_box
        return max(float(box.width), 0.0) * max(float(box.height), 0.0)
    except (AttributeError, TypeError, ValueError):
        return 0.0


def _unavailable_detail(error: Exception) -> str:
    """Build an accurate 503 message."""
    reason = f"{type(error).__name__}: {error}".strip().splitlines()[0][:200]
    if isinstance(error, ImportError):
        remedy = "install them with `pip install insightface onnxruntime`"
    else:
        remedy = (f"check that the '{settings.FACE_MODEL_NAME}' weights can be downloaded to "
                  f"'{settings.FACE_MODEL_ROOT}', or run `python -m app.utils.fetch_face_model`")

    if settings.AI_SERVICE_URL:
        where = (f"the AI worker at {settings.AI_SERVICE_URL} did not respond, and the in-process "
                 f"ArcFace model could not be loaded")
    else:
        where = ("no AI worker is configured (AI_SERVICE_URL is empty), and the in-process ArcFace "
                 "model could not be loaded")
    return f"Face recognition is unavailable: {where} -- {reason}. To fix: {remedy}."


@lru_cache
def _arcface_app():
    """Load ArcFace once per process."""
    try:
        from insightface.app import FaceAnalysis

        model = FaceAnalysis(
            name=settings.FACE_MODEL_NAME,
            root=settings.FACE_MODEL_ROOT,
            providers=["CPUExecutionProvider"],
        )
        model.prepare(ctx_id=-1, det_size=(640, 640))
        return model
    except Exception as error:
        logger.exception("Failed to load the local ArcFace model")
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, _unavailable_detail(error)) from error


def _insightface_bbox_area(face) -> float:
    """Pixel area of an InsightFace detection's bounding box ([x1, y1, x2, y2])."""
    x1, y1, x2, y2 = (float(v) for v in face.bbox[:4])
    return max(x2 - x1, 0.0) * max(y2 - y1, 0.0)


def _local_face_encoding(image: np.ndarray) -> tuple[int, np.ndarray | None]:
    """Run ArcFace locally. Returns (num_faces, 512-d unit vector | None)."""
    with _arcface_lock:
        faces = _arcface_app().get(image[:, :, ::-1])
    if not faces:
        return 0, None
    primary = max(faces, key=_insightface_bbox_area)
    return len(faces), np.asarray(primary.normed_embedding, dtype=np.float64)


def _cosine_distance(stored: np.ndarray, live: np.ndarray) -> float:
    """Distance in [0, 2] for unit vectors. Re-normalises defensively, so an
    embedding stored slightly off-unit (float rounding through JSON) cannot
    drift the score."""
    stored_norm = float(np.linalg.norm(stored)) or 1.0
    live_norm = float(np.linalg.norm(live)) or 1.0
    return float(1.0 - np.dot(stored / stored_norm, live / live_norm))


def _parse_stored_encoding(stored_encoding_json: str) -> np.ndarray | None:
    """Return the stored embedding, or None if it is unusable or legacy."""
    try:
        vector = np.asarray(json.loads(stored_encoding_json), dtype=np.float64)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if vector.ndim != 1 or vector.shape[0] != ARCFACE_DIM:
        # 128 long means a dlib/face_recognition profile from before ArcFace
        # became the only identity model.
        return None
    return vector


def register_face(base64_image: str, save_path: str) -> tuple[bool, str, str | None]:
    worker = ai_worker_client.register_face(base64_image)
    if worker is not None:
        if not worker.get("success"):
            return False, worker.get("message", "Face registration failed."), None
        Image.fromarray(_decode_base64_image(base64_image)).save(save_path, quality=90)
        return True, worker.get("message", "ArcFace profile created."), json.dumps(worker["encoding"])

    image = _decode_base64_image(base64_image)

    liveness = assess_liveness(image)
    # Only refuse when the check is authoritative.
    if not liveness["live"] and liveness.get("blocking", True):
        return False, f"Liveness check failed: {liveness['reason']}. Use your own live camera, not a photo or screen.", None
    if not liveness["live"]:
        logger.warning("Advisory liveness concern during face registration (score=%s): %s",
                       liveness.get("score"), liveness.get("reason"))

    with _face_detector_lock:
        detections = _get_face_detector().process(image).detections or []
    if not detections:
        return False, ("No face detected. Move closer so your face fills more of the frame, make sure "
                       "it is well lit, and look straight at the camera."), None

    # Only faces close to the lens count as "someone else is registering with you".
    prominent = [d for d in detections if _relative_detection_area(d) >= MIN_ENROLMENT_FACE_AREA]
    if len(prominent) > 1:
        return False, ("More than one person is close to the camera. Only you should be in frame when "
                       "registering your face."), None

    face_count, encoding = _local_face_encoding(image)
    if encoding is None:
        return False, ("No face could be read from this photo. Make sure your whole face is visible, "
                       "unobstructed, and facing the camera, then retake it."), None
    if face_count > 1:
        # Not a rejection: the prominence gate above already established that only one person is
        # actually at the camera, so the extra detections are background.
        logger.info("Face registration: %s faces in frame, enrolling the largest (others are background).",
                    face_count)

    Image.fromarray(image).save(save_path, quality=90)
    return True, "Face registered successfully.", json.dumps(encoding.astype(float).tolist())


def verify_live_frame(base64_image: str, stored_encoding_json: str, tolerance: float | None = None) -> dict:
    """`tolerance` is optional and defaults to `settings.FACE_MATCH_TOLERANCE`."""
    match_tolerance = settings.FACE_MATCH_TOLERANCE if tolerance is None else tolerance
    stored_vector = _parse_stored_encoding(stored_encoding_json)

    # Only ask the worker when there is a usable profile to compare against;
    # otherwise the round-trip is wasted and the answer is the same either way.
    if stored_vector is not None:
        worker = ai_worker_client.verify_face(
            base64_image, stored_vector.tolist(), settings.FACE_MATCH_TOLERANCE,
        )
        if worker is not None:
            return worker

    image = _decode_base64_image(base64_image)

    liveness = assess_liveness(image)
    # A blocking (trained-model) failure short-circuits: there is no point
    # matching a face we believe is a photograph.
    if not liveness["live"] and liveness.get("blocking", True):
        return {
            "face_count": 0, "match": False, "distance": None,
            "spoof_suspected": True, "liveness_score": liveness["score"],
            "message": f"Liveness check failed: {liveness['reason']}. Use your own live camera, not a photo or screen.",
        }

    # An ADVISORY (classical-heuristic) failure no longer sets
    # spoof_suspected, and this is the whole point of the flag.
    advisory_spoof = not liveness["live"]
    if advisory_spoof:
        logger.info(
            "Advisory liveness concern during verification (score=%s, source=%s): %s -- "
            "not raised as a violation; install a trained anti-spoofing model to enforce.",
            liveness.get("score"), liveness.get("source"), liveness.get("reason"),
        )

    # An unusable stored profile is reported after the liveness gate but before the (far more
    # expensive) identity model runs.
    if stored_vector is None:
        return {
            "face_count": 0, "match": False, "distance": None,
            "spoof_suspected": False, "liveness_score": liveness["score"],
            "message": LEGACY_PROFILE_MESSAGE,
        }

    with _face_detector_lock:
        detections = _get_face_detector().process(image).detections or []
    count = len(detections)
    if count != 1:
        return {
            "face_count": count, "match": None, "distance": None,
            "spoof_suspected": False, "liveness_score": liveness["score"],
            "message": "No single face detected.",
        }

    face_count, live_encoding = _local_face_encoding(image)
    if live_encoding is None:
        # MediaPipe saw exactly one face just above but ArcFace's own detector found none.
        return {
            "face_count": 0, "match": None, "distance": None,
            "spoof_suspected": False, "liveness_score": liveness["score"],
            "message": "No face could be read from this frame. Face the camera directly in good light.",
        }

    distance = _cosine_distance(stored_vector, live_encoding)
    matched = distance <= match_tolerance
    return {
        "face_count": 1, "match": matched, "distance": round(distance, 4),
        # False, always, on this path: a blocking failure already returned above, so anything
        # still here is at most an advisory concern, and advisory concerns do not accuse.
        "spoof_suspected": False, "liveness_score": liveness["score"],
        "message": ("Face matched." if matched else "Face does not match.") if not advisory_spoof
                   else f"Face {'matched' if matched else 'does not match'}, but the frame looks unusual: {liveness['reason']}",
    }
