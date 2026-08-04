"""
Merit.Ai AI Worker -- standalone face/object inference microservice.

Implements the exact HTTP contract app/ai/ai_worker_client.py already speaks
(that client has existed in this codebase since before this service did --
AI_SERVICE_URL and the fallback-to-local-model design were built for this
worker, which had simply never been stood up as a real process):

    POST /face/register  {image_base64}                          -> {success, message, encoding}
    POST /face/verify     {image_base64, encoding, tolerance}      -> FaceMatchResponse shape
    POST /detect           {image_base64}                           -> {detections, person_count}
    GET  /health

This process runs the *exact same* app.ai.face_service / object_service code
the Core API falls back to locally when this worker is unset or unreachable
-- not a reimplementation of it. That matters: both paths must produce
bit-identical ArcFace embeddings (see face_service.py's module docstring for
why), and the only way to guarantee that is to run the one tested module in
both places rather than hand-porting the model-inference logic here.

This worker deliberately runs with AI_SERVICE_URL left unset in its own
environment (see docker-compose.yml -- it is never given that variable), so
face_service.register_face() / verify_live_frame() always take their "local"
branch inside THIS process. That local branch is not a fallback from this
worker's point of view -- it is the thing AI_SERVICE_URL, when set on the
Core API, is pointed at.

No auth, no CORS, no database, no filesystem persistence: this service is
reachable only from the Core API over the internal Docker network (see
docker-compose.yml -- no `ports:` mapping is published for it, only
`expose`), the same trust boundary an internal sidecar would have. It never
opens a connection to Postgres, so none of app.database / app.models /
app.services / app.api is imported here.
"""
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

# Same upper bound as the Core API's own proctoring.ImagePayload -- a live
# webcam JPEG comfortably fits well under this; the ceiling exists to bound
# how much a single request can make this process decode and hold in memory.
_MAX_IMAGE_B64_CHARS = 7_000_000


class ImagePayload(BaseModel):
    image_base64: str = Field(min_length=20, max_length=_MAX_IMAGE_B64_CHARS)


class VerifyPayload(ImagePayload):
    # Sent by app.ai.ai_worker_client.verify_face as stored_vector.tolist() --
    # a plain list, not the JSON-string-in-a-Text-column shape the database
    # stores it as. Re-serialized to that shape below before handing it to
    # verify_live_frame, which is the one function that knows how to parse it
    # (and reject a legacy/malformed profile).
    encoding: list[float]
    # The Core API's own settings.FACE_MATCH_TOLERANCE, sent explicitly so
    # this worker's decision uses the caller's configured threshold rather
    # than needing its own env to be hand-kept in sync. See the `tolerance`
    # parameter added to face_service.verify_live_frame for exactly this.
    tolerance: float = Field(gt=0, le=2)


@app.get("/health")
def health():
    """Liveness probe -- dependency-free on purpose, mirroring the Core
    API's own /api/health: a slow model warm-up on first boot must not read
    to an orchestrator as a crashed container and trigger a restart loop."""
    return {"status": "ok"}


@app.post("/face/register")
def register(payload: ImagePayload):
    """Computes a face embedding for a new registration.

    The registered photo itself is NOT persisted by this service -- it has
    no volume for it and no reason to keep one. face_service.register_face
    writes the decoded frame to `save_path` as a side effect of computing the
    embedding, so a throwaway temp file satisfies that without this process
    ever owning student photo storage; the Core API separately decodes and
    saves the real copy itself (see proctor_service.register_face), exactly
    as it already does on the "worker configured and reachable" path today.

    Always returns 200: `success: false` (bad photo, no face, liveness
    blocked) is a legitimate computed answer, not a service failure -- see
    the module docstring on why that distinction is what keeps
    app.ai.ai_worker_client's fallback-to-local behavior correct. An actual
    fault (model failed to load, unexpected exception) is left to raise
    normally, which FastAPI turns into a non-2xx response -- and *that* is
    what should make the Core API fall back to computing locally instead.
    """
    with tempfile.NamedTemporaryFile(suffix=".jpg") as tmp:
        success, message, encoding_json = face_service.register_face(payload.image_base64, tmp.name)
    if not success:
        return {"success": False, "message": message}
    return {"success": True, "message": message, "encoding": json.loads(encoding_json)}


@app.post("/face/verify")
def verify(payload: VerifyPayload):
    """Matches a live frame against an already-registered embedding.

    Always returns the full FaceMatchResponse-shaped dict as computed --
    "no match" and "spoof suspected" are answers, not errors, so they come
    back as normal 200 responses just like the Core API's own in-process
    path does.
    """
    stored_encoding_json = json.dumps(payload.encoding)
    return face_service.verify_live_frame(payload.image_base64, stored_encoding_json, tolerance=payload.tolerance)


@app.post("/detect")
def detect(payload: ImagePayload):
    """Phone / book / extra-person detection for one frame.

    Deliberately returns an HTTP error (not a 200 with `available: false`)
    when local detection can't run here -- e.g. YOLO weights were never
    fetched into this worker's volume. An HTTPError is what makes
    app.ai.ai_worker_client._post() return None, which is what makes
    proctor_service.detect_objects() fall back to the Core API's own
    in-process detector instead of just reporting the signal dead for the
    rest of the exam -- see that function's docstring. A 200 here would be
    read as a *successful* worker answer and skip that fallback entirely.
    """
    image = np.asarray(decode_image(payload.image_base64))
    result = object_service.detect(image)
    if not result.get("available"):
        logger.warning("Object detection unavailable in ai-worker: %s", result.get("message"))
        return JSONResponse(status_code=503, content={"detail": result.get("message", "Object detection unavailable.")})
    return {"detections": result["detections"], "person_count": result["person_count"]}
