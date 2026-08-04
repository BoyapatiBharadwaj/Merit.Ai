"""
Kill switches for the AI proctoring signals.

The guarantee: switching a signal off degrades it to "not collected" and never
fails, blocks or accuses a candidate. The exam matters more than any individual
proctoring input -- and the failure this exists to make survivable has already
happened once here (the classical anti-spoof heuristic accusing legitimate
candidates every few seconds, which at the time needed a code change to stop).
"""
import pytest

from app.core.config import settings
from app.services import proctor_service


def test_object_detection_returns_unavailable_when_switched_off(monkeypatch):
    monkeypatch.setattr(settings, "OBJECT_DETECTION_ENABLED", False)
    result = proctor_service.detect_objects("ignored")
    assert result["available"] is False
    assert result["detections"] == []
    assert result["person_count"] == 0


def test_pose_detection_returns_unavailable_when_switched_off(monkeypatch):
    monkeypatch.setattr(settings, "POSE_DETECTION_ENABLED", False)
    result = proctor_service.analyze_pose("ignored")
    assert result["available"] is False


def test_face_matching_returns_unavailable_rather_than_a_verdict(db_session, monkeypatch):
    """The important one. A disabled check must not report `match: False` --
    that would accuse a candidate on the basis of a check nobody ran -- and must
    not report `match: True` either, which would put a fabricated pass on a
    proctoring report."""
    monkeypatch.setattr(settings, "FACE_MATCHING_ENABLED", False)
    result = proctor_service.verify_live_face(db_session, student_id=1, base64_image="ignored")

    assert result["available"] is False
    assert result["match"] is None
    assert result["spoof_suspected"] is False


def test_a_disabled_signal_never_touches_the_model(monkeypatch):
    """Cheap as well as safe: the switch short-circuits before any inference,
    so turning a signal off actually relieves the CPU pressure that is usually
    the reason for turning it off."""
    def _must_not_run(*_args, **_kwargs):
        raise AssertionError("inference ran despite the signal being disabled")

    monkeypatch.setattr(settings, "OBJECT_DETECTION_ENABLED", False)
    monkeypatch.setattr(proctor_service.ai_worker_client, "detect_objects", _must_not_run)
    monkeypatch.setattr(proctor_service.object_service, "detect_frame", _must_not_run, raising=False)
    proctor_service.detect_objects("ignored")

    monkeypatch.setattr(settings, "POSE_DETECTION_ENABLED", False)
    monkeypatch.setattr(proctor_service.pose_service, "analyze_frame", _must_not_run)
    proctor_service.analyze_pose("ignored")


def test_signals_are_all_on_by_default():
    """A kill switch that defaults to off is a disabled feature, not a switch."""
    assert settings.FACE_MATCHING_ENABLED is True
    assert settings.OBJECT_DETECTION_ENABLED is True
    assert settings.POSE_DETECTION_ENABLED is True


def test_the_response_shapes_stay_valid_when_disabled(monkeypatch):
    """The disabled payloads must still satisfy the response models, or the
    endpoint 500s at serialization time -- which would turn a graceful
    degradation into exactly the outage it was meant to avoid."""
    from app.schemas.proctor import FaceMatchResponse, ObjectDetectionResponse, PoseCheckResponse

    monkeypatch.setattr(settings, "OBJECT_DETECTION_ENABLED", False)
    ObjectDetectionResponse(**proctor_service.detect_objects("x"))

    monkeypatch.setattr(settings, "POSE_DETECTION_ENABLED", False)
    PoseCheckResponse(**proctor_service.analyze_pose("x"))
