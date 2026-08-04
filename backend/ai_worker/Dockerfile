# syntax=docker/dockerfile:1
# ==============================================================================
# Merit.Ai AI Worker -- face/object inference microservice
#
# Build context is `backend/` (see root docker-compose.yml:
#   build: { context: ./backend, dockerfile: ai_worker/Dockerfile }
# ), NOT this ai_worker/ directory, so the COPY steps below can pull the exact
# same app/ai/*.py the Core API uses -- one source of truth for the ArcFace/
# YOLO/anti-spoof logic, never a hand-copied fork of it living in two places
# that can silently drift apart.
# ==============================================================================

FROM python:3.11-slim AS base

# libgl1 / libglib2.0-0: mediapipe's native graph and onnxruntime's image
# pre/post-processing pull in a handful of shared libraries a "slim" Debian
# base doesn't ship. Without these, `import mediapipe` fails at container
# start with an opaque "libGL.so.1: cannot open shared object file" that gives
# no hint it's a missing system package rather than a broken Python install.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 \
        libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# ------------------------------------------------------------------------------
# Builder -- resolve and install Python dependencies into an isolated prefix,
# kept out of the final image except for the installed packages themselves.
# ------------------------------------------------------------------------------
FROM base AS builder
WORKDIR /build
COPY ai_worker/requirements.txt .
# See backend/Dockerfile for why the timeout/retry values are raised: the same
# large wheels, the same failure mode on a slow link.
RUN pip install --no-cache-dir --user --timeout 120 --retries 10 -r requirements.txt

# ------------------------------------------------------------------------------
# Runtime
# ------------------------------------------------------------------------------
FROM base AS runtime

# Non-root: this process only ever handles images handed to it by the Core
# API over the internal Docker network and holds no secrets of its own, but
# there is no reason a model-inference process should run as root.
RUN groupadd --gid 1000 aiworker && useradd --uid 1000 --gid aiworker --create-home --shell /usr/sbin/nologin aiworker

WORKDIR /app
COPY --from=builder /root/.local /home/aiworker/.local
ENV PATH=/home/aiworker/.local/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Only the source this service actually imports: app/ai (the shared
# ArcFace/YOLO/anti-spoof logic), app/core (Settings), and app/__init__.py.
# Deliberately NOT app/api, app/models, app/schemas, app/repositories,
# app/services, app/database, or alembic/ -- none of which this process
# touches, and all of which would otherwise pull sqlalchemy/psycopg2 into an
# image that never opens a database connection.
COPY app/__init__.py ./app/__init__.py
COPY app/core ./app/core
COPY app/ai ./app/ai
# fetch_models.py is what the entrypoint below runs to pre-warm the face/yolo
# weights on boot -- it only imports app.core.config plus lazily-imported AI
# libraries already in this image, never app.database or app.models.
COPY app/utils/__init__.py ./app/utils/__init__.py
COPY app/utils/fetch_models.py ./app/utils/fetch_models.py
COPY ai_worker/__init__.py ./ai_worker/__init__.py
COPY ai_worker/main.py ./ai_worker/main.py

# Model weight cache. Mounted as a named volume in docker-compose.yml so a
# container restart or redeploy doesn't re-pay the ~280MB ArcFace / ~19MB
# YOLO download (see app/utils/fetch_models.py, run once at first boot by
# the entrypoint below). Also covers models/antispoof and models/yolo, both
# of which live under this same root.
RUN mkdir -p /app/models && chown -R aiworker:aiworker /app

COPY ai_worker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

USER aiworker
EXPOSE 8010
VOLUME ["/app/models"]

# Dependency-free, like the Core API's /api/health -- a slow first-boot model
# download must not read to Docker as a crashed container and trigger a
# restart loop mid-download (start-period gives it real room before the
# first check counts against retries).
HEALTHCHECK --interval=30s --timeout=5s --start-period=45s --retries=3 \
    CMD python -c "import urllib.request as r; r.urlopen('http://127.0.0.1:8010/health', timeout=3)" || exit 1

ENTRYPOINT ["/entrypoint.sh"]
CMD ["python", "-m", "uvicorn", "ai_worker.main:app", "--host", "0.0.0.0", "--port", "8010"]
