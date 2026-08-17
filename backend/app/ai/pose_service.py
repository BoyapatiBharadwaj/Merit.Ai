"""Head-pose and gaze-deviation estimation, purely local (no AI worker needed)."""
import threading
from functools import lru_cache

import mediapipe as mp
import numpy as np

from app.ai.image_utils import decode_image

_mp_face_mesh = mp.solutions.face_mesh

# See the matching comment in app/ai/face_service.py.
_face_mesh_lock = threading.Lock()

# Generic 3D face model (arbitrary units) and the
# corresponding MediaPipe Face Mesh landmark indices.
_MODEL_POINTS = np.array([
    (0.0, 0.0, 0.0),        # nose tip
    (0.0, -330.0, -65.0),   # chin
    (-225.0, 170.0, -135.0),  # left eye, left corner
    (225.0, 170.0, -135.0),   # right eye, right corner
    (-150.0, -150.0, -125.0),  # left mouth corner
    (150.0, -150.0, -125.0),   # right mouth corner
], dtype=np.float64)
_LANDMARK_INDICES = [1, 152, 33, 263, 61, 291]

# Refined-iris landmark indices (only present when refine_landmarks=True).
_LEFT_IRIS_CENTER, _RIGHT_IRIS_CENTER = 468, 473
_LEFT_EYE_CORNERS, _RIGHT_EYE_CORNERS = (33, 133), (362, 263)

# Raised from the original 25/20 degrees: pitch in particular kept firing on completely normal
# exam behaviour -- glancing down at a keyboard, a physical scratch pad, or the lower half of
# the screen easily exceeds 20 degrees of pitch for a second or two, which made "looking away"
# trigger on essentially every student, constantly, rather than on someone sustaining an actual
# turn toward a second screen or notes off to the side.
YAW_LOOKING_AWAY_DEG = 32.0
PITCH_LOOKING_AWAY_DEG = 28.0
GAZE_DEVIATION_RATIO = 0.35  # distance from center (0.5) that counts as "off to one side"


@lru_cache
def _get_face_mesh():
    return _mp_face_mesh.FaceMesh(
        static_image_mode=True, max_num_faces=1, refine_landmarks=True, min_detection_confidence=0.6,
    )


def _euler_angles_from_landmarks(landmarks, width: int, height: int) -> tuple[float, float, float] | None:
    try:
        import cv2
    except ImportError:
        return None

    image_points = np.array([
        (landmarks[i].x * width, landmarks[i].y * height) for i in _LANDMARK_INDICES
    ], dtype=np.float64)

    focal_length = width
    center = (width / 2, height / 2)
    camera_matrix = np.array([
        [focal_length, 0, center[0]],
        [0, focal_length, center[1]],
        [0, 0, 1],
    ], dtype=np.float64)
    dist_coeffs = np.zeros((4, 1))

    success, rotation_vector, _ = cv2.solvePnP(
        _MODEL_POINTS, image_points, camera_matrix, dist_coeffs, flags=cv2.SOLVEPNP_ITERATIVE,
    )
    if not success:
        return None

    rotation_matrix, _ = cv2.Rodrigues(rotation_vector)
    euler_angles, *_ = cv2.RQDecomp3x3(rotation_matrix)
    pitch, yaw, roll = (float(a) for a in euler_angles)

    # RQDecomp3x3 has a well-known ambiguity: a genuinely near-frontal pose
    # can decompose to pitch (or yaw) near +-180 instead of near 0, because R
    # and a ~180-degree-about-that-axis variant of R both satisfy the same
    # reprojection up to sign flips this decomposition doesn't resolve.
    def _fold(angle: float) -> float:
        if angle > 90:
            return angle - 180
        if angle < -90:
            return angle + 180
        return angle

    return _fold(pitch), _fold(yaw), roll


def _gaze_ratio(landmarks, width: int, height: int) -> float | None:
    def ratio_for(iris_idx, corner_indices):
        left_x = landmarks[corner_indices[0]].x * width
        right_x = landmarks[corner_indices[1]].x * width
        iris_x = landmarks[iris_idx].x * width
        span = right_x - left_x
        if abs(span) < 1e-6:
            return None
        return (iris_x - left_x) / span

    left_ratio = ratio_for(_LEFT_IRIS_CENTER, _LEFT_EYE_CORNERS)
    right_ratio = ratio_for(_RIGHT_IRIS_CENTER, _RIGHT_EYE_CORNERS)
    ratios = [r for r in (left_ratio, right_ratio) if r is not None]
    if not ratios:
        return None
    return sum(ratios) / len(ratios)


def analyze_frame(base64_image: str) -> dict:
    """Return head-pose + gaze signals for a single frame."""
    image = np.asarray(decode_image(base64_image))
    with _face_mesh_lock:
        result = _get_face_mesh().process(image)
    faces = result.multi_face_landmarks or []

    if len(faces) != 1:
        return {
            "available": True, "face_count": len(faces), "looking_away": None, "gaze_deviation": None,
            "yaw": None, "pitch": None, "gaze_ratio": None,
            "message": "No single face available for pose/gaze analysis.",
        }

    landmarks = faces[0].landmark
    height, width = image.shape[0], image.shape[1]

    angles = _euler_angles_from_landmarks(landmarks, width, height)
    if angles is None:
        return {
            "available": False, "face_count": 1, "looking_away": None, "gaze_deviation": None,
            "yaw": None, "pitch": None, "gaze_ratio": None,
            "message": "Head-pose estimation is unavailable on this server (OpenCV not installed).",
        }
    pitch, yaw, _roll = angles
    looking_away = abs(yaw) > YAW_LOOKING_AWAY_DEG or abs(pitch) > PITCH_LOOKING_AWAY_DEG

    gaze_ratio = _gaze_ratio(landmarks, width, height)
    gaze_deviation = None if gaze_ratio is None else abs(gaze_ratio - 0.5) > GAZE_DEVIATION_RATIO

    return {
        "available": True, "face_count": 1,
        "looking_away": looking_away, "gaze_deviation": gaze_deviation,
        "yaw": round(yaw, 1), "pitch": round(pitch, 1),
        "gaze_ratio": None if gaze_ratio is None else round(gaze_ratio, 2),
        "message": "ok",
    }
