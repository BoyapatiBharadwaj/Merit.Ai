"""
Face identity service.

Identity matching uses **ArcFace** (InsightFace) everywhere:

- Preferred: the ArcFace model served by the isolated ai_worker, when
  AI_SERVICE_URL is configured. Keeps the heavy model in its own container.
- Local: the same ArcFace model loaded in-process via onnxruntime, used when no
  AI worker is configured or the worker is unreachable.

Both paths therefore produce the **same 512-d L2-normalised embedding** and are
compared with the **same cosine distance**, so a profile registered against one
can be verified against the other. That was not true of the previous design,
which fell back to a dlib/`face_recognition` 128-d Euclidean embedding: a
student who registered while the worker was up and later verified while it was
down hit a shape mismatch and was told to register again. Dropping dlib also
drops the cmake/C++ toolchain requirement -- both `insightface` (>=1.0.1) and
`onnxruntime` ship prebuilt wheels.

MediaPipe Face Detection is still used, but only for the cheap, non-identity
task of counting how many faces are in a frame. MediaPipe Face Mesh landmarks
are never used for identity: that geometry encodes expression and pose far more
than it encodes who someone is.
"""
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

# FastAPI runs each sync `def` route in its own worker thread, and this
# service's proctoring endpoints (face/register, face/verify) are hit
# repeatedly while an exam is in progress -- often close enough together
# that two calls are in flight on different threads at once. MediaPipe
# Solution objects (FaceDetection here) are explicitly documented as not
# thread-safe for concurrent `.process()` calls on the *same instance*, and
# `_get_face_detector()` hands out one shared instance to every caller. Two
# overlapping `.process()` calls on it can hang inside MediaPipe's native
# graph rather than raise -- which then never frees the worker thread, and
# with enough of those the whole app stops responding to every user, not
# just this request. A lock serializes access to the one shared instance;
# ArcFace gets its own lock below for the same reason applied to its model.
_face_detector_lock = threading.Lock()
_arcface_lock = threading.Lock()

# ArcFace embeddings are unit vectors, so cosine distance (1 - dot) lands in
# [0, 2]. Anything that is not 512 long did not come from this model.
ARCFACE_DIM = 512

# Minimum share of the frame a face must occupy, during REGISTRATION only, to
# be treated as a second person rather than a bystander in the background.
#
# Calibrated against the case this exists for -- a candidate registering at a
# lab machine with classmates at desks behind them. The candidate's face is
# typically 8-12% of the frame area (a ~0.23 x 0.40 relative bounding box);
# someone seated a few metres back is 0.2-0.5%. 2% sits an order of magnitude
# clear of the background faces and well below the subject, so the separation
# does not depend on the threshold being finely tuned.
#
# This is NOT a relaxation of proctoring: it applies only to enrolment, where
# the question is "whose face do we store". During an exam, `verify_live_frame`
# still reports every detected face in `face_count`, and the browser-side
# tracker (frontend/src/lib/faceMesh.js) still raises a multiple-faces
# violation on any extra face regardless of how small it is.
MIN_ENROLMENT_FACE_AREA = 0.02

LEGACY_PROFILE_MESSAGE = (
    "Your saved face profile was created with the previous face-recognition model and is no "
    "longer compatible. Please register your face again on your Profile page."
)


def _get_face_detector():
    global _face_detector
    if _face_detector is None:
        # min_detection_confidence lowered from 0.6: this same detector is what
        # counts *how many* faces are in frame, and a second person is often
        # partially out of frame, angled away, or worse-lit than the primary
        # face -- exactly the case a high confidence floor misses. For "is
        # someone else in this frame" the cost of a false miss (a real second
        # person going unflagged) is worse than the cost of an occasional false
        # positive, so this is deliberately biased toward catching more faces.
        _face_detector = _mp_face_detection.FaceDetection(model_selection=1, min_detection_confidence=0.5)
    return _face_detector


def _decode_base64_image(value: str) -> np.ndarray:
    return np.asarray(decode_image(value))


