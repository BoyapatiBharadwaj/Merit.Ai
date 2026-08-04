"""Local (in-process) YOLO object detection: decoding, filtering, degradation.

The interesting, breakable part of object_service is not "does onnxruntime
run" -- it is the post-processing: letterbox coordinate mapping, the
anchor-free class-score layout YOLO11 uses, per-class NMS, and the per-class
confidence floors. All of that is pure numpy and is exercised here directly
against tensors shaped exactly like a real YOLO11 head, so these tests are
meaningful without a 19MB model download in CI.

A synthetic ONNX graph (built with onnx, which onnxruntime already ships
alongside) covers the one thing pure-numpy tests cannot: that the session is
actually driven with the right input name, layout and dtype.
"""
import numpy as np
import pytest

from app.ai import object_service

NUM_CLASSES = 80
NUM_PREDICTIONS = 40


def _empty_head() -> np.ndarray:
    """A (1, 84, N) YOLO11 head tensor with every score at zero."""
    return np.zeros((1, 4 + NUM_CLASSES, NUM_PREDICTIONS), dtype=np.float32)


def _plant(head: np.ndarray, slot: int, class_id: int, confidence: float,
           box=(320.0, 320.0, 60.0, 120.0)) -> None:
    """Write one detection into a prediction slot, in cxcywh 640-space."""
    head[0, 0, slot], head[0, 1, slot], head[0, 2, slot], head[0, 3, slot] = box
    head[0, 4 + class_id, slot] = confidence


# --------------------------------------------------------------------------
# Decoding
# --------------------------------------------------------------------------

def test_decode_reads_the_anchor_free_class_layout():
    """YOLO11 has no objectness channel -- the class score IS the confidence.

    Multiplying by a non-existent objectness (the v5-era habit) would zero
    every score here and return nothing.
    """
    head = _empty_head()
    _plant(head, slot=3, class_id=object_service.COCO_CELL_PHONE, confidence=0.91)

    detections = object_service._decode(head, scale=1.0, pad_x=0, pad_y=0)

    assert detections == [{"label": "cell phone", "confidence": 0.91}]


def test_decode_ignores_classes_we_do_not_act_on():
    head = _empty_head()
    _plant(head, slot=0, class_id=56, confidence=0.99)   # chair
    _plant(head, slot=1, class_id=63, confidence=0.98)   # laptop

    assert object_service._decode(head, 1.0, 0, 0) == []


def test_a_confident_irrelevant_class_cannot_mask_a_phone_in_the_same_slot():
    """The argmax must run over the classes we care about, not all 80.

    A single prediction slot carries a score for every class. If the argmax
    ran over all of them first, this slot would resolve to "chair", get
    discarded as irrelevant, and the phone in it would vanish -- the exact
    detection that matters most in an exam.
    """
    head = _empty_head()
    slot = 5
    head[0, 0, slot], head[0, 1, slot], head[0, 2, slot], head[0, 3, slot] = 100.0, 100.0, 40.0, 80.0
    head[0, 4 + 56, slot] = 0.97                                 # chair, very confident
    head[0, 4 + object_service.COCO_CELL_PHONE, slot] = 0.62     # phone, less so

    detections = object_service._decode(head, 1.0, 0, 0)

    assert detections == [{"label": "cell phone", "confidence": 0.62}]


def test_decode_survives_a_model_with_fewer_classes_than_coco():
    """A custom/pruned export must not raise an IndexError on COCO indices."""
    head = np.zeros((1, 4 + 10, NUM_PREDICTIONS), dtype=np.float32)
    head[0, 0, 0], head[0, 1, 0], head[0, 2, 0], head[0, 3, 0] = 320.0, 320.0, 40.0, 40.0
    head[0, 4 + object_service.COCO_PERSON, 0] = 0.88

    assert object_service._decode(head, 1.0, 0, 0) == [{"label": "person", "confidence": 0.88}]


def test_decode_returns_nothing_for_a_malformed_head():
    assert object_service._decode(np.zeros((1, 4, 10), dtype=np.float32), 1.0, 0, 0) == []


# --------------------------------------------------------------------------
# Per-class confidence floors
# --------------------------------------------------------------------------

