"""Merit.Ai AI Worker -- standalone face/object inference microservice."""
import json
import logging
import tempfile

import numpy as np
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.ai import face_service, object_service
from app.ai.image_utils import decode_image

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("ai_worker")

app = FastAPI(
    title="Merit.Ai AI Worker",
    description="Internal-only face identity and object-detection inference service.",
)

# Same upper bound as the Core API's own proctoring.ImagePayload.
_MAX_IMAGE_B64_CHARS = 7_000_000


class ImagePayload(BaseModel):
    image_base64: str = Field(min_length=20, max_length=_MAX_IMAGE_B64_CHARS)


class VerifyPayload(ImagePayload):
    # Sent by app.ai.ai_worker_client.verify_face as stored_vector.tolist().
    encoding: list[float]
    # The Core API's own settings.FACE_MATCH_TOLERANCE, sent explicitly
    # so this worker's decision uses the caller's configured threshold
    # rather than needing its own env to be hand-kept in sync.
    tolerance: float = Field(gt=0, le=2)


@app.get("/health")
def health():
    """Liveness probe -- dependency-free on purpose, mirroring the Core
    API's own /api/health: a slow model warm-up on first boot must not read
    to an orchestrator as a crashed container and trigger a restart loop."""
    return {"status": "ok"}


@app.post("/face/register")
def register(payload: ImagePayload):
    """Computes a face embedding for a new registration."""
    with tempfile.NamedTemporaryFile(suffix=".jpg") as tmp:
        success, message, encoding_json = face_service.register_face(payload.image_base64, tmp.name)
    if not success:
        return {"success": False, "message": message}
    return {"success": True, "message": message, "encoding": json.loads(encoding_json)}


@app.post("/face/verify")
def verify(payload: VerifyPayload):
    """Matches a live frame against an already-registered embedding."""
    stored_encoding_json = json.dumps(payload.encoding)
    return face_service.verify_live_frame(payload.image_base64, stored_encoding_json, tolerance=payload.tolerance)


@app.post("/detect")
def detect(payload: ImagePayload):
    """Phone / book / extra-person detection for one frame."""
    image = np.asarray(decode_image(payload.image_base64))
    result = object_service.detect(image)
    if not result.get("available"):
        logger.warning("Object detection unavailable in ai-worker: %s", result.get("message"))
        return JSONResponse(status_code=503, content={"detail": result.get("message", "Object detection unavailable.")})
    return {"detections": result["detections"], "person_count": result["person_count"]}
