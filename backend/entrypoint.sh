#!/bin/sh
# Core API container entrypoint: wait for Postgres to actually accept
# connections (belt-and-suspenders behind docker-compose's own
# `depends_on: condition: service_healthy` -- a restart or a slow-to-recover
# database shouldn't crash-loop this container), run migrations, seed roles +
# the default admin, warm up ID-card OCR weights, then hand off to the real
# process (`exec "$@"`, i.e. whatever CMD/`docker compose run` supplied).
set -eu

echo "[core-api] Waiting for the database..."
python - <<'PY'
import sys
import time

from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError

from app.core.config import settings

deadline = time.monotonic() + 60
last_error = None
while time.monotonic() < deadline:
    try:
        engine = create_engine(settings.DATABASE_URL)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        break
    except OperationalError as error:
        last_error = error
        time.sleep(2)
else:
    print(f"[core-api] Database never became reachable: {last_error}", file=sys.stderr)
    sys.exit(1)
print("[core-api] Database is reachable.")
PY

echo "[core-api] Running migrations (alembic upgrade head)..."
alembic upgrade head

# Set AUTO_SEED_ADMIN=false in a real deployment once you've created your own
# admin account -- seed.py is idempotent (it only ever creates roles/the admin
# if they don't already exist, never overwrites a password), so leaving this
# on is safe, but a fresh install still gets the well-known
# admin@examproctor.com / Admin@12345 pair documented in the README, and that
# pair is public the moment this repository is. Rotate it after first login.
if [ "${AUTO_SEED_ADMIN:-true}" = "true" ]; then
    echo "[core-api] Seeding roles + default admin (idempotent)..."
    python -m app.utils.seed
fi

echo "[core-api] Warming up ID-card OCR weights (idempotent; this always runs in-process regardless of AI_SERVICE_URL)..."
if ! python -m app.utils.fetch_models --only ocr; then
    echo "[core-api] OCR warm-up reported problems -- continuing; ID verification retries lazily on first use." >&2
fi

exec "$@"