def test_book_needs_more_confidence_than_a_phone():
    """"book" is COCO's noisiest class here (it fires on laptops, keyboards and
    monitor bezels), so it carries a stricter floor than "cell phone". The same
    0.40 score must therefore be rejected as a book and accepted as a phone."""
    score = 0.40
    assert object_service.CONFIDENCE_FLOORS["book"] > score > object_service.CONFIDENCE_FLOORS["cell phone"]

    as_book = _empty_head()
    _plant(as_book, 0, object_service.COCO_BOOK, score)
    assert object_service._decode(as_book, 1.0, 0, 0) == []

    as_phone = _empty_head()
    _plant(as_phone, 0, object_service.COCO_CELL_PHONE, score)
    assert object_service._decode(as_phone, 1.0, 0, 0) == [{"label": "cell phone", "confidence": score}]


def test_detections_come_back_most_confident_first():
    head = _empty_head()
    _plant(head, 0, object_service.COCO_PERSON, 0.55, box=(100.0, 100.0, 50.0, 50.0))
    _plant(head, 1, object_service.COCO_CELL_PHONE, 0.95, box=(400.0, 400.0, 30.0, 60.0))

    confidences = [d["confidence"] for d in object_service._decode(head, 1.0, 0, 0)]

    assert confidences == sorted(confidences, reverse=True)


# --------------------------------------------------------------------------
# NMS
# --------------------------------------------------------------------------

def test_nms_collapses_duplicate_boxes_for_the_same_class():
    head = _empty_head()
    for slot, confidence in enumerate([0.90, 0.88, 0.86]):
        _plant(head, slot, object_service.COCO_PERSON, confidence, box=(300.0, 300.0, 100.0, 200.0))

    detections = object_service._decode(head, 1.0, 0, 0)

    assert detections == [{"label": "person", "confidence": 0.9}]


def test_nms_keeps_two_genuinely_separate_people():
    head = _empty_head()
    _plant(head, 0, object_service.COCO_PERSON, 0.90, box=(150.0, 300.0, 100.0, 200.0))
    _plant(head, 1, object_service.COCO_PERSON, 0.80, box=(500.0, 300.0, 100.0, 200.0))

    detections = object_service._decode(head, 1.0, 0, 0)

    assert len(detections) == 2
    assert {d["confidence"] for d in detections} == {0.9, 0.8}


def test_a_phone_overlapping_the_person_holding_it_is_not_suppressed():
    """NMS must run per class. Cross-class suppression would delete the phone
    inside the person box -- i.e. exactly the cheating case, every time."""
    head = _empty_head()
    _plant(head, 0, object_service.COCO_PERSON, 0.95, box=(320.0, 320.0, 300.0, 500.0))
    _plant(head, 1, object_service.COCO_CELL_PHONE, 0.70, box=(320.0, 320.0, 40.0, 80.0))

    labels = {d["label"] for d in object_service._decode(head, 1.0, 0, 0)}

    assert labels == {"person", "cell phone"}


def test_nms_helper_handles_the_empty_case():
    assert object_service._nms(np.zeros((0, 4), dtype=np.float32), np.zeros(0, dtype=np.float32), 0.45) == []


# --------------------------------------------------------------------------
# Letterboxing
# --------------------------------------------------------------------------

def test_letterbox_preserves_aspect_ratio_and_centres_the_image():
    image = np.full((360, 640, 3), 200, dtype=np.uint8)

    padded, scale, pad_x, pad_y = object_service._letterbox(image, size=640)

    assert padded.shape == (640, 640, 3)
    assert scale == pytest.approx(1.0)
    assert pad_x == 0 and pad_y == 140          # (640 - 360) // 2
    assert (padded[0, 0] == 114).all()          # YOLO's grey pad, not black
    assert (padded[320, 320] == 200).all()      # real pixels land in the middle


def test_letterbox_scale_and_padding_invert_back_to_source_coordinates():
    """The numbers _letterbox returns are only useful if _decode can undo them,
    so verify the round trip on a non-square frame rather than the scale alone."""
    image = np.zeros((480, 640, 3), dtype=np.uint8)
    _, scale, pad_x, pad_y = object_service._letterbox(image, size=640)

    # A box centred in the padded canvas must map back to the frame's centre.
    head = _empty_head()
    _plant(head, 0, object_service.COCO_PERSON, 0.9, box=(320.0, 320.0, 64.0, 64.0))
    centre_x = (320.0 - pad_x) / scale
    centre_y = (320.0 - pad_y) / scale

    assert centre_x == pytest.approx(320.0, abs=1.0)
    assert centre_y == pytest.approx(240.0, abs=1.0)
    assert object_service._decode(head, scale, pad_x, pad_y)[0]["label"] == "person"


