"""FastAPI application entrypoint."""
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.api.router import api_router
from app.core.config import settings
from app.database.session import SessionLocal

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("app")

# Create every static/upload directory the app writes to up front so a first
# request never fails with a FileNotFoundError because a folder is missing.
for directory in (settings.UPLOAD_DIR, settings.FACES_DIR, settings.ID_CARDS_DIR, settings.VIOLATIONS_DIR):
    try:
        os.makedirs(directory, exist_ok=True)
    except OSError:
        logger.exception("Could not create required upload directory: %s", directory)
        raise

if not settings.cors_origins:
    logger.warning(
        "CORS_ORIGINS is empty or contains no valid entries; no browser frontend will be able to call this API. "
        "Set CORS_ORIGINS in the environment to a comma-separated list of trusted frontend origins."
    )

@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Start and stop background workers around the app's serving lifetime.

    A lifespan context rather than the older on_event pair: on_event is
    deprecated in this FastAPI version, and more usefully, startup and shutdown
    for one subsystem live next to each other here instead of in two handlers
    that can drift apart.

    Both halves are guarded. A scheduler that cannot start must not stop the API
    from serving -- exams still run and candidates still sit them, only the
    courtesy reminder is lost -- and an error while stopping must not turn a
    clean shutdown into a hung container.
    """
    from app.services import reminder_service

    try:
        reminder_service.start()
    except Exception:
        logger.exception("Could not start the exam reminder scheduler; the API will run without it")

    yield

    try:
        await reminder_service.stop()
    except Exception:
        logger.exception("Error while stopping the exam reminder scheduler")


# Interactive docs are development-only.
#
# FastAPI serves /docs, /redoc and /openapi.json unauthenticated by default, and
# the OpenAPI schema is a complete map of all 90-odd endpoints: every path,
# parameter, request shape and role requirement. That is a genuinely useful
# artefact while building and a free reconnaissance document in production --
# particularly next to an unauthenticated public form (access requests) and the
# student self-registration endpoint.
#
# Serving None for all three is what actually removes the routes; setting only
# docs_url would leave the schema itself readable at /openapi.json, which is the
# part worth having anyway.
_docs_urls = (
    {"docs_url": None, "redoc_url": None, "openapi_url": None}
    if settings.is_production
    else {"docs_url": "/docs", "redoc_url": "/redoc", "openapi_url": "/openapi.json"}
)

app = FastAPI(title="AI Exam Proctor API", description="Online examination system with basic AI proctoring.",
              version="1.0.0", lifespan=lifespan, **_docs_urls)

if settings.is_production:
    logger.info("Interactive API docs are disabled (ENVIRONMENT=production).")

# Restricted to the explicit, trusted frontend origins configured via CORS_ORIGINS.
# Wildcards are stripped out in Settings.cors_origins, so this can never silently
# widen to "allow everything".
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
# JSON question/exam payloads and proctoring event batches compress extremely
# well; 500 bytes is above the size where the CPU cost stops being worth it.
app.add_middleware(GZipMiddleware, minimum_size=500)


# Header values are static, so build the dict once rather than per request.
#
# HSTS is deliberately omitted here and left to the TLS-terminating reverse
# proxy: emitting it from the app would also emit it over plain HTTP in local
# development, pinning developers' browsers to https://localhost for months
# with no easy way to undo it.
_SECURITY_HEADERS = {
    # Stops a browser from MIME-sniffing a stored upload (e.g. a violation
    # screenshot) into something executable.
    "X-Content-Type-Options": "nosniff",
    # This API is never meant to be framed; blocks clickjacking against any
    # HTML error/doc pages it serves.
    "X-Frame-Options": "DENY",
    # Don't leak authenticated exam URLs (which contain attempt/exam ids) to
    # third-party sites via the Referer header.
    "Referrer-Policy": "strict-origin-when-cross-origin",
    # The API itself has no legitimate use for these device APIs. The exam
    # frontend is a separate origin and is unaffected by this header.
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
    # Nothing served from this origin should ever execute as a page.
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'; base-uri 'none'",
}


@app.middleware("http")
async def request_context_middleware(request: Request, call_next):
    """Attach a request id, emit one structured access log line, and apply
    security headers.

    The request id is echoed back as X-Request-ID and included in the access
    log, so a user-reported failure ("it said 500 at 14:32") can be tied to
    the exact log line and stack trace without guessing from timestamps. An
    inbound X-Request-ID is honoured so a reverse proxy's id wins and the two
    logs correlate.
    """
    request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:16]
    request.state.request_id = request_id
    started = time.perf_counter()

    response = await call_next(request)

    duration_ms = (time.perf_counter() - started) * 1000
    response.headers["X-Request-ID"] = request_id
    for header, value in _SECURITY_HEADERS.items():
        response.headers.setdefault(header, value)

    logger.info(
        "request_id=%s method=%s path=%s status=%s duration_ms=%.1f",
        request_id, request.method, request.url.path, response.status_code, duration_ms,
    )
    return response


app.include_router(api_router)
# No public static mount for UPLOAD_DIR here on purpose: that used to serve
# every student's face photo to anyone on the network with no authentication
# (see app/api/v1/proctoring.py's get_face_photo for the replacement, gated
# to the owning student, admins, and examiners).


def _request_id(request: Request) -> str:
    """Best-effort: the middleware sets this, but an error raised before it
    runs (or in a test calling a handler directly) must not itself crash."""
    return getattr(request.state, "request_id", "-")


@app.exception_handler(SQLAlchemyError)
async def database_error_handler(request: Request, exc: SQLAlchemyError):
    """Never let a DB error leak internals or crash the worker; log and return a clean 503."""
    request_id = _request_id(request)
    logger.exception("request_id=%s Database error handling %s %s", request_id, request.method, request.url.path)
    # The request id is echoed in the body as well as the header so a user can
    # read it straight off an error screen and quote it in a support ticket.
    return JSONResponse(
        status_code=503,
        content={"detail": "A database error occurred. Please try again shortly.", "request_id": request_id},
        headers={"X-Request-ID": request_id},
    )


@app.exception_handler(Exception)
async def unhandled_error_handler(request: Request, exc: Exception):
    """Catch-all so an unexpected bug in AI/OCR/etc. returns JSON instead of crashing the request."""
    if isinstance(exc, (HTTPException, RequestValidationError)):
        raise exc
    request_id = _request_id(request)
    logger.exception("request_id=%s Unhandled error handling %s %s", request_id, request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={"detail": "An unexpected error occurred. Please try again.", "request_id": request_id},
        headers={"X-Request-ID": request_id},
    )


@app.get("/api/health", tags=["Health"])
def health_check():
    """Liveness: is the process up? Deliberately dependency-free.

    An orchestrator restarts a container that fails its liveness probe, so
    this must NOT check the database -- a brief DB blip would otherwise
    trigger a restart storm across every replica at once, turning a
    recoverable outage into an outage plus a thundering herd of cold starts.
    """
    return {"status": "ok"}


@app.get("/api/health/ready", tags=["Health"])
def readiness_check():
    """Readiness: can this instance actually serve traffic?

    Checked by the load balancer, which pulls an unready instance out of
    rotation without killing it. Every meaningful request touches the
    database, so an instance that cannot reach it should not receive any.
    """
    try:
        db = SessionLocal()
        try:
            db.execute(text("SELECT 1"))
        finally:
            db.close()
    except SQLAlchemyError:
        logger.exception("Readiness check failed: database unreachable")
        return JSONResponse(status_code=503, content={"status": "unavailable", "database": "unreachable"})
    return {"status": "ok", "database": "ok"}