def _relative_detection_area(detection) -> float:
    """Fraction of the frame covered by one MediaPipe detection's bounding box.

    MediaPipe reports the box in relative coordinates already (width and height
    as fractions of the image), so the area fraction is just their product --
    no frame dimensions needed, which also makes this resolution-independent.
    Returns 0.0 for a detection missing the box rather than raising: a
    malformed detection should be treated as "not prominent", never as a reason
    to fail a registration.
    """
    try:
        box = detection.location_data.relative_bounding_box
        return max(float(box.width), 0.0) * max(float(box.height), 0.0)
    except (AttributeError, TypeError, ValueError):
        return 0.0


def _unavailable_detail(error: Exception) -> str:
    """Build an accurate 503 message.

    Two failure modes get confused constantly, so they are named separately:
    "AI_SERVICE_URL isn't set" and "AI_SERVICE_URL is set but the worker didn't
    answer" need completely different fixes, and the previous wording claimed
    the former even when the latter was true.

    The underlying reason is included because the realistic reader of this
    string is whoever is standing up the server -- a missing package and a
    blocked model download look identical otherwise. It is one truncated line,
    never a traceback; the full trace goes to the log.
    """
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
    """Load ArcFace once per process.

    Lazy and memoised for the same reason the EasyOCR reader is (see
    ocr_service): importing this module must never pull in onnxruntime or
    trigger a model-weight download, and a missing or broken install should
    surface as a clean 503 rather than killing the app at import time.

    Weights (~280MB for buffalo_l, ~16MB for buffalo_s) are fetched from GitHub
    releases on first use into FACE_MODEL_ROOT. That makes the first
    registration after a cold start slow, and makes it fail outright on a host
    that cannot reach GitHub -- so `app/utils/fetch_face_model.py` exists to do
    the download deliberately, at deploy time, instead of inside a student's
    request. The ai_worker solves the same problem by warming up in a
    background thread at container boot.
    """
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
    """Run ArcFace locally. Returns (num_faces, 512-d unit vector | None).

    insightface expects BGR, hence the channel reversal -- the same convention
    the ai_worker uses, so both produce identical embeddings for a given frame.

    When several faces are detected the **largest** one is embedded rather than
    refusing to embed anything. This used to `return len(faces), None` for any
    count other than exactly one, which had a failure mode that made the
    product unusable in the environment it is actually deployed in:

    Registration ran two different detectors in sequence -- MediaPipe first (to
    gate on face count), then InsightFace's own RetinaFace inside `.get()`.
    Those two do not agree on small, badly-lit, off-angle faces, and
    RetinaFace at det_size=(640, 640) is markedly better at exactly that. In a
    computer lab or classroom -- i.e. every real registration -- MediaPipe sees
    one face (the student at the camera) and passes the gate, RetinaFace sees
    that face *plus* four classmates at desks behind, `len(faces) != 1` fires,
    and the student is told "Could not compute a face profile. Try better
    lighting and face the camera directly." That message is wrong twice over:
    the lighting was fine and there was nothing wrong with the photo, so the
    advice it gives can never fix it, and retaking in the same room fails
    identically forever.

    Choosing the largest face is both the fix and the correct semantics: the
    person enrolling is the one at the camera, and a bystander three metres
    back is not a candidate for whose identity to store. The true count is
    still returned so callers can apply their own policy -- `register_face`
    rejects a genuine second person close to the lens, and exam-time
    verification still reports `face_count` so a multiple-faces violation is
    raised exactly as before. Nothing about the strictness of proctoring
    changes here; only the ability to enrol at all.
    """
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
    # Only refuse when the check is authoritative. The classical detector is
    # advisory (see anti_spoof.CLASSICAL_IS_ADVISORY) because a false positive
    # here is the worst possible one: it stops a legitimate student
    # registering at all, and therefore from sitting any exam. A suspicious
    # registration is logged and still has to get past ArcFace matching and
    # live proctoring afterwards.
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

    # Only faces close to the lens count as "someone else is registering with
    # you". `_get_face_detector()` deliberately runs at a low confidence floor
    # because its other job is catching a second person during an exam, where a
    # false miss is the expensive error -- so at registration time it routinely
    # returns bystanders sitting well behind the candidate. Counting those as
    # co-subjects is what made enrolment impossible in a shared room.
    prominent = [d for d in detections if _relative_detection_area(d) >= MIN_ENROLMENT_FACE_AREA]
    if len(prominent) > 1:
        return False, ("More than one person is close to the camera. Only you should be in frame when "
                       "registering your face."), None

    face_count, encoding = _local_face_encoding(image)
    if encoding is None:
        return False, ("No face could be read from this photo. Make sure your whole face is visible, "
                       "unobstructed, and facing the camera, then retake it."), None
    if face_count > 1:
        # Not a rejection: the prominence gate above already established that
        # only one person is actually at the camera, so the extra detections
        # are background. Logged because "which face did we enrol?" is the
        # first question worth answering if a match later goes wrong.
        logger.info("Face registration: %s faces in frame, enrolling the largest (others are background).",
                    face_count)

    Image.fromarray(image).save(save_path, quality=90)
    return True, "Face registered successfully.", json.dumps(encoding.astype(float).tolist())


