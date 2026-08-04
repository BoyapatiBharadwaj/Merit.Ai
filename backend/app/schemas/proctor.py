from datetime import datetime
from pydantic import BaseModel, Field
from app.models.enums import EventType


class ProctorEventCreate(BaseModel):
    attempt_id: int = Field(gt=0)
    event_type: EventType
    description: str | None = Field(default=None, max_length=255)
    # Was previously a separate, un-typed query-string parameter on the
    # endpoint -- FastAPI silently binds bare scalar params that way when a
    # Pydantic body model is already present, so any real base64 image sent
    # through it would have been forced through the URL (length limits,
    # proxy/access-log exposure of exam-screen contents) rather than the
    # request body. Moved onto the body model so it's actually usable and
    # never ends up in a log line. Same upper bound as proctoring.py's
    # ImagePayload for the live-frame endpoints.
    screenshot_base64: str | None = Field(default=None, max_length=7_000_000)


class ProctorEventBatchCreate(BaseModel):
    """Body for POST /proctoring/events/batch -- see frontend/src/lib/eventLogger.js.

    Each event still carries its own attempt_id (rather than one shared field
    on the envelope) so the schema stays identical to the single-event path;
    the endpoint validates ownership per item for the same reason. Capped at
    a size well above the client's own MAX_BATCH_SIZE (8) so a misbehaving or
    modified client can't turn one request into an unbounded write.
    """

    events: list[ProctorEventCreate] = Field(min_length=1, max_length=50)


class ProctorEventOut(BaseModel):
    id: int
    attempt_id: int
    event_type: str
    severity: str
    description: str | None
    screenshot_path: str | None
    created_at: datetime

    class Config:
        from_attributes = True


class FaceMatchResponse(BaseModel):
    # Mirrors ObjectDetectionResponse/PoseCheckResponse below: False means the
    # signal was not collected (switched off, or the model is unavailable), as
    # distinct from "collected and found nothing". The exam client must treat
    # an unavailable signal as no information rather than as a failed check --
    # see FACE_MATCHING_ENABLED in app/core/config.py.
    available: bool = True
    face_count: int
    match: bool | None = None
    distance: float | None = None
    spoof_suspected: bool = False
    liveness_score: float | None = None
    message: str


class IDCardVerifyResponse(BaseModel):
    extracted_text: str
    name_matched: bool
    message: str


class DetectionOut(BaseModel):
    label: str
    confidence: float


class ObjectDetectionResponse(BaseModel):
    available: bool
    detections: list[DetectionOut] = []
    person_count: int = 0
    message: str


class PoseCheckResponse(BaseModel):
    available: bool
    face_count: int
    looking_away: bool | None = None
    gaze_deviation: bool | None = None
    yaw: float | None = None
    pitch: float | None = None
    gaze_ratio: float | None = None
    message: str