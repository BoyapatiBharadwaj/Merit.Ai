"""
In-process object detection (phone / book / extra person) for proctoring.

WHY THIS EXISTS
---------------
Object detection used to live *only* in the optional ai_worker container. With
the Docker worker deferred (AI_SERVICE_URL blank -- see deferred-docker/README),
that meant phone, book and second-person detection were not merely degraded but
switched off completely: every /detect call returned "unavailable" and the
frontend stopped polling by design. Face matching had already been given an
in-process path for exactly this reason; this module closes the same gap for
object detection, so a default install actually proctors what it claims to.

MODEL
-----
YOLO11s (Ultralytics), the small tier: ~19MB of weights, meaningfully stronger
than the yolov8n the worker used, and still fast enough on CPU for a signal
that is polled every few seconds rather than every frame.

TWO EXECUTION PATHS, PREFERRED FIRST
------------------------------------
1. onnxruntime against an exported ``yolo11s.onnx``. Preferred because
   onnxruntime is *already* a hard dependency (ArcFace runs on it), so this
   path adds no new runtime requirement, and ONNX CPU inference is several
   times faster than the equivalent PyTorch graph.
2. ultralytics' own ``YOLO`` class, if the ONNX export is missing but the
   package is installed. Slower and drags in torch, but it self-downloads its
   weights, so a user who has not run the fetch script still gets working
   detection rather than a dead feature.

If neither is available the service reports ``available: False`` with an
actionable message -- the same contract proctor_service already relies on, so
the frontend degrades exactly as it does today rather than erroring.

THREAD SAFETY
-------------
FastAPI runs each sync route in its own worker thread and this endpoint is
polled throughout an exam, so two calls are routinely in flight at once. An
onnxruntime InferenceSession is documented as thread-safe for concurrent
``run()``; the ultralytics path is not, so it gets a lock. Loading is guarded
either way so a cold start cannot race two model loads.
"""
import logging
import threading
from pathlib import Path

import numpy as np

from app.core.config import settings

logger = logging.getLogger("app")

# COCO class ids for the three things worth flagging in an exam. Hard-coded
# rather than read from model metadata: these indices are fixed by the COCO
# dataset itself and every YOLO variant trained on it shares them, and pinning
# them means a model whose metadata is missing or malformed still detects the
# right things instead of silently matching nothing.
COCO_PERSON = 0
COCO_BOOK = 73
COCO_CELL_PHONE = 67

CLASS_LABELS = {
    COCO_PERSON: "person",
    COCO_CELL_PHONE: "cell phone",
    COCO_BOOK: "book",
}

# Per-class confidence floors. A phone is the highest-value catch and the
# easiest to hide (edge-on in a lap, half under a desk), so it gets the most
# permissive floor. "book" is the noisiest class in COCO -- it fires on
# laptops, keyboards, folded paper and monitor bezels -- so it needs the
# strictest floor to stay useful rather than crying wolf every poll.
CONFIDENCE_FLOORS = {
    "cell phone": 0.35,
    "person": 0.45,
    "book": 0.55,
}
# Anything below the lowest floor is discarded before per-class filtering.
MIN_CONFIDENCE = min(CONFIDENCE_FLOORS.values())
NMS_IOU_THRESHOLD = 0.45
INPUT_SIZE = 640

_session = None
_session_lock = threading.Lock()
_ultralytics_model = None
_ultralytics_lock = threading.Lock()
_load_failure: str | None = None


def _onnx_path() -> Path:
    return Path(settings.OBJECT_MODEL_ROOT) / settings.OBJECT_MODEL_ONNX


def _load_onnx_session():
    """Return a cached InferenceSession, or None if the export isn't present."""
    global _session
    if _session is not None:
        return _session
    path = _onnx_path()
    if not path.is_file():
        return None
    with _session_lock:
        if _session is not None:  # another thread won the race while we waited
            return _session
        import onnxruntime as ort

        options = ort.SessionOptions()
        # One intra-op thread per session keeps a burst of concurrent polls
        # from oversubscribing the CPU and starving the request handlers --
        # this is a background signal, not the thing the user is waiting on.
        options.intra_op_num_threads = 1
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        _session = ort.InferenceSession(str(path), options, providers=["CPUExecutionProvider"])
        logger.info("Loaded ONNX object-detection model from %s", path)
    return _session


