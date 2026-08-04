"""
Unit tests for the AI/OCR helper modules: shared image validation, the
liveness/anti-spoof heuristic, severity mapping, and the worker-vs-in-process
model selection in face_service.

Both face paths run ArcFace, so these also assert the property that makes that
worth doing: an embedding produced by one path verifies against the other.
Everything is mocked, so no GPU, no network, and no model download is needed.
"""
import base64
import io
import json

import numpy as np
import pytest
from fastapi import HTTPException
from PIL import Image

from app.ai import anti_spoof, face_service, image_utils, ocr_service
from app.services import proctor_service
from app.services.proctor_service import SEVERITY_MAP


def _b64_png(size=(64, 64), color=(120, 120, 120)):
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


def _textured_but_realistic_image():
    """A synthetic frame with fine texture and correlated RGB channels (like a
    real webcam capture), as opposed to fully independent per-channel noise
    which spuriously trips the specular-highlight check."""
    rng = np.random.default_rng(42)
    luminance = rng.integers(60, 200, size=(256, 256), dtype=np.uint8)
    tint = rng.integers(-15, 15, size=(256, 256, 3))
    return np.clip(luminance[..., None].astype(int) + tint, 0, 254).astype(np.uint8)


def test_decode_image_accepts_valid_png():
    image = image_utils.decode_image(_b64_png())
    assert image.mode == "RGB"
    assert image.size == (64, 64)


def test_decode_image_strips_data_url_prefix():
    image = image_utils.decode_image("data:image/png;base64," + _b64_png())
    assert image.size == (64, 64)


def test_decode_image_rejects_invalid_base64():
    with pytest.raises(HTTPException) as exc:
        image_utils.decode_image("not-valid-base64!!!")
    assert exc.value.status_code == 400


def test_decode_image_rejects_corrupted_payload():
    garbage = base64.b64encode(b"this is not an image").decode()
    with pytest.raises(HTTPException) as exc:
        image_utils.decode_image(garbage)
    assert exc.value.status_code == 400


def test_decode_image_rejects_oversized_payload():
    oversized = base64.b64encode(b"0" * (image_utils.MAX_IMAGE_BYTES + 1)).decode()
    with pytest.raises(HTTPException) as exc:
        image_utils.decode_image(oversized)
    assert exc.value.status_code == 413


def test_anti_spoof_flags_flat_solid_color_image_as_not_live():
    # A perfectly flat, textureless, unsaturated frame is exactly the signature
    # of a poor-quality printed-photo or screen re-capture.
    flat_image = np.full((256, 256, 3), 200, dtype=np.uint8)
    result = anti_spoof.assess_liveness(flat_image)
    assert result["live"] is False
    assert result["score"] < 1.0
    assert result["reason"]


def test_anti_spoof_accepts_textured_realistic_image():
    result = anti_spoof.assess_liveness(_textured_but_realistic_image())
    assert result["live"] is True
    assert result["score"] == 1.0


def test_anti_spoof_never_raises_on_bad_input():
    # Fails open rather than crashing the calling request when given input that
    # can't even be turned into an image (as opposed to a degenerate-but-valid
    # image, which is legitimately scored as "not live").
    result = anti_spoof.assess_liveness("not an image array")
    assert result["live"] is True
    assert result["reason"] == "Liveness check unavailable."


def _stub_single_face_identity(monkeypatch):
    """Drive verify_live_frame past detection and ArcFace to its final return
    with a clean identity match, without loading any model."""
    class _Detections:
        detections = [object()]

    class _Detector:
        def process(self, _image):
            return _Detections()

    monkeypatch.setattr(face_service, "_get_face_detector", lambda: _Detector())
    # Identical to the stored vector below, so the cosine distance is 0 and
    # the frame matches -- isolating the spoof flag as the thing under test.
    monkeypatch.setattr(face_service, "_local_face_encoding",
                        lambda _image: (1, np.full(face_service.ARCFACE_DIM, 0.1)))


