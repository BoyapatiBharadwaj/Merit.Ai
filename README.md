# Merit.Ai (formerly "AI Exam Proctor" / "Skillarena.Ai" / "Assessly") — Online Examination System

**Conduct. Monitor. Evaluate.**

A modular, HackerRank-style online exam platform with real-time AI proctoring,
sandboxed coding assessments, and role-based dashboards for admins, examiners,
and students.

**Stack:** FastAPI + SQLAlchemy + PostgreSQL + Alembic + JWT auth (backend) ·
MediaPipe + EasyOCR + Web Audio API (AI/proctoring) · React + Vite + Tailwind
(frontend).

---

## 1. Project Structure

```
Merit.Ai/
├── backend/
│   ├── app/
│   │   ├── core/            # config, security (hashing, JWT), redis_client.py,
│   │   │                    # locks.py (distributed locks), queues.py (RQ queues),
│   │   │                    # rate_limit.py (fail-closed Redis rate limiting)
│   │   ├── database/        # SQLAlchemy engine/session
│   │   ├── models/          # ORM models (one file per table group)
│   │   ├── schemas/         # Pydantic request/response models
│   │   ├── repositories/    # raw DB access (no business logic)
│   │   ├── services/        # business logic, calls repositories
│   │   ├── ai/               # face_service.py, object_service.py, anti_spoof.py, ocr_service.py
│   │   ├── api/v1/          # FastAPI routers (thin controllers)
│   │   ├── scheduler/        # standalone periodic-job process (see 12) -- exam
│   │   │                     # reminders, OTP purge, biometric retention
│   │   ├── worker/           # standalone RQ worker (see 12) -- consumes the
│   │   │                     # emails/reports/default queues over Redis
│   │   ├── utils/           # seed.py (roles + default admin), fetch_models.py
│   │   └── main.py          # app entrypoint
│   ├── alembic/              # DB migrations
│   ├── ai_worker/             # standalone face/object inference microservice (see 3.0/4)
│   │   ├── main.py             # FastAPI app: /face/register, /face/verify, /detect, /health
│   │   ├── requirements.txt    # deliberately a slim subset of backend/requirements.txt
│   │   └── Dockerfile
│   ├── uploads/               # faces/, id_cards/, violations/ (gitignored)
│   ├── Dockerfile             # Core API image (also runs scheduler/worker, see 3.0)
│   ├── entrypoint.sh           # wait-for-db, migrate, seed, warm up OCR weights
│   ├── requirements.txt        # production dependencies only
│   ├── requirements-dev.txt    # + pytest/httpx/fakeredis for section 11 below
│   └── .env.example            # for running the backend directly, no Docker
├── frontend/                  # React (Vite) frontend — "Merit.Ai"
│   ├── src/
│   │   ├── components/        # Navbar, DashboardHeader, Icon, CaptureCard, CodeEditor, ...
│   │   ├── lib/                # api.js, auth.js, theme.js, ui.js, proctoring.js
│   │   ├── pages/              # Home, Features, Pricing, About, FAQ, Contact, Login,
│   │   │                       # Register, StudentDashboard, Profile, Exam, Results,
│   │   │                       # ExaminerDashboard, Admin*/AttemptReport pages, ...
│   │   │   └── examiner/       # exam builder, question builder, per-attempt violation review
│   │   ├── App.jsx             # route table
│   │   └── main.jsx            # entry point
│   ├── package.json
│   ├── Dockerfile              # multi-stage: vite build -> static nginx
│   ├── nginx.conf               # SPA fallback, served on an unprivileged port
│   └── README.md               # frontend-specific setup + structure
├── deploy/
│   └── nginx/                  # the reverse proxy in front of the whole stack (see 3.0)
│       ├── Dockerfile
│       └── nginx.conf
├── docker-compose.yml          # all eight services, wired together (see 3.0)
└── .env.example                 # compose-level config (Postgres/Redis creds, SECRET_KEY, ...)
```

Architecture follows a clean layering: **API (routers) → Services (business
rules) → Repositories (DB access) → Models (SQLAlchemy ORM)**. AI logic lives
in its own `ai/` package so it can be swapped/tested independently -- and, as
of the Docker setup in 3.0, actually deployed independently too, as the
`ai-worker` microservice.

### 1.1 Frontend — `frontend/` (Merit.Ai, React)

