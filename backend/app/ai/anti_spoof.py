"""Liveness / anti-spoofing: is this a real person in front of the camera, or a photo, printout,
or phone/monitor screen held up to it?
"""
import logging
import threading
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

from app.core.config import settings

logger = logging.getLogger("app")

_LAPLACIAN_KERNEL = ImageFilter.Kernel((3, 3), [0, 1, 0, 1, -4, 1, 0, 1, 0], scale=1)

# Analysis window: the centre square of the frame, at native resolution, capped for cost.
PATCH_FRACTION = 0.7
PATCH_MAX = 320
PATCH_MIN = 96

# --- classical cue calibration ---
# Soft reference points, not pass/fail gates: each cue is scaled to 0-1 between its "clean" and
# "suspicious" ends and the results are combined.

# Whitened periodicity peak (see _moire_score). ~1 means "no
# bin stands out from its neighbours at the same frequency".
MOIRE_CLEAN = 4.0                # <= this: nothing periodic worth reporting
MOIRE_SCREEN = 8.0               # >= this: a strong, unambiguous grid
# Measured: the real student frame that triggered the false positive scores 3.3, and spectrally
# realistic synthetic frames (grainy, soft, dim) span 2.9-3.5.

MOIRE_VETO_TERM = 0.5
MOIRE_VETO_SCORE_CAP = 0.35

# Laplacian variance of a real, in-focus webcam face measured over the whole centre window.
SHARPNESS_CLEAN = 6.0
SATURATION_REFERENCE = 8.0       # saturation spread of real skin under normal light
HIGHLIGHT_REFERENCE = 0.12       # fraction of near-blown pixels before glare is suspicious

# Below both of these a frame carries no usable detail at all:
# a blank wall, a lens cap, a heavily over-exposed print.
DEGENERATE_SHARPNESS = 0.8
DEGENERATE_SATURATION = 1.0
DEGENERATE_SCORE_CAP = 0.15

LIVE_SCORE_THRESHOLD = 0.5

# The classical detector reports, it does not decide. It is a hand-written heuristic, and it has
# already produced a false positive on the first real face it ever saw.
CLASSICAL_IS_ADVISORY = True


_onnx_session = None
_onnx_lock = threading.Lock()
_onnx_checked = False


# --- - ---
# Trained model -------------------------------------------------------------------------

def _model_path() -> Path:
    return Path(settings.ANTISPOOF_MODEL_ROOT) / settings.ANTISPOOF_MODEL_ONNX


def _load_model():
    """Return a cached InferenceSession, or None when no model is installed."""
    global _onnx_session, _onnx_checked
    if _onnx_checked:
        return _onnx_session
    with _onnx_lock:
        if _onnx_checked:
            return _onnx_session
        path = _model_path()
        if path.is_file():
            try:
                import onnxruntime as ort

                options = ort.SessionOptions()
                options.intra_op_num_threads = 1
                _onnx_session = ort.InferenceSession(
                    str(path), options, providers=["CPUExecutionProvider"])
                logger.info("Loaded anti-spoofing model from %s", path)
            except Exception:
                logger.exception("Anti-spoofing model at %s could not be loaded; "
                                 "falling back to the classical detector", path)
                _onnx_session = None
        _onnx_checked = True
    return _onnx_session


def reset_model_cache() -> None:
    """Forget the cached model decision. Used by tests and by fetch_models
    after a download, so a newly installed model is picked up without a
    restart."""
    global _onnx_session, _onnx_checked
    with _onnx_lock:
        _onnx_session = None
        _onnx_checked = False