def test_an_advisory_classical_liveness_failure_never_accuses_the_student(monkeypatch):
    """The classical heuristic reports; it does not accuse.

    Regression test for a real false-positive complaint: verify_live_face
    used to copy an advisory (non-blocking) liveness failure straight into
    spoof_suspected, which the frontend turns into a spoof_detected violation
    plus a toast. Face verification polls throughout the exam, and no trained
    model ships by default, so a heuristic that the anti_spoof module's own
    docstring admits false-positived on the first real face it ever saw got
    to accuse a legitimate student every few seconds, all exam long.

    Only a *blocking* result (a trained model) may set spoof_suspected.
    """
    monkeypatch.setattr(face_service, "assess_liveness", lambda _image: {
        "live": False, "score": 0.31, "reason": "low texture detail",
        "source": "classical", "blocking": False,
    })
    _stub_single_face_identity(monkeypatch)

    # Must run all the way to the *final* return, past the identity match --
    # that is the line that used to copy the advisory verdict into
    # spoof_suspected. The early-exit paths already reported False, so a test
    # that stops short of here would pass with or without the fix.
    result = face_service.verify_live_frame(_b64_png(), json.dumps([0.1] * face_service.ARCFACE_DIM))
    assert result["match"] is True, "test should reach the identity-match path"
    assert result["spoof_suspected"] is False
    # The measurement itself is still reported, just not as an accusation,
    # and the message still tells a reviewer the frame looked unusual.
    assert result["liveness_score"] == 0.31
    assert "unusual" in result["message"]


def test_a_blocking_model_liveness_failure_still_accuses(monkeypatch):
    """The other half of the rule: a trained model is trusted enough to
    refuse, so its verdict must still short-circuit and flag."""
    monkeypatch.setattr(face_service, "assess_liveness", lambda _image: {
        "live": False, "score": 0.02, "reason": "screen replay detected",
        "source": "model", "blocking": True,
    })
    result = face_service.verify_live_frame(_b64_png(), "not-a-usable-profile")
    assert result["spoof_suspected"] is True
    assert result["match"] is False


def test_ocr_normalize_strips_non_letters():
    assert ocr_service._normalize("Jane, D0e!! 2024") == "JANE DE"


def test_severity_map_flags_spoof_and_mismatch_as_high():
    assert SEVERITY_MAP["spoof_detected"] == "high"
    assert SEVERITY_MAP["face_mismatch"] == "high"
    assert SEVERITY_MAP.get("unknown_event_type", "low") == "low"


def test_severity_map_ranks_loud_noise_above_ordinary_noise():
    # Two-tier noise detection (see proctoring.js): sustained loud/voice audio
    # is logged as a distinct, more serious event than ordinary background
    # noise, and must actually rank higher, not just exist as a separate key.
    assert SEVERITY_MAP["noise_detected"] == "low"
    assert SEVERITY_MAP["noise_detected_loud"] == "high"


def test_register_face_uses_arcface_worker_when_configured(monkeypatch):
    monkeypatch.setattr(face_service.ai_worker_client, "register_face", lambda image: {
        "success": True, "message": "ArcFace profile created.", "encoding": [0.1, 0.2, 0.3],
    })
    ok, message, encoding_json = face_service.register_face(_b64_png(), "/tmp/does-not-matter.jpg")
    assert ok is True
    assert "ArcFace" in message
    assert encoding_json == "[0.1, 0.2, 0.3]"


def test_register_face_rejects_worker_failure_without_touching_local_model(monkeypatch):
    monkeypatch.setattr(face_service.ai_worker_client, "register_face", lambda image: {
        "success": False, "message": "Multiple faces detected.",
    })
    ok, message, encoding_json = face_service.register_face(_b64_png(), "/tmp/does-not-matter.jpg")
    assert ok is False
    assert encoding_json is None
    assert "multiple faces" in message.lower()


def _unit_embedding(seed: int = 0) -> np.ndarray:
    """A deterministic 512-d unit vector, the shape ArcFace actually returns."""
    rng = np.random.default_rng(seed)
    vector = rng.normal(size=face_service.ARCFACE_DIM)
    return vector / np.linalg.norm(vector)


def _stub_local_model(monkeypatch, embedding: np.ndarray, *, face_count: int = 1) -> None:
    """Pretend the in-process ArcFace model is loaded and sees one face, so the
    tests never download weights or need onnxruntime."""
    fake_detections = type("Result", (), {"detections": [object()] * max(face_count, 0)})()
    monkeypatch.setattr(face_service, "_get_face_detector", lambda: type(
        "Detector", (), {"process": lambda self, image: fake_detections},
    )())
    monkeypatch.setattr(face_service, "_local_face_encoding", lambda image: (face_count, embedding))


