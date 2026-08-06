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
# curl: the HEALTHCHECK below.
#
# The `docker` CLI code_runner_service.py shells out to for sandboxed
# coding-question execution (Docker-outside-of-Docker -- this container talks
# to the *host's* Docker daemon via a mounted socket, see docker-compose.yml,
# rather than running its own nested daemon) used to come from apt's
# `docker.io` package. On Debian that package is the FULL engine --
# dockerd, containerd, runc, and the CLI together -- to get a client binary
# that only ever runs `docker run`/`docker exec` against a socket this
# container doesn't even own the server side of. The daemon and containerd
# are pure dead weight here and were a meaningful share of this image's
# install time. Copied as just the CLI binary from Docker's own official
# image below instead (see the COPY --from=docker-cli stage).
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 \
        libglib2.0-0 \
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
#
# The cache mount (not `--no-cache-dir`, which this used to use) is what makes
# every build after the first fast: it persists pip's downloaded wheels
# (torch-cpu, onnxruntime, mediapipe, ...) across builds on this same Docker
# host, keyed by the mount target rather than by this layer -- so editing
# requirements.txt (invalidating this RUN) only re-downloads whatever actually
# changed, instead of every unchanged multi-hundred-MB wheel along with it.
# It costs nothing in final image size: BuildKit cache mounts are never part
# of any committed layer, unlike pip's default cache directory would be.
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --user --timeout 120 --retries 10 -r requirements.txt

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
# Just the `docker` client binary -- see the note above the apt-get install
# above for why this replaces the `docker.io` apt package (dockerd +
# containerd + runc this container never runs, for a client it only ever
# points at the host's socket).
COPY --from=docker:27-cli /usr/local/bin/docker /usr/local/bin/docker
ENV PATH=/home/appuser/.local/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Application code. Alembic + its config travel with the image so the
# entrypoint can run migrations against whatever DATABASE_URL the environment
# supplies, without a separate "migrations" image to keep in sync.
#
# Deliberately NOT tests/ or pytest.ini: pytest isn't even installed here
# anymore (moved to requirements-dev.txt, never part of this image's
# requirements.txt install), so a copy of the test suite alongside it could
# never run -- it was dead weight in every deployed image.
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini .
# Operator diagnostics, run with `docker compose exec core-api python
# scripts/check_email.py`. Small, and worth having inside the image: the whole
# point is to observe the configuration and network the container actually has,
# which you cannot do from the host.
COPY scripts ./scripts

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
# Workers come from WEB_CONCURRENCY at runtime, so the same image serves a
# single-worker dev stack and a multi-worker deployment without a rebuild.
# Shell form (not exec form) because the value must be expanded by a shell --
# exec form would pass the literal string "$WEB_CONCURRENCY" to uvicorn.
#
# NOTE: with >1 worker, REDIS_URL must be set or rate-limit counters become
# per-process and every limit is silently multiplied. app/main.py warns about
# exactly that at startup.
CMD ["sh", "-c", "exec python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers ${WEB_CONCURRENCY:-1}"]
