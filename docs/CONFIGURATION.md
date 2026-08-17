# Configuration

Every setting is an environment variable read by `backend/app/core/config.py`
(76 settings in total). Two example files ship with the project:

| File | Used by |
|---|---|
| `.env.example` → `.env` | The Docker Compose stack |
| `backend/.env.example` → `backend/.env` | Running the backend directly, no Docker |

`docker-compose.yml` lists every variable it passes explicitly rather than using
`env_file`. Anything added to `.env` must also be added there to reach the
container.

Settings not present in either example file keep the defaults in `config.py`.

---

## Required in production

`ENVIRONMENT=production` makes `core-api` refuse to boot if any of these is wrong.

| Variable | Default | Notes |
|---|---|---|
| `ENVIRONMENT` | `development` | `production` enables the startup gates below |
| `SECRET_KEY` | placeholder | Signs every JWT including admin tokens. Must be ≥32 characters and not the placeholder. `python3 -c "import secrets; print(secrets.token_urlsafe(64))"` |
| `POSTGRES_PASSWORD` | — | Compose will not start without it |
| `REDIS_PASSWORD` | — | Compose will not start without it |
| `CORS_ORIGINS` | `http://127.0.0.1:5173,http://localhost:5173` | Comma-separated. Must be non-empty in production |
| `DATABASE_URL` | local Postgres | Built from the `POSTGRES_*` values inside Compose |

---

## Authentication and accounts

| Variable | Default | Notes |
|---|---|---|
| `ALGORITHM` | `HS256` | JWT signing algorithm |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `120` | Session token lifetime |
| `ATTEMPT_TOKEN_GRACE_MINUTES` | `30` | Extra life on the attempt-scoped token past the exam deadline, so a submission retried over a bad connection does not fail on an expired token |
| `PASSWORD_MIN_LENGTH` | `10` | The rest of the policy is a blocklist in `app/core/passwords.py` |
| `REGISTRATION_MODE` | `open` | `open` — anyone may register. `invite` — only addresses already on an exam or organization roster |
| `REQUIRE_EMAIL_VERIFICATION` | `true` | Closes the unverified registration endpoint. Refuses to boot if email is disabled, since that would let nobody register |
| `REQUIRE_CONSENT_ON_SIGNUP` | `true` | Rejects a registration that does not carry the consent the form asks for |
| `AUTO_SEED_ADMIN` | `true` | Seeds roles and the first admin on boot; idempotent |
| `SEED_ADMIN_EMAIL` | `admin@merit.ai` | |
| `SEED_ADMIN_PASSWORD` | *(none)* | No default on purpose — unset in production means "create no admin" |
| `ACTIVATION_TTL_HOURS` | `72` | Single-use examiner activation link lifetime. Days rather than minutes so a Friday approval still works on Monday |
| `NOTIFY_STAFF_ON_LOGIN` | `true` | Emails examiner and admin sign-ins; candidate sign-ins are never emailed |

---

## Proctoring and AI

| Variable | Default | Notes |
|---|---|---|
| `AI_SERVICE_URL` | *(empty)* | Points at `ai-worker`. Empty means run inference in-process |
| `AI_SERVICE_TIMEOUT_SECONDS` | `8.0` | Falls back to local inference on timeout |
| `FACE_MATCH_TOLERANCE` | `0.38` | Cosine distance between ArcFace embeddings; lower is stricter, 0 is identical |
| `FACE_MODEL_NAME` | `buffalo_l` | `buffalo_l` ≈280 MB, best accuracy. `buffalo_s` ≈16 MB, much faster first download |
| `FACE_MODEL_ROOT` | `models/insightface` | Weight cache directory |
| `FACE_MATCHING_ENABLED` | `true` | Kill switch |
| `OBJECT_DETECTION_ENABLED` | `true` | Kill switch |
| `OBJECT_MODEL_ROOT` / `OBJECT_MODEL_WEIGHTS` / `OBJECT_MODEL_ONNX` | YOLO11s | Where the object weights and ONNX export live |
| `POSE_DETECTION_ENABLED` | `true` | Kill switch |
| `ANTISPOOF_MODEL_ROOT` / `ANTISPOOF_MODEL_ONNX` | *(optional)* | Trained liveness classifier. Without one, the built-in detector runs |
| `ANTISPOOF_LIVE_THRESHOLD` | see `config.py` | Score above which a face counts as live |
| `LOCKDOWN_STRIKE_LIMIT` | `3` | Violations before an attempt is auto-submitted |
| `LOCKDOWN_STRIKE_DEBOUNCE_SECONDS` | `3.0` | Collapses one physical action (an alt-tab produces both a blur and a visibility change) into a single strike |
| `NOISE_DB_THRESHOLD` | `65.0` | Reserved; noise detection currently runs entirely client-side |

Per-signal tuning guidance is in [PROCTORING.md](PROCTORING.md).

---

## Coding questions

| Variable | Default | Notes |
|---|---|---|
| `CODE_EXECUTION_ENABLED` | `true` | Needs the host Docker socket mounted |
| `CODE_EXECUTION_DEFAULT_TIMEOUT_SECONDS` | `6` | Per test case |
| `CODE_EXECUTION_MEMORY_LIMIT` | `128m` | Per sandbox container |
| `CODE_EXECUTION_CPUS` | `0.5` | Per sandbox container |
| `CODE_EXECUTION_MAX_CONCURRENT` | `4` | Across all candidates. Roughly `host cores / CODE_EXECUTION_CPUS` |
| `DOCKER_GID` | `999` | The **host's** `docker` group GID, so the container's non-root user can use the mounted socket. Find it with `getent group docker \| cut -d: -f3` |

