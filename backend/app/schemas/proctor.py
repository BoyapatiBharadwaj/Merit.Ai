from datetime import datetime
from pydantic import BaseModel, Field
from app.models.enums import EventType


class ProctorEventCreate(BaseModel):
    attempt_id: int = Field(gt=0)
    event_type: EventType
    description: str | None = Field(default=None, max_length=255)
    screenshot_base64: str | None = Field(default=None, max_length=7_000_000)


class ProctorEventBatchCreate(BaseModel):
    """Body for POST /proctoring/events/batch -- see frontend/src/lib/eventLogger.js."""

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
    # Mirrors ObjectDetectionResponse/PoseCheckResponse below.
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