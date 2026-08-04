"""HTTP bridge to the optional isolated AI worker."""
import json
import logging
from urllib import error, request

from app.core.config import settings

logger = logging.getLogger("ai_worker_client")


def _post(path: str, payload: dict) -> dict | None:
    if not settings.AI_SERVICE_URL:
        return None
    url = f"{settings.AI_SERVICE_URL.rstrip('/')}{path}"
    body = json.dumps(payload).encode("utf-8")
    req = request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with request.urlopen(req, timeout=settings.AI_SERVICE_TIMEOUT_SECONDS) as response:
            return json.loads(response.read().decode("utf-8"))
    except error.HTTPError as err:
        # The worker IS reachable here -- it responded, just with an error
        # status (most commonly its ArcFace/YOLO model failing to load, e.g.
        # a blocked model-weight download on first use). Previously this was
        # caught by the same branch as "worker unreachable" below, so a
        # genuine worker-side failure silently fell back to the local model
        # path and surfaced as "no AI worker configured" -- actively
        # misleading when the worker is in fact configured and running.
        # Logging the worker's own error body here is the fast way to tell
        # the two failure modes apart.
        try:
            detail = err.read().decode("utf-8")
        except Exception:
            detail = str(err)
        logger.warning("AI worker returned HTTP %s for %s: %s", err.code, url, detail)
        return None
    except (error.URLError, TimeoutError, json.JSONDecodeError) as err:
        logger.warning("AI worker unreachable at %s: %s", url, err)
        return None


def register_face(image_base64: str) -> dict | None:
    return _post("/face/register", {"image_base64": image_base64})


def verify_face(image_base64: str, encoding: list[float], tolerance: float) -> dict | None:
    return _post("/face/verify", {"image_base64": image_base64, "encoding": encoding, "tolerance": tolerance})


def detect_objects(image_base64: str) -> dict | None:
    return _post("/detect", {"image_base64": image_base64})