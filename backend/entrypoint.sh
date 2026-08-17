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

# Migrations on boot: convenient, and the wrong default for a mature production deployment.
if [ "${RUN_MIGRATIONS_ON_START:-true}" = "true" ]; then
    echo "[core-api] Running migrations (alembic upgrade head)..."
    alembic upgrade head
else
    echo "[core-api] Skipping migrations (RUN_MIGRATIONS_ON_START=false)."
    echo "[core-api] Current schema revision:"
    alembic current || echo "[core-api] Could not read the current revision."
fi

# Idempotent: seed.py only ever creates the roles/admin when they don't already exist and never
# overwrites an existing password, so leaving this on is safe.
if [ "${AUTO_SEED_ADMIN:-true}" = "true" ]; then
    echo "[core-api] Seeding roles + first admin (idempotent)..."
    python -m app.utils.seed
fi

echo "[core-api] Warming up ID-card OCR weights (idempotent; this always runs in-process regardless of AI_SERVICE_URL)..."
if ! python -m app.utils.fetch_models --only ocr; then
    echo "[core-api] OCR warm-up reported problems -- continuing; ID verification retries lazily on first use." >&2
fi

exec "$@"