def test_register_face_uses_local_arcface_when_worker_not_configured(monkeypatch):
    monkeypatch.setattr(face_service.ai_worker_client, "register_face", lambda image: None)
    monkeypatch.setattr(face_service, "assess_liveness", lambda image: {"live": True, "score": 1.0, "reason": "ok"})
    embedding = _unit_embedding(1)
    _stub_local_model(monkeypatch, embedding)

    ok, message, encoding_json = face_service.register_face(_b64_png(), "/tmp/does-not-matter.jpg")
    assert ok is True
    stored = json.loads(encoding_json)
    assert len(stored) == face_service.ARCFACE_DIM
    assert np.allclose(stored, embedding)


def test_local_and_worker_embeddings_share_one_format(monkeypatch):
    """The whole point of dropping dlib: a profile registered against the
    worker must be verifiable against the local model and vice versa."""
    embedding = _unit_embedding(2)
    monkeypatch.setattr(face_service.ai_worker_client, "register_face", lambda image: {
        "success": True, "message": "ArcFace profile created.", "encoding": embedding.tolist(),
    })
    _, _, worker_json = face_service.register_face(_b64_png(), "/tmp/does-not-matter.jpg")

    # Same profile, now verified with NO worker available.
    monkeypatch.setattr(face_service.ai_worker_client, "verify_face", lambda *a, **k: None)
    monkeypatch.setattr(face_service, "assess_liveness", lambda image: {"live": True, "score": 1.0, "reason": "ok"})
    _stub_local_model(monkeypatch, embedding)

    result = face_service.verify_live_frame(_b64_png(), worker_json)
    assert result["match"] is True
    assert result["distance"] == pytest.approx(0.0, abs=1e-6)


def test_verify_rejects_a_different_face(monkeypatch):
    monkeypatch.setattr(face_service.ai_worker_client, "verify_face", lambda *a, **k: None)
    monkeypatch.setattr(face_service, "assess_liveness", lambda image: {"live": True, "score": 1.0, "reason": "ok"})
    _stub_local_model(monkeypatch, _unit_embedding(3))

    result = face_service.verify_live_frame(_b64_png(), json.dumps(_unit_embedding(4).tolist()))
    assert result["match"] is False
    # Two random 512-d unit vectors are near-orthogonal, so cosine distance ~1.
    assert 0.5 < result["distance"] < 1.5


def _embedding_at_distance(base: np.ndarray, target_distance: float) -> np.ndarray:
    """A unit vector whose cosine distance from `base` is exactly
    `target_distance`, for pinning down FACE_MATCH_TOLERANCE's actual
    boundary rather than relying on two random (near-orthogonal, distance~1)
    vectors the way test_verify_rejects_a_different_face does."""
    rng = np.random.default_rng(99)
    candidate = rng.normal(size=base.shape[0])
    # Gram-Schmidt: strip out any component along `base` so what's left is
    # guaranteed orthogonal to it.
    orthogonal = candidate - np.dot(candidate, base) * base
    orthogonal /= np.linalg.norm(orthogonal)
    cosine_similarity = 1.0 - target_distance
    theta = np.arccos(np.clip(cosine_similarity, -1.0, 1.0))
    return np.cos(theta) * base + np.sin(theta) * orthogonal


def test_a_similar_but_different_face_is_rejected_at_the_configured_tolerance(monkeypatch):
    """Pins down the actual number, not just "very different faces fail".
    FACE_MATCH_TOLERANCE was loosened to 0.5 at one point and shown to
    false-accept real, different people in practice -- this fails loudly if
    it (or the comparison direction) ever regresses back to something that
    permissive."""
    monkeypatch.setattr(face_service.ai_worker_client, "verify_face", lambda *a, **k: None)
    monkeypatch.setattr(face_service, "assess_liveness", lambda image: {"live": True, "score": 1.0, "reason": "ok"})
    base = _unit_embedding(42)
    stored_json = json.dumps(base.tolist())

    # Comfortably inside the tolerance (same person, natural lighting/pose
    # variation between registration and exam day) -- must match.
    close = _embedding_at_distance(base, target_distance=0.20)
    _stub_local_model(monkeypatch, close)
    result = face_service.verify_live_frame(_b64_png(), stored_json)
    assert result["match"] is True
    assert result["distance"] == pytest.approx(0.20, abs=1e-3)

    # Just outside the tolerance -- a genuinely different (if similar-looking)
    # face -- must NOT match. This is exactly the false-accept the tolerance
    # was tightened to close.
    far = _embedding_at_distance(base, target_distance=0.45)
    _stub_local_model(monkeypatch, far)
    result = face_service.verify_live_frame(_b64_png(), stored_json)
    assert result["match"] is False
    assert result["distance"] == pytest.approx(0.45, abs=1e-3)


