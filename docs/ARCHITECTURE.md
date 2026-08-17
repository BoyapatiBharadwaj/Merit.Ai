# Architecture

## Services

The Compose stack runs eight containers rather than one monolith. Each row's
"Reachable from" column is enforced by Docker network membership, not convention.

| Service | Responsibility | Reachable from |
|---|---|---|
| `proxy` | nginx reverse proxy; routes `/api/*` to `core-api`, everything else to `frontend` | The host — the **only** published port |
| `frontend` | React SPA, built once and served as static files | `proxy` only |
| `core-api` | Auth, exams, attempts, organizations, admin, analytics, proctoring orchestration, sandboxed code execution | `proxy` only |
| `ai-worker` | Face identity and object-detection inference | `core-api` only |
| `postgres` | The single shared database | `core-api`, `scheduler`, `worker` |
| `redis` | Rate-limit counters, OTP state, distributed locks, RQ job queue | `core-api`, `scheduler`, `worker` |
| `scheduler` | Periodic jobs: exam reminders, OTP purge, biometric retention. Pinned to one replica | Outbound only |
| `worker` | RQ worker draining the `emails` / `reports` / `default` queues | Outbound only |

`scheduler` and `worker` share the `core-api` image so a job imports the same
services, models, and settings the API uses rather than a second copy of them.

## Networks

Five networks instead of one flat bridge:

| Network | Members |
|---|---|
| `data-net` | `postgres` ↔ `core-api`, `scheduler`, `worker` |
| `cache-net` | `redis` ↔ `core-api`, `scheduler`, `worker` |
| `ai-net` | `ai-worker` ↔ `core-api` |
| `api-net` | `core-api` ↔ `proxy` |
| `web-net` | `frontend` ↔ `proxy` |

A compromised `frontend` container has no network path to Postgres, Redis, or
the AI worker. `ai-worker` can reach neither the database nor Redis. `proxy` can
reach only `core-api` and `frontend`.

## AI inference: worker plus fallback

`AI_SERVICE_URL` points `core-api` at `http://ai-worker:8010`. When set, face
matching and object detection run in the dedicated container. When the worker is
unreachable, `core-api` computes locally instead — the AI libraries stay
installed in its image for exactly this reason.

Both paths run the *same* `app/ai/face_service.py` module: the worker imports and
executes it rather than reimplementing it. One ArcFace model, one 512-dimensional
embedding format, one cosine-distance threshold. A profile registered against one
path verifies correctly against the other, so the worker can be added or removed
without invalidating anything already stored.

`ai-worker` deliberately leaves `AI_SERVICE_URL` unset in its own environment;
that is what makes its internal calls take the local branch instead of calling
itself.

## Backend layering

```
api/v1/        FastAPI routers — request parsing, auth dependencies, HTTP status
services/      business logic and transaction boundaries
repositories/  database queries; no business rules
models/        SQLAlchemy ORM
schemas/       Pydantic request/response contracts
```

Routers never touch the ORM directly and repositories never raise HTTP errors.
Cross-cutting concerns live in `core/`: configuration and its production gates,
password hashing and JWT issuance, the Redis client, distributed locks, RQ queue
definitions, and rate limiting.

## Data model

21 tables. The exam content hierarchy and the attempt records are the two spines.

**Identity and access**

- `roles`, `users` — base auth identity; role is admin, examiner, or student
- `students`, `examiners` — 1:1 profile extensions of `users`
- `organizations`, `organization_members` — tenancy and cohort rosters
- `access_requests` — examiner account requests awaiting admin approval
- `otp_codes` — signup and password-reset codes
- `face_profiles` — one 512-d L2-normalised ArcFace embedding per student
- `activity_logs` — audit trail with before/after values

**Exam content**

- `exams` → `sections` → `questions` → `options`
- `exam_participants` — per-exam allow-list, keyed on email so it works before
  the candidate has an account

**Attempts and outcomes**

- `student_exam_attempts` — one attempt per (student, exam), unique-constrained
- `student_answers` — one row per (attempt, question); the autosave target
- `exam_results` — computed once, server-side, on submit
- `attempt_resets` — audit record of an examiner-granted retake
- `proctor_events` — every violation with type, severity, timestamp, optional
  screenshot, and reviewer decision

**Infrastructure**

- `email_outbox` — the permanent delivery record for every transactional email

Foreign keys use `CASCADE` for owned children and `SET NULL` / `RESTRICT` where
deleting the parent should not cascade destructively. Composite indexes match
queries the application actually issues — `proctor_events(attempt_id, created_at)`
for the candidate timeline, `proctor_events(severity, created_at)` for the review
queue, `student_exam_attempts(exam_id, status)` for live sessions. Unique
constraints prevent a candidate attempting the same exam twice or answering the
same question twice.

Exact DDL lives in `backend/alembic/versions/`; the ORM definitions in
`backend/app/models/`.

## Request flow: sitting an exam

1. `POST /api/v1/attempts/start/{exam_id}` — idempotent. Returns the existing
   in-progress attempt if there is one, with a server-computed
   `remaining_seconds` so reloading cannot rewind the timer. Issues an
   attempt-scoped token whose lifetime is the deadline plus
   `ATTEMPT_TOKEN_GRACE_MINUTES`.
2. The browser starts local tracking at ~12 fps (face count, head pose, gaze)
   and periodic server calls for identity and object detection.
3. `PUT /attempts/{id}/answer` and `/code-answer` on every change. Failures queue
   client-side and retry with exponential backoff, flushing on an `online` event.
4. `POST /proctoring/events` batches violations. Screenshots are decoded and
   written to disk in a background task so logging never blocks on file I/O.
5. `POST /attempts/{id}/submit` grades server-side inside one transaction:
   MCQ answers against the key, coding answers against every test case
   (sample and hidden) with proportional marks.

## Failure behaviour

Everything Redis backs — rate limiting, OTP storage, distributed locks, the job
queue — fails **closed**, returning `503` rather than silently granting the
request, skipping the OTP check, or double-running a job. PostgreSQL stays the
permanent record regardless of Redis health: every transactional email is written
to `email_outbox` *before* delivery is attempted, so a Redis outage delays a
queued email rather than losing it.

The AI path is the deliberate exception. Face and object inference degrade to the
in-process fallback, and an individual signal whose weights are missing reports
itself unavailable and stops being polled. Proctoring gets weaker, never absent.