def verify_live_frame(base64_image: str, stored_encoding_json: str, tolerance: float | None = None) -> dict:
    """`tolerance` is optional and defaults to `settings.FACE_MATCH_TOLERANCE` --
    every existing caller (proctor_service, the ai_worker microservice's own
    /face/verify handler when it runs this same "local" branch, every test)
    omits it and gets today's behavior unchanged. It exists so the ai_worker
    process -- a separate container with its own settings object -- can be
    told the *core API's* configured threshold explicitly (the value
    app.ai.ai_worker_client.verify_face already sends over the wire) rather
    than relying on both services' env files being kept in sync by hand."""
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
    #
    # It used to. The frontend treats spoof_suspected as "log a
    # spoof_detected violation and toast the student", and face verification
    # polls every few seconds -- so on any deployment without a trained model
    # installed (which is the default: fetch_antispoof deliberately ships no
    # weights), a heuristic that the module's own docstring admits already
    # false-positived on the first real face it ever saw got to accuse a
    # legitimate student every few seconds for the length of the exam. That
    # is not an advisory signal, it is an alarm with extra steps, and it
    # trains examiners to ignore the one flag that should mean something.
    #
    # The heuristic's opinion is still recorded -- liveness_score rides along
    # on every response, and a concern is logged server-side for anyone
    # investigating an attempt afterwards -- it simply no longer raises a
    # violation on its own. Only a trained model, which is trusted enough to
    # block, may accuse. Install one (fetch_models --only antispoof) to get
    # an enforcing gate.
    advisory_spoof = not liveness["live"]
    if advisory_spoof:
        logger.info(
            "Advisory liveness concern during verification (score=%s, source=%s): %s -- "
            "not raised as a violation; install a trained anti-spoofing model to enforce.",
            liveness.get("score"), liveness.get("source"), liveness.get("reason"),
        )

    # An unusable stored profile is reported after the liveness gate but before
    # the (far more expensive) identity model runs -- there is nothing to
    # compare against, so computing an embedding would be wasted work.
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
        # MediaPipe saw exactly one face just above but ArcFace's own detector
        # found none. Reported as face_count 0 with an accurate message rather
        # than the old "could not compute a face profile", which read as an
        # internal fault and gave the student nothing actionable; the two
        # detectors disagreeing means the face is marginal (turned away, half
        # out of frame, very dim), and that is what the student needs told.
        return {
            "face_count": 0, "match": None, "distance": None,
            "spoof_suspected": False, "liveness_score": liveness["score"],
            "message": "No face could be read from this frame. Face the camera directly in good light.",
        }

    distance = _cosine_distance(stored_vector, live_encoding)
    matched = distance <= match_tolerance
    return {
        "face_count": 1, "match": matched, "distance": round(distance, 4),
        # False, always, on this path: a blocking failure already returned
        # above, so anything still here is at most an advisory concern, and
        # advisory concerns do not accuse. See the comment on advisory_spoof.
        "spoof_suspected": False, "liveness_score": liveness["score"],
        "message": ("Face matched." if matched else "Face does not match.") if not advisory_spoof
                   else f"Face {'matched' if matched else 'does not match'}, but the frame looks unusual: {liveness['reason']}",
    }