def test_legacy_dlib_profile_asks_for_re_registration(monkeypatch):
    """128-d profiles predate ArcFace-everywhere. They must fail with an
    actionable message rather than a shape-mismatch or a bogus match."""
    monkeypatch.setattr(face_service, "assess_liveness", lambda image: {"live": True, "score": 1.0, "reason": "ok"})

    def _must_not_run(*_args, **_kwargs):
        raise AssertionError("the identity model must not run without a usable stored profile")

    monkeypatch.setattr(face_service, "_local_face_encoding", _must_not_run)
    monkeypatch.setattr(face_service.ai_worker_client, "verify_face", _must_not_run)

    legacy = json.dumps([0.01] * 128)
    result = face_service.verify_live_frame(_b64_png(), legacy)
    assert result["match"] is False
    assert "register your face again" in result["message"].lower()


def test_corrupt_profile_is_treated_as_legacy(monkeypatch):
    monkeypatch.setattr(face_service, "assess_liveness", lambda image: {"live": True, "score": 1.0, "reason": "ok"})
    monkeypatch.setattr(face_service.ai_worker_client, "verify_face", lambda *a, **k: None)
    result = face_service.verify_live_frame(_b64_png(), "not-json-at-all")
    assert result["match"] is False
    assert "register your face again" in result["message"].lower()


def test_register_face_rejects_suspected_spoof_before_running_face_model(monkeypatch):
    monkeypatch.setattr(face_service.ai_worker_client, "register_face", lambda image: None)
    monkeypatch.setattr(face_service, "assess_liveness", lambda image: {
        "live": False, "score": 0.2, "reason": "flat color distribution",
    })

    def _must_not_run(*_args, **_kwargs):
        raise AssertionError("local face model should not run when the liveness check fails")

    monkeypatch.setattr(face_service, "_get_face_detector", _must_not_run)
    monkeypatch.setattr(face_service, "_local_face_encoding", _must_not_run)
    monkeypatch.setattr(face_service, "_arcface_app", _must_not_run)

    ok, message, encoding_json = face_service.register_face(_b64_png(), "/tmp/does-not-matter.jpg")
    assert ok is False
    assert encoding_json is None
    assert "liveness" in message.lower()


# --- registration in a room with other people in it -------------------------
#
# Regression cover for the bug that made registration impossible in a computer
# lab: MediaPipe (which gates on face count) and InsightFace's own RetinaFace
# (inside `_arcface_app().get()`) disagree about small background faces, and
# `_local_face_encoding` used to refuse to produce an embedding for any count
# other than exactly one. The student got "Could not compute a face profile.
# Try better lighting..." for a photo with nothing wrong with it, forever.
#
# These stub the two detectors at the seam rather than mocking
# `_local_face_encoding` wholesale (which is what let the bug through: every
# existing registration test replaced the very function that was broken).


class _FakeMediaPipeDetection:
    """Mimics the one attribute path face_service reads off a detection."""

    def __init__(self, width: float, height: float):
        box = type("Box", (), {"width": width, "height": height})()
        self.location_data = type("LocationData", (), {"relative_bounding_box": box})()


class _FakeInsightFace:
    def __init__(self, bbox, embedding):
        self.bbox = np.asarray(bbox, dtype=np.float32)
        self.normed_embedding = embedding


def _stub_detectors(monkeypatch, *, mediapipe_boxes, insightface_faces):
    """Wire both real detector seams to fixed results, leaving the code under
    test -- the counting, prominence and largest-face logic -- untouched."""
    detections = [_FakeMediaPipeDetection(w, h) for w, h in mediapipe_boxes]
    monkeypatch.setattr(face_service, "_get_face_detector", lambda: type(
        "Detector", (), {"process": lambda self, image: type(
            "Result", (), {"detections": detections})()},
    )())
    monkeypatch.setattr(face_service, "_arcface_app", lambda: type(
        "Model", (), {"get": lambda self, image: insightface_faces},
    )())