def _load_ultralytics():
    """Fallback path: ultralytics' own loader, which self-downloads weights."""
    global _ultralytics_model
    if _ultralytics_model is not None:
        return _ultralytics_model
    with _ultralytics_lock:
        if _ultralytics_model is not None:
            return _ultralytics_model
        from ultralytics import YOLO

        weights = Path(settings.OBJECT_MODEL_ROOT) / settings.OBJECT_MODEL_WEIGHTS
        # Pass the cached path when we have it so repeat loads are offline;
        # otherwise pass the bare name and let ultralytics fetch it.
        _ultralytics_model = YOLO(str(weights) if weights.is_file() else settings.OBJECT_MODEL_WEIGHTS)
        logger.info("Loaded ultralytics object-detection model (%s)", settings.OBJECT_MODEL_WEIGHTS)
    return _ultralytics_model


def _letterbox(image: np.ndarray, size: int = INPUT_SIZE) -> tuple[np.ndarray, float, int, int]:
    """Resize preserving aspect ratio and pad to a square, YOLO-style.

    Returns the padded image plus the scale and padding actually applied, which
    the caller needs to map boxes back to original-image coordinates. Squashing
    to a square instead (the naive resize) distorts every object and measurably
    costs recall on the thin, elongated shapes that matter most here -- a phone
    seen edge-on being the obvious one.
    """
    height, width = image.shape[:2]
    scale = min(size / height, size / width)
    new_h, new_w = int(round(height * scale)), int(round(width * scale))

    # Nearest-neighbour via index arithmetic: avoids a hard dependency on
    # cv2/PIL resize semantics inside the hot path, and at 640px the
    # interpolation choice is not what limits detection quality.
    rows = (np.arange(new_h) / scale).astype(np.int32).clip(0, height - 1)
    cols = (np.arange(new_w) / scale).astype(np.int32).clip(0, width - 1)
    resized = image[rows][:, cols]

    canvas = np.full((size, size, 3), 114, dtype=np.uint8)  # 114 = YOLO's pad grey
    pad_y, pad_x = (size - new_h) // 2, (size - new_w) // 2
    canvas[pad_y:pad_y + new_h, pad_x:pad_x + new_w] = resized
    return canvas, scale, pad_x, pad_y


def _nms(boxes: np.ndarray, scores: np.ndarray, iou_threshold: float) -> list[int]:
    """Greedy non-maximum suppression over xyxy boxes. Returns kept indices.

    Written out rather than pulled from torchvision/cv2 so this module stays
    on numpy alone -- the whole point of the ONNX path is not needing torch.
    """
    if len(boxes) == 0:
        return []
    x1, y1, x2, y2 = boxes[:, 0], boxes[:, 1], boxes[:, 2], boxes[:, 3]
    areas = np.maximum(0.0, x2 - x1) * np.maximum(0.0, y2 - y1)
    order = scores.argsort()[::-1]

    kept = []
    while order.size > 0:
        best = order[0]
        kept.append(int(best))
        if order.size == 1:
            break
        rest = order[1:]
        inter_w = np.maximum(0.0, np.minimum(x2[best], x2[rest]) - np.maximum(x1[best], x1[rest]))
        inter_h = np.maximum(0.0, np.minimum(y2[best], y2[rest]) - np.maximum(y1[best], y1[rest]))
        intersection = inter_w * inter_h
        union = areas[best] + areas[rest] - intersection
        iou = np.where(union > 0, intersection / np.maximum(union, 1e-9), 0.0)
        order = rest[iou <= iou_threshold]
    return kept


def _decode(output: np.ndarray, scale: float, pad_x: int, pad_y: int) -> list[dict]:
    """Turn a raw YOLO11 head tensor into a list of {label, confidence}.

    YOLO11 (like v8) is anchor-free and NMS-free in the graph: the single
    output is (1, 4 + num_classes, num_predictions) holding cx, cy, w, h
    followed by one sigmoid score per class -- there is no separate
    objectness channel to multiply in, which is the classic mistake when
    porting v5-era post-processing. We transpose to (num_predictions, 4 +
    num_classes) and reduce over the class axis.
    """
    predictions = np.squeeze(output, axis=0).T  # -> (num_predictions, 4 + num_classes)
    if predictions.ndim != 2 or predictions.shape[1] <= 4:
        return []

    class_scores = predictions[:, 4:]
    # Restrict to the three classes we act on *before* the argmax, so a
    # high-confidence "chair" can never mask a lower-confidence "cell phone"
    # sharing the same prediction slot.
    wanted = np.array(sorted(CLASS_LABELS), dtype=np.int32)
    wanted = wanted[wanted < class_scores.shape[1]]
    if wanted.size == 0:
        return []
    subset = class_scores[:, wanted]

    best_local = subset.argmax(axis=1)
    confidences = subset[np.arange(subset.shape[0]), best_local]
    class_ids = wanted[best_local]

    keep = confidences >= MIN_CONFIDENCE
    if not keep.any():
        return []
    boxes_cxcywh = predictions[keep, :4]
    confidences = confidences[keep]
    class_ids = class_ids[keep]

    # cxcywh (letterboxed 640-space) -> xyxy in original-image coordinates.
    cx, cy, w, h = boxes_cxcywh[:, 0], boxes_cxcywh[:, 1], boxes_cxcywh[:, 2], boxes_cxcywh[:, 3]
    boxes = np.stack([
        (cx - w / 2 - pad_x) / scale,
        (cy - h / 2 - pad_y) / scale,
        (cx + w / 2 - pad_x) / scale,
        (cy + h / 2 - pad_y) / scale,
    ], axis=1)

    detections = []
    # NMS per class: a person and the phone they are holding overlap heavily,
    # and suppressing across classes would drop exactly the detection we care
    # about most.
    for class_id in np.unique(class_ids):
        mask = class_ids == class_id
        label = CLASS_LABELS[int(class_id)]
        for index in _nms(boxes[mask], confidences[mask], NMS_IOU_THRESHOLD):
            confidence = float(confidences[mask][index])
            if confidence >= CONFIDENCE_FLOORS[label]:
                detections.append({"label": label, "confidence": round(confidence, 3)})
    detections.sort(key=lambda item: item["confidence"], reverse=True)
    return detections