---

## Database and performance

| Variable | Default | Notes |
|---|---|---|
| `WEB_CONCURRENCY` | `4` | uvicorn workers. `(2 × cores) + 1`, capped by the database |
| `DB_POOL_SIZE` | `5` | Per worker process |
| `DB_MAX_OVERFLOW` | `10` | Per worker process. Total connections are `(pool + overflow) × WEB_CONCURRENCY`; Postgres allows 100 by default |
| `DB_POOL_TIMEOUT_SECONDS` | see `config.py` | Wait before failing to check out a connection |
| `DB_POOL_RECYCLE_SECONDS` | see `config.py` | Recycle connections before a proxy or Postgres drops them |
| `RUN_MIGRATIONS_ON_START` | `true` | Set `false` once migrations run as an explicit deploy step |

---

## Redis, jobs, and rate limiting

| Variable | Default | Notes |
|---|---|---|
| `REDIS_URL` | `redis://redis:6379/0` | |
| `REDIS_PASSWORD` | — | Passed separately from the URL so it never lands whole in a log line |
| `REDIS_SOCKET_TIMEOUT_SECONDS` | see `config.py` | |
| `LOCK_DEFAULT_TTL_SECONDS` | `30` | Distributed lock lease |
| `JOB_MAX_RETRIES` | `3` | RQ retries per job |
| `JOB_TIMEOUT_SECONDS` | `300` | Ceiling before a job counts as stuck |
| `JOB_WORKER_REPLICAS` | `1` | RQ worker containers. Safe to scale freely |
| `AUTH_RATE_LIMIT_MAX_REQUESTS` | `10` | Per window, per identity |
| `AUTH_RATE_LIMIT_WINDOW_SECONDS` | `60` | |
| `AI_RATE_LIMIT_MAX_REQUESTS` | `30` | Proctoring endpoints, keyed on user id rather than IP |
| `AI_RATE_LIMIT_WINDOW_SECONDS` | `60` | |
| `TRUSTED_PROXY_IPS` | private ranges | Networks whose `X-Forwarded-For` may be believed. Empty means trust nobody and use the TCP peer. Get this wrong and either every candidate shares one rate-limit bucket, or a caller can spoof their way out of the limit |

Rate limiting, OTP storage, and locks all fail **closed** with a `503` when Redis
is unreachable.

---

## Email

Every value is optional. With `EMAIL_ENABLED=false` the application runs exactly
as it does without email configured.

| Variable | Default | Notes |
|---|---|---|
| `EMAIL_ENABLED` | `false` | |
| `SMTP_HOST` | `smtp.gmail.com` | |
| `SMTP_PORT` | `587` | 587 with `SMTP_USE_TLS=true` (STARTTLS), or 465 with it false (implicit TLS) |
| `SMTP_USE_TLS` | `true` | |
| `SMTP_USERNAME` / `SMTP_PASSWORD` | *(empty)* | For Gmail, a 16-character App Password from a dedicated account with 2-Step Verification enabled |
| `SMTP_TIMEOUT_SECONDS` | see `config.py` | |
| `EMAIL_FROM` | *(empty)* | Defaults to `SMTP_USERNAME`. Gmail rewrites a mismatched `From:` anyway |
| `EMAIL_FROM_NAME` | `Merit.Ai` | |
| `ADMIN_NOTIFICATION_EMAIL` | *(empty)* | Where access-request notices go. Blank fans out to every active admin |
| `APP_BASE_URL` | first CORS origin | The address email links point at |
| `EMAIL_OUTBOX_MAX_ATTEMPTS` | `5` | |
| `EMAIL_OUTBOX_RETRY_BASE_SECONDS` | `60` | Exponential backoff base |
| `EMAIL_OUTBOX_RETRY_MAX_SECONDS` | `3600` | Backoff ceiling |
| `ACCESS_REQUEST_RENOTIFY_SECONDS` | `900` | Minimum gap between two notices about the same pending request |

Email is queued on Redis and written to the `email_outbox` table *before*
delivery is attempted, so a Redis outage delays a message rather than losing it.

---

## OTP

| Variable | Default |
|---|---|
| `OTP_LENGTH` | `6` |
| `OTP_TTL_MINUTES` | `10` |
| `OTP_MAX_ATTEMPTS` | `5` |
| `OTP_RESEND_COOLDOWN_SECONDS` | `60` |

---

## Privacy and retention

| Variable | Default | Notes |
|---|---|---|
| `BIOMETRIC_CONSENT_VERSION` | `2026-08-04.v1` | Bumping it invalidates previously captured consent |
| `BIOMETRIC_RETENTION_DAYS` | `0` | Days after a candidate's last attempt before their face embedding, face image, ID card, and violation screenshots become eligible for deletion. `0` never auto-deletes — a deliberate default, since destroying evidence is not something to do by accident |
| `BIOMETRIC_PURGE_INTERVAL_HOURS` | `24` | How often the scheduler sweeps |

---

## Scheduler

| Variable | Default |
|---|---|
| `EXAM_REMINDER_ENABLED` | `true` |
| `EXAM_REMINDER_MINUTES_BEFORE` | `5` |
| `EXAM_REMINDER_POLL_SECONDS` | `60` |

---

## Miscellaneous

| Variable | Default | Notes |
|---|---|---|
| `UPLOAD_DIR` | `uploads` | Faces, ID cards, violation screenshots |
| `HTTP_PORT` | `80` | Published port of the `proxy` container |