def test_register_face_enrols_the_candidate_when_classmates_are_in_the_background(monkeypatch):
    """The reported bug, end to end: one person at the camera, several seated
    well behind. Must register, and must register the FRONT face."""
    monkeypatch.setattr(face_service.ai_worker_client, "register_face", lambda image: None)
    monkeypatch.setattr(face_service, "assess_liveness", lambda image: {"live": True, "score": 1.0, "reason": "ok"})

    candidate = _unit_embedding(11)
    bystander_a, bystander_b = _unit_embedding(12), _unit_embedding(13)
    _stub_detectors(
        monkeypatch,
        # MediaPipe: the candidate at ~9% of frame area, two classmates at ~0.2%.
        mediapipe_boxes=[(0.23, 0.40), (0.05, 0.05), (0.04, 0.05)],
        # RetinaFace agrees there are three, which is what used to fail.
        insightface_faces=[
            _FakeInsightFace([300, 200, 430, 370], candidate),   # 130x170 = 22100
            _FakeInsightFace([40, 250, 68, 285], bystander_a),   # 28x35   =   980
            _FakeInsightFace([700, 245, 726, 278], bystander_b),
        ],
    )

    ok, message, encoding_json = face_service.register_face(_b64_png(), "/tmp/does-not-matter.jpg")
    assert ok is True, message
    assert np.allclose(json.loads(encoding_json), candidate), "must enrol the largest (front) face"


def test_register_face_still_rejects_a_second_person_at_the_camera(monkeypatch):
    """The prominence rule must not become a way for two people to enrol
    together -- only to ignore the back of the room."""
    monkeypatch.setattr(face_service.ai_worker_client, "register_face", lambda image: None)
    monkeypatch.setattr(face_service, "assess_liveness", lambda image: {"live": True, "score": 1.0, "reason": "ok"})
    _stub_detectors(
        monkeypatch,
        mediapipe_boxes=[(0.20, 0.35), (0.18, 0.33)],  # two faces, both large
        insightface_faces=[_FakeInsightFace([0, 0, 10, 10], _unit_embedding(14))],
    )

    ok, message, encoding_json = face_service.register_face(_b64_png(), "/tmp/does-not-matter.jpg")
    assert ok is False
    assert encoding_json is None
    assert "more than one person" in message.lower()


def test_register_face_with_no_face_says_so_instead_of_blaming_lighting(monkeypatch):
    monkeypatch.setattr(face_service.ai_worker_client, "register_face", lambda image: None)
    monkeypatch.setattr(face_service, "assess_liveness", lambda image: {"live": True, "score": 1.0, "reason": "ok"})
    _stub_detectors(monkeypatch, mediapipe_boxes=[], insightface_faces=[])

    ok, message, _ = face_service.register_face(_b64_png(), "/tmp/does-not-matter.jpg")
    assert ok is False
    assert "no face detected" in message.lower()


def test_local_face_encoding_returns_the_largest_face_and_the_true_count(monkeypatch):
    """The count must stay truthful even though only one face is embedded --
    exam-time multiple-face violations are driven off it."""
    big, small = _unit_embedding(15), _unit_embedding(16)
    monkeypatch.setattr(face_service, "_arcface_app", lambda: type("Model", (), {
        "get": lambda self, image: [
            _FakeInsightFace([0, 0, 20, 20], small),
            _FakeInsightFace([100, 100, 300, 300], big),
        ],
    })())

    count, encoding = face_service._local_face_encoding(np.zeros((64, 64, 3), dtype=np.uint8))
    assert count == 2
    assert np.allclose(encoding, big)


def test_local_face_encoding_reports_nothing_when_there_is_no_face(monkeypatch):
    monkeypatch.setattr(face_service, "_arcface_app", lambda: type("Model", (), {
        "get": lambda self, image: [],
    })())
    count, encoding = face_service._local_face_encoding(np.zeros((64, 64, 3), dtype=np.uint8))
    assert count == 0
    assert encoding is None


def test_detect_objects_falls_back_to_the_local_model_without_the_ai_worker(monkeypatch):
    """No worker no longer means no detection.

    This used to assert available=False, which was correct when object
    detection existed *only* in the ai_worker container -- but it also meant
    the test passed for the wrong reason (it was asserting a missing feature)
    and would have started failing the moment anyone installed the weights.
    The contract now is: fall through to the in-process detector.
    """
    monkeypatch.setattr(proctor_service.ai_worker_client, "detect_objects", lambda image: None)
    monkeypatch.setattr(proctor_service.object_service, "detect", lambda image: {
        "available": True, "detections": [{"label": "book", "confidence": 0.8}],
        "person_count": 0, "message": "ok",
    })

    result = proctor_service.detect_objects(_b64_png())

    assert result["available"] is True
    assert result["detections"][0]["label"] == "book"