A single React app covers the whole product: the marketing/home page; fully
working `/login` and `/register`; fully working student, admin, and
examiner dashboards (exam builder with drag-and-drop reordering and bulk
MCQ import included); `/profile` (face registration + ID card OCR); the full
proctored exam-taking interface at `/exam/:examId` — timer, question
navigator, MCQ + CodeMirror coding questions with sandboxed "Run Sample",
autosave with retry/backoff, mark-for-review, submit, and live AI proctoring
(face/object/pose/gaze checks, tab-switch and fullscreen-exit detection, mic
noise detection) — and a standalone `/results/:attemptId` page with a
result-report PDF download. See `frontend/README.md` for the full
structure breakdown.

---

## 2. Database Design

Normalized PostgreSQL schema (see `backend/alembic/versions/0001_initial_schema.py`
for the exact DDL, and `backend/app/models/` for the ORM definitions):

- **roles / users** — base auth identity, role = admin / examiner / student
- **students / examiners** — 1:1 profile extensions of `users`
- **face_profiles** — one registered face-identity embedding per student (JSON; a 512-d L2-normalised ArcFace vector, produced identically by the AI worker or by the in-process model)
- **exams → sections → questions → options** — exam content hierarchy
- **student_exam_attempts** — one attempt per (student, exam), unique constraint
- **student_answers** — one row per (attempt, question), auto-save target
- **exam_results** — computed once on submit
- **proctor_events** — every violation: type, severity, timestamp, optional screenshot