def _model_liveness(session, face: np.ndarray) -> dict | None:
    """Run the ONNX classifier. Returns None if its output isn't interpretable,
    so an unexpected model shape degrades to the classical path rather than
    letting everyone through (or blocking everyone)."""
    spec = session.get_inputs()[0]
    # Trust the model's own declared spatial size when it is static; MiniFASNet
    # variants are commonly 80x80 while others use 128x128 or 224x224.
    shape = [d if isinstance(d, int) and d > 0 else None for d in spec.shape]
    height = shape[2] or 80
    width = shape[3] or 80

    patch = np.asarray(Image.fromarray(face).resize((width, height)), dtype=np.float32) / 255.0
    tensor = patch.transpose(2, 0, 1)[None]

    raw = session.run(None, {spec.name: tensor})[0]
    scores = np.asarray(raw, dtype=np.float64).reshape(-1)
    if scores.size == 0:
        return None

    if scores.size == 1:
        # Single logit/probability: interpret as P(live).
        live_probability = float(scores[0])
        if not 0.0 <= live_probability <= 1.0:
            live_probability = float(1.0 / (1.0 + np.exp(-live_probability)))
    else:
        # Multi-class head. MiniFASNet's 3-class layout is [spoof/print, live, spoof/replay];
        # 2-class exports are [spoof, live].
        shifted = scores - scores.max()
        exponentials = np.exp(shifted)
        probabilities = exponentials / exponentials.sum()
        live_probability = float(probabilities[1])

    live = live_probability >= settings.ANTISPOOF_LIVE_THRESHOLD
    return {
        "live": live,
        "score": round(live_probability, 3),
        "reason": ("Liveness checks passed." if live else
                   "Trained liveness model flagged this frame as a photo or screen replay"),
        "source": "model",
        # A trained model is trusted enough to refuse, unlike the heuristic.
        "blocking": True,
    }


# --- - ---
# Classical cues -------------------------------------------------------------------------

