# syntax=docker/dockerfile:1
# Merit.Ai AI worker -- stateless face/object inference service. No database, no secrets.

FROM python:3.11-slim AS base

# libgl1 / libglib2.0-0: mediapipe's native graph and onnxruntime's image pre/post-processing
# pull in a handful of shared libraries a "slim" Debian base doesn't ship.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 \
        libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# ------------------------------------------------------------------------------
# --- Builder: install Python dependencies into an isolated prefix ---
# ------------------------------------------------------------------------------
FROM base AS builder
WORKDIR /build
COPY ai_worker/requirements.txt .
# See backend/Dockerfile for why the timeout/retry values are raised: the same large wheels, the
# same failure mode on a slow link.
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --user --timeout 120 --retries 10 -r requirements.txt

# --- - ---
# --- Runtime ---
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

# Only the source this service actually imports: app/ai (the shared ArcFace/YOLO/anti-spoof
# logic), app/core (Settings), and app/__init__.py.
COPY app/__init__.py ./app/__init__.py
COPY app/core ./app/core
COPY app/ai ./app/ai
# fetch_models.py is what the entrypoint below runs to pre-warm the face/yolo weights on boot.
COPY app/utils/__init__.py ./app/utils/__init__.py
COPY app/utils/fetch_models.py ./app/utils/fetch_models.py
COPY ai_worker/__init__.py ./ai_worker/__init__.py
COPY ai_worker/main.py ./ai_worker/main.py

# Model weight cache.
RUN mkdir -p /app/models && chown -R aiworker:aiworker /app

COPY ai_worker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

USER aiworker
EXPOSE 8010
VOLUME ["/app/models"]

# Dependency-free, like the Core API's /api/health.
HEALTHCHECK --interval=30s --timeout=5s --start-period=45s --retries=3 \
    CMD python -c "import urllib.request as r; r.urlopen('http://127.0.0.1:8010/health', timeout=3)" || exit 1

ENTRYPOINT ["/entrypoint.sh"]
CMD ["python", "-m", "uvicorn", "ai_worker.main:app", "--host", "0.0.0.0", "--port", "8010"]