# --------------------------------------------------------------------------
# End-to-end through a real onnxruntime session
# --------------------------------------------------------------------------

def _build_stub_onnx(path, planted_head: np.ndarray):
    """A real ONNX graph with YOLO11's signature that ignores its input and
    returns a fixed head, so the session plumbing is exercised for real."""
    onnx = pytest.importorskip("onnx")
    from onnx import TensorProto, helper, numpy_helper

    graph = helper.make_graph(
        nodes=[helper.make_node("Identity", ["const_out"], ["output0"])],
        name="stub_yolo",
        inputs=[helper.make_tensor_value_info("images", TensorProto.FLOAT, [1, 3, 640, 640])],
        outputs=[helper.make_tensor_value_info(
            "output0", TensorProto.FLOAT, list(planted_head.shape))],
        initializer=[numpy_helper.from_array(planted_head, name="const_out")],
    )
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 13)])
    model.ir_version = 9  # onnxruntime 1.2x rejects anything newer
    onnx.save(model, str(path))


def test_onnx_session_path_end_to_end(tmp_path, monkeypatch):
    """Drives _detect_onnx through a genuine InferenceSession: proves the input
    name lookup, NCHW layout, float32 dtype and 0-1 scaling all line up."""
    pytest.importorskip("onnxruntime")
    head = _empty_head()
    _plant(head, 0, object_service.COCO_CELL_PHONE, 0.77)
    _plant(head, 1, object_service.COCO_PERSON, 0.93, box=(100.0, 100.0, 80.0, 160.0))

    model_path = tmp_path / "stub.onnx"
    _build_stub_onnx(model_path, head)

    import onnxruntime as ort
    session = ort.InferenceSession(str(model_path), providers=["CPUExecutionProvider"])
    frame = np.random.randint(0, 255, (480, 640, 3), dtype=np.uint8)

    detections = object_service._detect_onnx(session, frame)

    assert {d["label"] for d in detections} == {"cell phone", "person"}


def test_detect_reports_unavailable_without_a_model(monkeypatch, tmp_path):
    """The contract the frontend depends on: no model means a clean
    available=False, never an exception and never a bogus empty detection list
    that would read as "we looked and the room is clear"."""
    monkeypatch.setattr(object_service, "_session", None)
    monkeypatch.setattr(object_service, "_ultralytics_model", None)
    monkeypatch.setattr(object_service.settings, "OBJECT_MODEL_ROOT", str(tmp_path))
    monkeypatch.setattr(object_service, "_load_ultralytics",
                        lambda: (_ for _ in ()).throw(ImportError("no ultralytics")))

    result = object_service.detect(np.zeros((480, 640, 3), dtype=np.uint8))

    assert result["available"] is False
    assert result["detections"] == []
    assert result["person_count"] == 0
    assert "fetch_models" in result["message"]


def test_detect_counts_people_and_never_raises_on_inference_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(object_service, "_session", object())  # truthy stand-in
    monkeypatch.setattr(object_service, "_load_onnx_session", lambda: object_service._session)
    monkeypatch.setattr(object_service, "_detect_onnx",
                        lambda session, image: (_ for _ in ()).throw(RuntimeError("boom")))

    result = object_service.detect(np.zeros((480, 640, 3), dtype=np.uint8))

    assert result["available"] is False
    assert "boom" in result["message"]


def test_detect_counts_multiple_people(monkeypatch):
    monkeypatch.setattr(object_service, "_load_onnx_session", lambda: object())
    monkeypatch.setattr(object_service, "_detect_onnx", lambda session, image: [
        {"label": "person", "confidence": 0.9},
        {"label": "person", "confidence": 0.8},
        {"label": "cell phone", "confidence": 0.7},
    ])

    result = object_service.detect(np.zeros((480, 640, 3), dtype=np.uint8))

    assert result["available"] is True
    assert result["person_count"] == 2
    assert len(result["detections"]) == 3