def _centre_face_patch(image: np.ndarray) -> np.ndarray:
    """Crop the centre square of the frame at the camera's own resolution."""
    height, width = image.shape[:2]
    side = int(min(height, width) * PATCH_FRACTION)
    side = max(PATCH_MIN, min(side, PATCH_MAX))
    side = min(side, height, width)

    top, left = max(0, (height - side) // 2), max(0, (width - side) // 2)
    crop = image[top:top + side, left:left + side]
    if crop.shape[0] < PATCH_MIN or crop.shape[1] < PATCH_MIN:
        return np.asarray(Image.fromarray(crop).convert("RGB").resize((PATCH_MIN, PATCH_MIN)))
    return np.ascontiguousarray(crop)


def _moire_score(gray: np.ndarray) -> float:
    """How much *periodic* structure the patch has, above its own 1/f envelope."""
    windowed = gray.astype(np.float64)
    windowed -= windowed.mean()
    # Hann window in both axes: without it the image borders act as a step
    # discontinuity and smear a cross of spurious energy through the spectrum,
    # which reads as exactly the kind of spike we are trying to detect.
    window = np.hanning(windowed.shape[0])[:, None] * np.hanning(windowed.shape[1])[None, :]
    spectrum = np.abs(np.fft.fftshift(np.fft.fft2(windowed * window)))

    size = min(spectrum.shape)
    centre_y, centre_x = np.array(spectrum.shape) // 2
    ys, xs = np.ogrid[:spectrum.shape[0], :spectrum.shape[1]]
    radius_exact = np.sqrt((ys - centre_y) ** 2 + (xs - centre_x) ** 2)
    radius = radius_exact.astype(np.int32)

    # Mean magnitude per integer radius = the 1/f envelope of this image.
    bins = radius.max() + 1
    totals = np.bincount(radius.ravel(), spectrum.ravel(), minlength=bins)
    counts = np.bincount(radius.ravel(), minlength=bins)
    envelope = totals / np.maximum(counts, 1)

    whitened = spectrum / np.maximum(envelope[radius], 1e-9)

    # Band runs to Nyquist so the finest panel grids are included, and skips
    # the lowest frequencies, which carry brightness and large-scale shading.
    band = (radius_exact > size * 0.08) & (radius_exact <= size * 0.5)
    values = whitened[band]
    if values.size == 0:
        return 1.0
    # 99.9th percentile rather than the raw max: one hot pixel or compression artefact should
    # not on its own look like a display panel.
    return float(np.percentile(values, 99.9))


def _ramp(value: float, reference: float) -> float:
    """Map "more is better" measurements to 0-1, saturating at the reference."""
    return float(np.clip(value / reference, 0.0, 1.0))


def _inverse_ramp(value: float, clean: float, suspicious: float) -> float:
    """Map "more is worse" measurements to 0-1: 1.0 at or below `clean`,
    0.0 at or above `suspicious`, linear in between."""
    if suspicious <= clean:
        return 1.0
    return float(np.clip((suspicious - value) / (suspicious - clean), 0.0, 1.0))


def _classical_liveness(face: np.ndarray) -> dict:
    luma = Image.fromarray(face).convert("L")
    gray = np.asarray(luma, dtype=np.float32)

    # PIL's Kernel filter leaves the outermost 1px ring *unfiltered*.
    edges = np.asarray(luma.filter(_LAPLACIAN_KERNEL), dtype=np.float32)[1:-1, 1:-1]
    sharpness = float(edges.var())

    hsv = np.asarray(Image.fromarray(face).convert("HSV"), dtype=np.float32)
    saturation_std = float(hsv[:, :, 1].std())
    highlight_ratio = float((hsv[:, :, 2] > 245).mean())

    moire = _moire_score(gray)

    # Each term is 0-1, where 1 means "this cue looks live".
    moire_term = _inverse_ramp(moire, MOIRE_CLEAN, MOIRE_SCREEN)
    texture_term = _ramp(sharpness, SHARPNESS_CLEAN)
    colour_term = _ramp(saturation_std, SATURATION_REFERENCE)
    glare_term = _inverse_ramp(highlight_ratio, HIGHLIGHT_REFERENCE, 2.0 * HIGHLIGHT_REFERENCE)

    # Moire carries the most weight because it is the most *specific* cue.
    score = (0.45 * moire_term + 0.30 * texture_term + 0.15 * colour_term + 0.10 * glare_term)

    reasons = []
    if moire_term < 0.5:
        reasons.append("periodic screen-pixel pattern detected (likely a display replay)")
    if texture_term < 0.35:
        reasons.append("low texture detail (possible printed photo or low-quality replay)")
    if colour_term < 0.35:
        reasons.append("flat colour distribution (possible print or screen capture)")
    if glare_term < 0.35:
        reasons.append("excess specular glare (possible screen reflection)")

    if moire_term < MOIRE_VETO_TERM:
        score = min(score, MOIRE_VETO_SCORE_CAP)

    # A frame with no usable detail cannot be a live
    # face no matter how the weighted terms fall out.
    if sharpness < DEGENERATE_SHARPNESS and saturation_std < DEGENERATE_SATURATION:
        score = min(score, DEGENERATE_SCORE_CAP)
        reasons.append("no detectable facial detail (blank, blocked, or badly out-of-focus frame)")

    live = score >= LIVE_SCORE_THRESHOLD
    return {
        "live": live,
        "score": round(score, 3),
        "reason": "Liveness checks passed." if live else "; ".join(reasons) or "overall liveness score too low",
        "source": "classical",
        # Advisory: callers log and warn on a failure here, but do not refuse.
        # See CLASSICAL_IS_ADVISORY.
        "blocking": not CLASSICAL_IS_ADVISORY,
    }


# --- - ---
# Public entry point -------------------------------------------------------------------------

def assess_liveness(image: np.ndarray) -> dict:
    """Return {"live": bool, "score": float 0-1, "reason": str, "source": str} for an RGB image array."""
    try:
        face = _centre_face_patch(np.asarray(image)[:, :, :3])
        session = _load_model()
        if session is not None:
            # The model call is wrapped separately from the outer handler on purpose.
            try:
                result = _model_liveness(session, face)
            except Exception:
                logger.exception("Anti-spoofing model failed; using the classical detector")
                result = None
            if result is not None:
                return result
            logger.warning("Anti-spoofing model returned an uninterpretable output; "
                           "using the classical detector for this frame")
        return _classical_liveness(face)
    except Exception:
        logger.exception("Liveness assessment failed")
        return {"live": True, "score": 0.5, "reason": "Liveness check unavailable.",
                "source": "unavailable", "blocking": False}