def test_detect_objects_degrades_gracefully_when_no_detector_exists_at_all(monkeypatch):
    """Neither worker nor local model: still a clean, non-raising answer, so
    the frontend stops polling instead of erroring."""
    monkeypatch.setattr(proctor_service.ai_worker_client, "detect_objects", lambda image: None)
    monkeypatch.setattr(proctor_service.object_service, "detect", lambda image: {
        "available": False, "detections": [], "person_count": 0, "message": "not set up",
    })

    result = proctor_service.detect_objects(_b64_png())

    assert result["available"] is False
    assert result["detections"] == []


def test_detect_objects_rejects_a_malformed_image_before_reaching_the_model(monkeypatch):
    """The local path decodes the payload itself, so the shared image
    validation must still apply -- a garbage payload is a 400, not a crash
    inside numpy."""
    monkeypatch.setattr(proctor_service.ai_worker_client, "detect_objects", lambda image: None)

    with pytest.raises(HTTPException) as exc:
        proctor_service.detect_objects(base64.b64encode(b"not an image").decode())

    assert exc.value.status_code == 400


def test_detect_objects_passes_through_worker_result(monkeypatch):
    monkeypatch.setattr(proctor_service.ai_worker_client, "detect_objects", lambda image: {
        "detections": [{"label": "cell phone", "confidence": 0.9}], "person_count": 1,
    })
    result = proctor_service.detect_objects(_b64_png())
    assert result["available"] is True
    assert result["person_count"] == 1
    assert result["detections"][0]["label"] == "cell phone"


def test_analyze_pose_reports_no_face_without_crashing():
    # No AI worker or heavy model needed -- pose/gaze analysis is fully local.
    # With no face in frame it should report a clean "not available" signal,
    # not raise, matching the same degrade-gracefully contract as face_service.
    result = proctor_service.analyze_pose(_b64_png())
    assert result["available"] is True
    assert result["face_count"] == 0
    assert result["looking_away"] is None
    assert result["gaze_deviation"] is None


def test_verify_id_card_matches_when_registered_name_present(monkeypatch):
    monkeypatch.setattr(ocr_service, "extract_text", lambda image: "GOVT OF EXAMPLE ID CARD JANE DOE ROLL 123")
    result = ocr_service.verify_id_card(_b64_png(), "Jane Doe")
    assert result["name_matched"] is True
    assert "matches" in result["message"].lower()


def test_verify_id_card_flags_mismatch(monkeypatch):
    monkeypatch.setattr(ocr_service, "extract_text", lambda image: "SOME COMPLETELY UNRELATED TEXT")
    result = ocr_service.verify_id_card(_b64_png(), "Jane Doe")
    assert result["name_matched"] is False


def test_extract_text_joins_easyocr_fragments_in_order(monkeypatch):
    fake_reader = type("FakeReader", (), {
        "readtext": lambda self, image, detail=1, paragraph=False: [
            ([[0, 0], [10, 0], [10, 10], [0, 10]], "JANE", 0.98),
            ([[0, 10], [10, 10], [10, 20], [0, 20]], "DOE", 0.95),
        ],
    })()
    monkeypatch.setattr(ocr_service, "_easyocr_reader", lambda: fake_reader)
    assert ocr_service.extract_text(_b64_png()) == "JANE DOE"


def test_easyocr_reader_unavailable_raises_friendly_503(monkeypatch):
    ocr_service._easyocr_reader.cache_clear()

    import builtins
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "easyocr":
            raise ModuleNotFoundError("no module named easyocr")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(HTTPException) as exc:
        ocr_service._easyocr_reader()
    assert exc.value.status_code == 503
    ocr_service._easyocr_reader.cache_clear()


def test_severity_map_covers_all_new_advanced_signals():
    for event_type in (
        "phone_detected", "book_detected", "multiple_persons_detected",
        "looking_away", "gaze_deviation", "external_monitor_detected",
    ):
        assert event_type in SEVERITY_MAP
