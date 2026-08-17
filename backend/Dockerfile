# syntax=docker/dockerfile:1
# Merit.Ai Core API -- FastAPI app, also used as the image for the scheduler and RQ worker.

FROM python:3.11-slim AS base

# libgl1/libglib2.0-0: required by mediapipe's native graph, used here as the local
# inference fallback when ai-worker is unreachable. curl: the HEALTHCHECK below.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 \
        libglib2.0-0 \
        curl \
    && rm -rf /var/lib/apt/lists/*

# --- - ---
# --- Builder ---
FROM base AS builder
WORKDIR /build

# Some transitive deps (e.g. cryptography, if a prebuilt wheel isn't available for this exact
# base image/arch) need a compiler to build from source.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
# --timeout/--retries: this install still pulls several 20-60MB wheels (onnxruntime, mediapipe,
# opencv, torch-cpu). pip's default 15s socket timeout is measured per read, so on a slow or
# contended link a large wheel dies part-way through and takes the whole build with it -- which
# is precisely how the CUDA-torch problem documented in requirements.txt first showed up, as a
# ReadTimeoutError rather than as "you are downloading 2GB you cannot use".
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --user --timeout 120 --retries 10 -r requirements.txt

# --- - ---
# --- Runtime ---
FROM base AS runtime

# Non-root app user. Deliberately *not* added to a `docker` group baked into the image.
RUN groupadd --gid 1000 appuser && useradd --uid 1000 --gid appuser --create-home --shell /usr/sbin/nologin appuser

WORKDIR /app
COPY --from=builder /root/.local /home/appuser/.local
# Just the `docker` client binary -- see the note above the apt-get install above for why this
# replaces the `docker.io` apt package (dockerd + containerd + runc this container never runs,
# for a client it only ever points at the host's socket).
COPY --from=docker:27-cli /usr/local/bin/docker /usr/local/bin/docker
ENV PATH=/home/appuser/.local/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Application code.
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini .
# Operator diagnostics, run with `docker compose exec core-api python scripts/check_email.py`.
COPY scripts ./scripts

RUN mkdir -p /app/uploads/faces /app/uploads/id_cards /app/uploads/violations /app/models \
    && chown -R appuser:appuser /app

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

USER appuser
EXPOSE 8000
VOLUME ["/app/uploads", "/app/models"]

# The app's own dependency-free liveness endpoint (see
# app/main.py's docstring on why it must never touch the database).
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -f http://127.0.0.1:8000/api/health || exit 1

ENTRYPOINT ["/entrypoint.sh"]
# Workers come from WEB_CONCURRENCY at runtime, so the same image serves a single-worker dev
# stack and a multi-worker deployment without a rebuild.
CMD ["sh", "-c", "exec python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers ${WEB_CONCURRENCY:-1}"]
