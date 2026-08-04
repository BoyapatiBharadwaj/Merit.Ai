# syntax=docker/dockerfile:1
# ==============================================================================
# Merit.Ai Core API -- auth, exams, attempts, organizations, admin, analytics,
# proctoring orchestration, and sandboxed code execution.
#
# Keeps its full requirements.txt (including mediapipe/insightface/onnxruntime/
# ultralytics/easyocr) rather than trimming to a "thin" API image, on purpose:
# app/ai/face_service.py and object_service.py fall back to computing locally
# whenever the ai-worker service is unset or unreachable (see AI_SERVICE_URL
# in app/core/config.py), and ID-card OCR always runs here regardless of the
# worker. Splitting the *heavy, independently-scalable* inference workload
# into backend/ai_worker/ was the point of this service split -- not shrinking
# this image, which stays the one place all of the graceful-degradation logic
# this app already relies on keeps working exactly as documented.
# ==============================================================================

FROM python:3.11-slim AS base

# libgl1/libglib2.0-0: mediapipe's native graph needs these even when it's
# only ever used as a *fallback* path here (see the module docstring above).
# docker.io: the `docker` CLI code_runner_service.py shells out to for
# sandboxed coding-question execution (Docker-outside-of-Docker -- this
# container talks to the *host's* Docker daemon via a mounted socket, see
# docker-compose.yml, rather than running its own nested daemon).
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 \
        libglib2.0-0 \
        docker.io \
        curl \
    && rm -rf /var/lib/apt/lists/*

# ------------------------------------------------------------------------------
# Builder
# ------------------------------------------------------------------------------
FROM base AS builder
WORKDIR /build

# Some transitive deps (e.g. cryptography, if a prebuilt wheel isn't
# available for this exact base image/arch) need a compiler to build from
# source. Kept in the builder stage only -- the runtime image never carries
# a toolchain.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
# --timeout/--retries: this install still pulls several 20-60MB wheels
# (onnxruntime, mediapipe, opencv, torch-cpu). pip's default 15s socket timeout
# is measured per read, so on a slow or contended link a large wheel dies
# part-way through and takes the whole build with it -- which is precisely how
# the CUDA-torch problem documented in requirements.txt first showed up, as a
# ReadTimeoutError rather than as "you are downloading 2GB you cannot use".
# Generous values here cost nothing on a fast connection and turn a transient
# stall into a retry instead of a failed build.
RUN pip install --no-cache-dir --user --timeout 120 --retries 10 -r requirements.txt

# ------------------------------------------------------------------------------
# Runtime
# ------------------------------------------------------------------------------
FROM base AS runtime

# Non-root app user. Deliberately *not* added to a `docker` group baked into
# the image -- the host's docker.sock GID varies by machine, so
# docker-compose.yml grants access at container-start time via `group_add`
# (see the DOCKER_GID note in .env.example) rather than this Dockerfile
# guessing a GID that would only be right by coincidence.
RUN groupadd --gid 1000 appuser && useradd --uid 1000 --gid appuser --create-home --shell /usr/sbin/nologin appuser

WORKDIR /app
COPY --from=builder /root/.local /home/appuser/.local
ENV PATH=/home/appuser/.local/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Application code. Alembic + its config travel with the image so the
# entrypoint can run migrations against whatever DATABASE_URL the environment
# supplies, without a separate "migrations" image to keep in sync.
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini .
COPY pytest.ini .
COPY tests ./tests

RUN mkdir -p /app/uploads/faces /app/uploads/id_cards /app/uploads/violations /app/models \
    && chown -R appuser:appuser /app

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

USER appuser
EXPOSE 8000
VOLUME ["/app/uploads", "/app/models"]

# The app's own dependency-free liveness endpoint (see app/main.py's
# docstring on why it must never touch the database) -- exactly the check an
# orchestrator should use, and exactly what this Dockerfile's HEALTHCHECK
# reuses rather than inventing a second one.
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -f http://127.0.0.1:8000/api/health || exit 1

ENTRYPOINT ["/entrypoint.sh"]
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