All FKs use appropriate `ON DELETE` behavior (`CASCADE` for owned children,
`SET NULL`/`RESTRICT` where deleting the parent shouldn't cascade destructively).
Indexes are added on all FK columns and frequently filtered columns
(`status`, `event_type`, `created_at`, etc). Unique constraints prevent a
student from attempting the same exam twice or answering the same question twice.

---

## 3. Setup

### 3.0 Docker (recommended): the whole stack in one command

The root `docker-compose.yml` runs Merit.Ai as eight containers rather than
one monolith:

| Service | What it is | Reachable from |
|---|---|---|
| `proxy` | nginx reverse proxy | The host — this is the **only** published port |
| `frontend` | The React app, built and served as static files | `proxy` only |
| `core-api` | Auth, exams, attempts, organizations, admin, analytics, proctoring orchestration, sandboxed code execution | `proxy` only — no longer published to the host, so the proxy is the single entry point |
| `ai-worker` | Dedicated face-identity + object-detection inference | `core-api` only — never published |
| `postgres` | The one shared database | `core-api` only |
| `redis` | Rate limits, OTP state, distributed locks, and the RQ job queue (see 12) | `core-api`, `scheduler`, `worker` only |
| `scheduler` | Single-replica periodic jobs: exam reminders, OTP purge, biometric retention (see 12) | Talks outward to `postgres`/`redis`; nothing talks to it |
| `worker` | RQ worker(s) draining the emails/reports/default queues (see 12) | Talks outward to `postgres`/`redis`; nothing talks to it |

`core-api` still owns the single Postgres database and every business
transaction exactly as it does when run directly (see 3.2) — nothing about
the request/response/DB-transaction model changed. What moved into its own
container is the AI inference workload: `AI_SERVICE_URL` (see `app/core/config.py`)
is set to `http://ai-worker:8010` automatically, so face matching and object
detection run in the dedicated `ai-worker` container instead of in-process.
If `ai-worker` is ever unreachable, `core-api` transparently falls back to
computing locally instead (it keeps the same AI libraries installed for
exactly this reason — see `backend/Dockerfile`'s header comment) rather than
proctoring simply going dark; a bad exam moment is a slower one, not an
unmonitored one.

Five separate Docker networks keep the blast radius of each service honest
rather than dropping everything onto one flat network: the database is
reachable only from `core-api` (`data-net`), Redis only from `core-api`/
`scheduler`/`worker` (`cache-net`), `ai-worker` only from `core-api`
(`ai-net`), and `frontend`/`core-api` are each reachable only through `proxy`
(`web-net`/`api-net`). A compromised frontend container, for instance, has no
network path to Postgres or Redis at all.

```bash
cp .env.example .env
# Edit .env: set SECRET_KEY, POSTGRES_PASSWORD, and REDIS_PASSWORD (generate
# each with the command shown next to it in the file) before running anything
# but a local throwaway stack. Compose refuses to start at all if
# REDIS_PASSWORD is left unset.

docker compose up --build
```

Open **http://localhost** — that's the `proxy` container, serving the
frontend and forwarding `/api/*` to `core-api` on one origin (so the
browser's requests are same-origin, no CORS involved, matching how
`vite.config.js`'s dev-server proxy already behaves locally). The API's own
Swagger UI is also directly reachable at http://localhost:8000/docs.

On first boot, `core-api`'s `entrypoint.sh` runs migrations and seeds roles +
the default admin account automatically (idempotent — safe on every
restart); `ai-worker`'s own entrypoint pre-downloads its ArcFace/YOLO
weights into a named volume so a container restart doesn't re-pay that
download. Coding questions need the **host's** Docker daemon reachable
from `core-api` (see the `DOCKER_GID` note in `.env.example`) — that
container talks to the same daemon your shell does via a mounted
`docker.sock`, not a nested one.

```bash
docker compose logs -f core-api ai-worker   # watch both AI paths at once
docker compose down                          # stop everything
docker compose down -v                       # ...and also delete the database/model volumes
```

The rest of this section (3.1–3.3) describes the alternative: running each
piece directly on your machine instead, with no Docker at all — useful for
active backend/frontend development with hot-reload.

### 3.1 Database

```bash
cd backend
docker run -d --name merit-ai-postgres -p 5432:5432 \
  -e POSTGRES_USER=merit_ai -e POSTGRES_PASSWORD=change_this_password -e POSTGRES_DB=merit_ai \
  postgres:16-alpine
```

This matches the credentials in `backend/.env.example`, and the ones the
Docker Compose stack uses (`POSTGRES_USER` / `POSTGRES_PASSWORD` /
`POSTGRES_DB` in the root `.env`), so the same client settings work either way.

> **Connecting a database client to the Compose stack?** Use the values above,
> not `localhost:5432` — the `postgres` service publishes no host port on
> purpose, so nothing outside Docker can reach it. Either
> `docker compose exec postgres psql -U merit_ai -d merit_ai`, or open a
> temporary tunnel:
>
> ```bash
> docker run --rm -d --name pgproxy --network meritai_data-net -p 15432:5432 \
>   alpine/socat tcp-listen:5432,fork,reuseaddr tcp-connect:postgres:5432
> ```
>
> then connect to `localhost:15432`, and `docker rm -f pgproxy` when done. **If your local
`backend/.env` points at different credentials or a different database
name** (e.g. a Postgres you installed directly instead of via Docker),
that's fine — just make sure whichever Postgres is actually running has a
database with the name in `DATABASE_URL`, created and reachable, before
running migrations below.

### 3.2 Backend

Redis is required here too, not just in Docker — rate limiting, OTP signup/
password-reset, and distributed locks all fail closed with a `503` rather
than silently working around a missing Redis (see 12). A plain, unauthenticated
local instance is enough for development and needs no `.env` changes, since
`backend/.env.example`'s defaults already point at it:

```bash
docker run -d --name merit-ai-redis -p 6379:6379 redis:7-alpine
```

```bash
cd backend
python -m venv venv && source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements-dev.txt   # includes requirements.txt, plus pytest/httpx/etc. for section 11 below

cp .env.example .env          # then edit DATABASE_URL if it doesn't match your Postgres setup

# Run migrations
alembic upgrade head

# Seed roles + a default admin account (admin@examproctor.com / Admin@12345)
python -m app.utils.seed

# Download the AI model weights (see "Model setup" below). Optional but
# strongly recommended -- without it the first student request pays the
# download cost, and object detection does not run at all.
python -m app.utils.fetch_models

# Start the API
uvicorn app.main:app --reload --port 8000
```

API docs (Swagger UI): http://localhost:8000/docs

#### Model setup

`python -m app.utils.fetch_models` prepares every model up front, so the first
exam of the day isn't the thing that pays for a 300MB download:

| Name | What it is | Size | Required? |
|---|---|---|---|
| `face` | ArcFace identity matching (InsightFace `buffalo_l`) | ~280MB | Yes, for face matching |
| `yolo` | YOLO11s object detection + an ONNX export | ~19MB | Yes, for phone/book/person detection |
| `antispoof` | Trained liveness classifier | ~2MB | No — a built-in detector runs without it |
| `ocr` | EasyOCR weights for ID cards | ~100MB | Yes, for ID verification |

Use `--only NAME` to do one at a time. It is idempotent, and it prints exactly
what failed and why rather than dying on the first problem — each proctoring
signal degrades independently, so a missing model disables one check rather
than breaking the app.

Two notes worth knowing:

- **Object detection is only as good as the weights being present.** Until
  `fetch_models --only yolo` has run (or `ultralytics` is installed so it can
  self-download), `POST /proctoring/objects/detect` reports itself unavailable
  and the frontend stops polling for it. That's by design, but it does mean
  phone/book/second-person detection is silently off until the weights exist.
- **The trained anti-spoofing model is opt-in.** There is no stable canonical
  URL for a MiniFASNet ONNX export, and auto-pulling an unverified binary from
  a random fork is a bad trade for something that decides whether a student is
  cheating — so `fetch_models` tells you where to put one instead of guessing.
  Until then the built-in classical detector runs (texture, colour, glare, and
  an FFT screen-moiré cue). Tune it by adjusting the thresholds in `app/ai/anti_spoof.py` directly against
your own captures. (An earlier `tools/liveness_probe.py` helper is referenced in
older notes but is not present in this repository.) Use `python -` with real*.jpg
  --spoof fake*.jpg` to check and tune it against your own cameras.

**Note on AI dependencies:** Face *identity* matching needs a real face-recognition
model, not just MediaPipe (which is only used to cheaply count faces in a frame).
The backend runs **ArcFace** (`insightface` + `onnxruntime`) in-process, and both
packages ship prebuilt wheels - no `cmake`, no C++ toolchain. Model weights
(`buffalo_l`, ~280MB) download on first use, so the first face registration after
a cold start is slow; set `FACE_MODEL_NAME=buffalo_s` for a ~16MB pack instead.

For better throughput, run the standalone `backend/ai_worker` service (see
`backend/ai_worker/main.py`) and set `AI_SERVICE_URL` to point at it — the
backend prefers it automatically when configured. This is exactly what the
Docker setup in 3.0 does by default (`AI_SERVICE_URL=http://ai-worker:8010`),
but it also runs standalone outside Docker:

```bash
cd backend
pip install -r ai_worker/requirements.txt
uvicorn ai_worker.main:app --port 8010
# then, in the main backend's .env:
#   AI_SERVICE_URL=http://localhost:8010
```

Both paths run the same ArcFace model (literally the same `app/ai/face_service.py`
module — the worker imports and runs it directly, rather than reimplementing
it) and therefore produce the *same* embedding, so a profile registered
against one verifies correctly against the other, and the worker can be
added or removed without invalidating anything already stored. ID-card OCR uses
`easyocr`, which pulls in PyTorch (CPU build) - a large (~500MB+) install, and
the first OCR request after boot is slower while it downloads its recognition
weights. No external OCR binary (Tesseract) is required anymore.

### 3.3 Frontend — `frontend/`

```bash
cd frontend
npm install
npm run dev
```

Open http://localhost:5173. `npm run dev` proxies `/api/*` to
`http://localhost:8000` (the FastAPI backend), so no CORS setup is needed in
dev. See `frontend/README.md` for the full page/component breakdown.

**Login flow:**
1. Log in as `admin@examproctor.com` / `Admin@12345` → create an Examiner account.
2. Log out, register a new Student account (or log in as the Examiner).
3. As Examiner: create an exam → add section(s) → add MCQ questions → Publish.
4. As Student: register your face + verify ID card on the Profile page, then
   start the exam from the Student Dashboard.

---

## 3.4 Organizations — who can see which exams

Exams are scoped to an **organization**. An examiner belongs to one (derived
from the organization name on their account), every exam they create inherits
it, and only students enrolled in that organization can see or start it.

This closed a real hole: previously `GET /exams/available` returned every
published exam to every student, and `POST /attempts/start/{exam_id}` checked
only status and timing — so any student could sit any exam on the platform by
naming its id. Filtering the list alone would have been cosmetic; the check
lives in `organization_service.can_student_access_exam`, which the list, the
exam detail route, and `start_attempt` all share.

**How a student gets access**

1. An examiner adds their email address to the organization roster
   (Examiner Dashboard → *Student roster*). Pasting a column from a
   spreadsheet works — commas, semicolons, newlines and spaces all split.
2. The student registers with that address and is linked automatically.
   Order doesn't matter: enrolling someone who already has an account links
   them immediately.

A student nobody has enrolled has no organization, and therefore sees and can
start **nothing**. That is deliberate — the failure mode of an access check
should be "no access", not "all access".

**Per-exam restriction.** By default an exam is open to the whole
organization. On any exam, *Who can take this* lets you paste a list of
emails; adding even one flips that exam to an allow-list. "Remove all"
reopens it.

Entry is by email rather than by picking registered students, because an
examiner sets an exam up before the cohort has signed up. Addresses with no
account yet show as **Invited** and start working the moment that person
registers — access matches on the email itself, so it never depends on a
background resolution step having run.

Note that being on an exam's list is *necessary, not sufficient*: the
organization check still applies. An email that isn't on your student roster
is accepted (you may be about to enrol them) but flagged **Not enrolled**,
because it grants nothing on its own.

**Passwords.** Examiner passwords are bcrypt-hashed, so they can never be read
back — not by an admin, not from the database. If a password is lost, an admin
resets it to a new one rather than looking the old one up. Making passwords
retrievable would mean one database leak exposes every account.

**Approving an access request does not create a password.** It creates the
account with an unusable random secret and emails a single-use activation link
(`ACTIVATION_TTL_HOURS`, default 72). The new examiner chooses their own
password from that link, and is the only person who ever knows it. Approving
used to have the admin type a password that was then emailed in plain text —
which meant the credential sat in a mailbox indefinitely, the admin knew it, and
"only this examiner could have done that" was never true of anything the account
did.

An admin *can* still set a password directly (the `+ New Examiner` form, and
candidate password resets, which candidates cannot do themselves). Those
accounts are flagged `must_change_password`, and every authenticated page shows
the owner a banner saying somebody else knows their password, with the route to
replace it.

**Invitation-only signup.** `REGISTRATION_MODE` defaults to `open`, which is
right for evaluating the software and wrong for an institution: signup is
available to anyone who finds the URL, so the candidate list is whoever happened
to sign up. Set it to `invite` and only addresses already on an exam roster or an
organization roster can register. That reuses the roster you had to build anyway
to run the exam — there is no second allow-list to keep in sync.

**Notes**

- Two examiners who enter the same organization name share one tenant and one
  roster (matched case- and whitespace-insensitively). That's how a department
  with several examiners works from one student list.
- Removing a student from the roster revokes access to future exams but keeps
  results they've already submitted.
- Migration `0008` backfills existing installs: organizations are created from
  existing examiner organization names, exams inherit their examiner's, and
  existing students are placed into the organization they've actually been
  sitting exams for. In-progress attempts keep working.
- The one case migration `0008` deliberately won't guess: a student with no
  attempt history on an install that already has several organizations is left
  unassigned, for an examiner to enrol. Assigning them arbitrarily could hand
  someone another institution's exams.

---

## 4. AI Proctoring — how it works

| Feature | Implementation |
|---|---|
| Face detection / count | MediaPipe face detector on each submitted frame (presence/count only, not identity) |
| Face matching | ArcFace (`insightface`), via the AI worker when `AI_SERVICE_URL` is set, else the same model in-process. One 512-d embedding format and one cosine-distance threshold on both paths - MediaPipe Face Mesh is never used for identity |
| Liveness / anti-spoofing | Trained ONNX classifier when one is installed; otherwise a built-in detector combining face-region texture, colour spread, glare, and an FFT screen-moiré cue (`app/ai/anti_spoof.py` — the AI worker imports this same module directly rather than a copy, same as face/object detection). Runs before a face is matched. Thresholds are tuned in `app/ai/anti_spoof.py` |
| Multiple faces | Same MediaPipe detection pass, flagged when `> 1` |
| Phone / book / multiple people | YOLO11s (`POST /proctoring/objects/detect`) - runs **in-process** via `onnxruntime` (`app/ai/object_service.py`), or through the AI worker when `AI_SERVICE_URL` is set. Per-class confidence floors: phone 0.35, person 0.45, book 0.55. Reports itself unavailable and stops polling if the weights were never fetched |
| Head pose ("looking away") | Local, no AI worker needed: MediaPipe Face Mesh landmarks + OpenCV `solvePnP` against a generic 3D face model (`app/ai/pose_service.py`), flagged on sustained yaw/pitch beyond threshold (`POST /proctoring/pose/check`) |
| Gaze deviation | Same local pass: iris-center landmarks (Face Mesh iris refinement) relative to eye corners give a coarse "looking hard to one side" signal - not a calibrated gaze model |
| External monitor / extra display | Frontend-only: Chrome's Window Management API (`getScreenDetails()`), since this isn't reliably inferable from webcam frames. No-ops on unsupported browsers/denied permission |
| Fullscreen exit | `fullscreenchange` event listener (frontend) |
| Tab switching | `visibilitychange` + `window.blur` listeners (frontend) |
| Copy/paste/right-click | `contextmenu`, `copy`, `paste`, `cut`, and blocked key combos (frontend) |
| Noise detection | Voice-activity heuristic: Web Audio API speech-band energy ratio + adaptive ambient floor, sustained over consecutive 1s checks (frontend) - not just raw volume |
| ID card OCR | `easyocr` (deep-learning text detector/recognizer), token-overlap match against registered name |

All of the above (head pose, gaze, phone/book/person count) require the signal to
persist across several consecutive checks before logging a violation, the same
debounce pattern used for noise detection - a single glance, blink, or head turn
never counts on its own.

All violations are POSTed to `/api/v1/proctoring/events` and stored in
`proctor_events` with a severity (`low`/`medium`/`high`), viewable by
Examiners/Admins per-exam or per-attempt. Violation screenshots are decoded and
written to disk in a FastAPI background task so logging a violation never blocks
on file I/O.

**Privacy/perf note:** live face frames are sent to the backend only every
8 seconds (configurable in `frontend/src/lib/proctoring.js`) to keep bandwidth
and inference load reasonable for a capstone-scale deployment — not a
continuous video stream.

---

## 5. Analytics

`GET /api/v1/analytics/exams/{exam_id}` (examiner who owns the exam, or admin)
returns attempt status breakdown, score min/avg/max, a 5-bucket score
distribution, and violation counts by type/severity. `GET
/api/v1/analytics/platform` (admin only) returns platform-wide totals. Both
are rendered as Chart.js charts (loaded from cdnjs) - a doughnut + bar chart
on the Admin Dashboard, and a bar + doughnut chart behind a new "Analytics"
button in the Examiner Dashboard's per-exam builder panel.

## 6. Frontend

Dark mode is available site-wide (toggle in the navbar/dashboard header) via
CSS custom properties and `frontend/src/lib/theme.js`, persisted in
`localStorage` and applied before first paint to avoid a flash of the wrong
theme. The exam page's proctoring panel shows a live per-signal status list
(Face / Objects / Head pose / Gaze / Audio / Monitor) and running tab-switch
and violation counters, both updated via callbacks from the
`createProctoring()` controller (`frontend/src/lib/proctoring.js`) rather
than polling from the page itself.

## 7. Exam-taking Features

- Timer with auto-submit on timeout (`frontend/src/pages/Exam.jsx`)
- One question at a time with Previous/Next + a jump-to-question grid
- Auto-save on every option selection (`PUT /attempts/{id}/answer`)
- Question order randomized per-attempt when `randomize_questions=true`
- Fullscreen is requested on exam start; exiting is logged as a violation
- Result computed server-side on submit (never trust client-side scoring)

---

## 8. PDF Result Reports & Certificates

Students (and the examiner/admin who can already view a given attempt via
`_can_access_attempt`) can download two generated PDFs once an attempt is
submitted:

- `GET /api/v1/attempts/{attempt_id}/result/pdf` — a one-page result report
  (score, percentage, correct/incorrect/unattempted breakdown, proctoring
  violation count).
> **Not implemented yet:** a completion certificate
> (`/attempts/{id}/certificate/pdf`). This README previously documented that
> endpoint as if it existed — it does not, in the API or the frontend. Verifiable
> certificates (unique number, QR code, public verification endpoint, revocation
> status) are a planned feature, not a shipped one.

The report is generated on-the-fly with `reportlab` (`app/services/pdf_service.py`)
— pure Python, no compiled toolchain — and streamed back as
`application/pdf` with no temp files written to
disk. The frontend's Results page (`frontend/src/pages/Results.jsx`) adds
a "Report" download button, fetched with the auth token via
`Api.downloadFile()` (a plain `<a href>` won't work here since the endpoint
requires a Bearer token) and saved through a temporary object URL.

## 9. Auto-save Robustness & Exam Recovery

Every answer selection is saved immediately (`PUT /attempts/{id}/answer`), but
a dropped connection no longer silently loses it:

- **Failed saves are queued client-side** (`Exam.jsx`) and retried with
  exponential backoff (2s -> 20s cap), both on a timer and immediately when
  the browser fires an `online` event. A non-intrusive banner
  ("Connection lost...") shows only while something is unsynced.
- **Submission gets the same treatment** - if the network drops exactly at
  submit time, it retries quietly instead of stranding the student with a
  stopped timer and a one-shot error.
- **Reload/crash recovery**: `POST /attempts/start/{exam_id}` is idempotent
  for an in-progress attempt - it returns the *same* attempt rather than
  erroring, with a server-computed `remaining_seconds` (so the timer can't be
  rewound by reloading). A new `GET /attempts/{attempt_id}/answers` endpoint
  returns every previously-saved answer in one call, so the question
  navigator repaints its answered/unanswered state immediately after a
  reload instead of only learning it as the student revisits each question.
- **Timer drift correction**: the client re-syncs against the server's
  `remaining_seconds` every 90s (via the same idempotent start-attempt call),
  so a long disconnect doesn't leave the local countdown out of sync.
- Mark-for-review state is a client-only convenience persisted in
  `localStorage` per exam, so it also survives a reload.

## 10. Coding Questions & Sandboxed Execution

Exams can include coding questions alongside MCQs. Each coding question has a
language (**Python or JavaScript** for this release), starter code, a set of
stdin/stdout test cases (sample cases are shown to the student; hidden ones
are grading-only), and a per-test-case time limit.

**Sandboxing:** `app/services/code_runner_service.py` runs every test case in
its own ephemeral, `--network none` Docker container (`python:3.11-slim` /
`node:20-slim`) with a memory cap, CPU cap, and process-count limit, via the
`docker` CLI already on the host -- no separate microservice or
Docker-in-Docker setup required. The student's code is base64-encoded and
handed to a small trusted bootstrap script (never interpolated into a shell
string), which decodes and executes it while the container's real
stdin/stdout stay free for the test case's actual input/output.

**Fails closed:** if `docker` isn't installed or isn't on PATH,
`code_runner_service.is_available()` returns `False` and coding questions are
reported as ungraded/unavailable rather than crashing the request -- the same
graceful-degradation pattern used for the optional AI worker. Run
`docker pull python:3.11-slim` / `docker pull node:20-slim` once ahead of
time so a student's first "Run" or the exam's first submission isn't slowed
down by an image pull.

**Grading flow**, split in two so Docker cost stays low and predictable:

- `PUT /attempts/{id}/code-answer` -- pure autosave, debounced client-side
  (`Exam.jsx`, ~1.2s after the last keystroke, flushed immediately on
  navigation/submit). Never runs the code.
- `POST /attempts/{id}/code-answer/run` -- the student's "Run Sample Tests"
  button. Executes against sample test cases only, ungraded, for quick
  feedback. Hidden test cases are never sent to or run against the client's
  ad-hoc request.
- Authoritative grading happens **once**, at final exam submission
  (`attempt_service._grade_coding_answer`): the saved code is run against
  every test case (sample + hidden), and marks are awarded proportionally
  (`marks * passed / total`, rounded) -- so partial credit is possible, not
  strictly all-or-nothing.

**Frontend:** the exam page (`Exam.jsx`) swaps the MCQ options panel for a
`CodeEditor` component wrapping CodeMirror 5 (loaded via CDN in `index.html`,
no build step) when the current question is a coding question, with a
language badge, "Reset", "Run Sample" button, and a sample-test-results
panel. The Examiner Dashboard's question form has a Type selector
(MCQ / Coding) with language, starter code, time limit, and a repeatable
test-case list (input / expected output / sample toggle).

**Known limitation** (explicitly scoped this way, not an oversight): each
test case spins up a fresh container rather than a pooled/warm one, so
grading N test cases takes roughly N container-startup times. Fine for a
capstone-scale project (a handful of test cases per question); a production
system would want a container pool or a queue-based worker.

## 11. Running the tests

```bash
cd backend
pip install -r requirements-dev.txt   # requirements.txt plus pytest/httpx/fakeredis (and optionally pgserver)
pytest
```

`backend/tests/` covers the auth flow (registration/login/role checks), the
full exam workflow (create → publish → attempt → answer → submit → score),
the AI helper modules (image validation, the liveness heuristic, and the
worker-vs-in-process selection in `face_service`, plus the property that both
paths share one embedding format - all mocked, so no GPU, network access or
model download is required to run them), and DB transaction/rollback
behavior. Tests run against an in-memory SQLite database and `fakeredis`, so
neither a real Postgres nor a real Redis instance is needed.

## 12. Background Jobs, Rate Limiting & Redis

Redis backs four things: request rate limiting (`app/core/rate_limit.py`),
one-time-passcode storage for signup/password-reset (`app/services/
otp_redis_store.py`), short-lived distributed locks (`app/core/locks.py`)
that keep a periodic job or a duplicate-prevention check from racing itself
across replicas, and an RQ job queue (`app/core/queues.py`) with three
queues — `emails`, `reports`, `default` — so a burst of OTP emails never sits
behind a slow PDF report job.

**Fails closed, not open.** Every one of those four fails its *own* request
with a `503` when Redis is unreachable, rather than silently granting the
request, skipping the OTP check, or double-running a job (`redis_client.py`'s
module docstring spells out the exact boundary). PostgreSQL remains the
permanent record of everything durable regardless of Redis's health — most
visibly the `email_outbox` table, which every transactional email is written
to *before* delivery is attempted, so a Redis outage delays a queued email
rather than losing it.

**Two dedicated single-purpose containers**, both sharing the `core-api`
image so a job can import the same services/models/settings the API uses
rather than a second copy of them:

- `scheduler` — a plain asyncio process (not a FastAPI app; it serves no
  traffic). Runs exam reminders, OTP purge, and biometric retention on their
  own intervals, each pass taking a Redis lock first. Pinned to exactly one
  replica in `docker-compose.yml` — this is what makes "only one of these
  ever runs at a time" true by construction, not just by convention.
- `worker` — an RQ worker draining the `emails`/`reports`/`default` queues.
  Scales freely (`JOB_WORKER_REPLICAS`); RQ workers claim jobs from Redis
  atomically, so more replicas only means more throughput.

## 13. Violations, Reviewer Decisions & Results Visibility

**No standalone violations list.** Every logged violation is reviewed in the
context of the student who triggered it — `GET /attempts/{id}/staff-report`,
surfaced at `/examiner/attempts/:attemptId` and `/admin/attempts/:attemptId`
— rather than a flat cross-student feed. Each violation there shows its proof
screenshot (`GET /proctoring/events/{id}/screenshot`, only if one was
captured) alongside a computed risk score for the attempt as a whole.

**Reviewer decisions.** `PATCH /proctoring/events/{id}/decision` sets one of
`pending` / `confirmed` / `dismissed` (labelled "Misleading" in both the
examiner and admin UIs) on a single violation — an audit annotation only,
never touching scoring or the attempt's already-final status. The exam's own
owning examiner can decide on their candidates' violations directly; an admin
can decide on any exam's.

**Live exam CSV export.** `GET /attempts/exam/{exam_id}/export` (owning
examiner or admin) returns one row per candidate who has sat the exam —
name, roll number, violation count, start/end time, status, score, result —
regenerated fresh from the database on every download rather than a
physically maintained file, so it is always complete and current with no
separate sync step to forget.

**Results visibility**, set per exam at creation time (`show_results`,
`results_release_mode`): whether a candidate sees their own score/pass-fail
outcome at all, and if so, whether that happens immediately on submission or
only after the exam's `end_time`. This is a separate gate from the
pre-existing `release_results_at`/`show_answers_on_release` pair, which
governs only the answer *key* (correct options and explanations) — withholding
the score implies withholding the key too, but delaying the key alone does not
delay the score. See `Exam.results_released` vs. `Exam.answer_key_released`
in `backend/app/models/exam.py` for the exact rule.

**Candidate account corrections.** An admin can edit a candidate's name,
email, or roll number (`PATCH /admin/candidates/{id}`). Editing an
already-face-verified candidate's name or email unlocks their identity and
flags the account for re-verification automatically; an admin can also flag
it manually (`POST /admin/candidates/{id}/require-reverification`) without
changing anything else. Every edit — and every re-verification requirement it
triggers — is written to the activity log with the actual before/after
values, not just "candidate updated."

## 14. Extending this project

- Add pagination to admin/examiner list endpoints for larger datasets
- Add refresh tokens (current JWT is a single long-lived access token)
- Add a `screenshot_path` capture on `face_mismatch`/`multiple_faces` events
  (the AI service already saves violation screenshots when the frontend
  passes `screenshot_base64`)
- Tune `FACE_MATCH_TOLERANCE` for your camera and lighting conditions
- Add WebSocket-based live monitoring dashboard for examiners