def _detect_onnx(session, image: np.ndarray) -> list[dict]:
    padded, scale, pad_x, pad_y = _letterbox(image)
    # NCHW float32 in [0, 1], RGB -- the layout ultralytics exports expect.
    tensor = padded.transpose(2, 0, 1)[None].astype(np.float32) / 255.0
    output = session.run(None, {session.get_inputs()[0].name: tensor})[0]
    return _decode(np.asarray(output, dtype=np.float32), scale, pad_x, pad_y)


def _detect_ultralytics(model, image: np.ndarray) -> list[dict]:
    with _ultralytics_lock:
        results = model(image, verbose=False, conf=MIN_CONFIDENCE, iou=NMS_IOU_THRESHOLD)[0]
    detections = []
    for box in results.boxes:
        class_id = int(box.cls[0])
        label = CLASS_LABELS.get(class_id)
        if label is None:
            continue
        confidence = float(box.conf[0])
        if confidence >= CONFIDENCE_FLOORS[label]:
            detections.append({"label": label, "confidence": round(confidence, 3)})
    detections.sort(key=lambda item: item["confidence"], reverse=True)
    return detections


def is_available() -> bool:
    """True when a local detection path can actually run. Cheap: no inference."""
    if _session is not None or _ultralytics_model is not None:
        return True
    if _onnx_path().is_file():
        return True
    try:
        import ultralytics  # noqa: F401
    except ImportError:
        return False
    return True


def unavailable_message() -> str:
    return (
        f"Local object detection is not set up: no '{settings.OBJECT_MODEL_ONNX}' under "
        f"'{settings.OBJECT_MODEL_ROOT}' and the 'ultralytics' package is not installed. "
        "Run `python -m app.utils.fetch_models` to download the weights, or set "
        "AI_SERVICE_URL to use the ai_worker container instead."
    )


def detect(image: np.ndarray) -> dict:
    """Detect phones/books/people in an RGB uint8 frame.

    Never raises: a detection failure must not break an exam, so every error
    path degrades to the same "unavailable" contract the caller already
    handles. The reason is logged once rather than on every poll.
    """
    global _load_failure

    image = np.ascontiguousarray(image[:, :, :3], dtype=np.uint8)
    try:
        session = _load_onnx_session()
        if session is not None:
            detections = _detect_onnx(session, image)
        else:
            try:
                model = _load_ultralytics()
            except ImportError:
                return {"available": False, "detections": [], "person_count": 0,
                        "message": unavailable_message()}
            detections = _detect_ultralytics(model, image)
    except Exception as error:  # noqa: BLE001 -- deliberately broad, see docstring
        message = f"{type(error).__name__}: {error}".splitlines()[0][:200]
        if message != _load_failure:
            logger.exception("Local object detection failed")
            _load_failure = message
        return {"available": False, "detections": [], "person_count": 0,
                "message": f"Object detection failed locally ({message})."}

    _load_failure = None
    return {
        "available": True,
        "detections": detections,
        "person_count": sum(item["label"] == "person" for item in detections),
        "message": "ok",
    }


def warm_up() -> bool:
    """Load the model ahead of the first request. Returns True if ready."""
    try:
        if _load_onnx_session() is not None:
            return True
        _load_ultralytics()
        return True
    except Exception:
        logger.warning("Object-detection warm-up skipped: no local model available yet.")
        return False
