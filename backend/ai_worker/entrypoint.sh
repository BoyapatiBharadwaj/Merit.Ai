#!/bin/sh
# Pre-warm the face + object-detection weights on every boot, before serving
# any request. fetch_models.py is idempotent (already-downloaded weights are
# verified, not re-fetched), so this costs nothing on a warm /app/models
# volume and only actually downloads on a genuinely fresh volume.
#
# Deliberately non-fatal: a blocked model download (offline build host, no
# route to GitHub) must not stop this container from starting at all -- see
# fetch_models.py's own module docstring. The service still starts; the
# affected model lazily retries its own load on first real request and
# reports the same "unavailable" contract either way (a clean 503 from
# /face/register, or {"available": false, ...} from /detect), which
# app/ai/ai_worker_client.py on the Core API side already treats as "fall
# back to local computation there instead."
set -u

echo "[ai-worker] Warming up local models (face, yolo)..."
if ! python -m app.utils.fetch_models --only face --only yolo; then
    echo "[ai-worker] Model warm-up reported problems -- starting anyway." >&2
    echo "[ai-worker] Affected signals will report themselves unavailable until weights are present." >&2
fi

exec "$@"
