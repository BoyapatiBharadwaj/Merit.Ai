# Merit.Ai — Complete Project Analysis

**Date:** 4 August 2026
**Method:** Full read of every backend and frontend source file, both READMEs, all 15 Alembic migrations, and the existing `CODEBASE_REVIEW.md`. Every claim below that could be checked mechanically was checked — the test suite was actually executed, the enum/migration gap was proved by replaying migration DDL against the Python enums, and `.env` was parsed. Nothing in the project was modified.

---

## 1. At a glance

| | |
|---|---|
| **What it is** | A HackerRank-style online examination platform with live AI proctoring, sandboxed coding assessments, and three role-based dashboards |
| **Backend** | FastAPI + SQLAlchemy 2.0 + PostgreSQL + Alembic + JWT (HS256) — 77 Python modules, 91 endpoints, 15 migrations |
| **Frontend** | React 18 + Vite 5 + Tailwind 3 + React Router 6 — 23 pages, 20 components, 9 lib modules, ~13,800 LOC |
| **AI/proctoring** | MediaPipe Tasks Vision (client, ~12fps), ArcFace/InsightFace (server, identity), YOLO11s (objects), EasyOCR (ID cards), Web Audio API (noise) |
| **Sandboxing** | Ephemeral Docker containers per test case, `--network none`, read-only rootfs, all caps dropped |
| **Tests** | 314 backend tests. **310 pass, 1 skipped, 3 fail** — all 3 failures are environment artifacts, not code defects (see §5) |
| **Version control** | **None.** No `.git` directory anywhere |

**Overall verdict:** this is a genuinely well-engineered codebase — better than most production systems at this scale, and far better than typical capstone work. The layering is honoured without exception across ~40 backend modules, the access-control model is centralised rather than duplicated, and the comments explain *past incidents and rejected alternatives* rather than restating the code. The findings below are the exceptions, and two of them are real production-breaking bugs.

---

## 2. Architecture

### Backend layering (followed consistently, no violations found)

```
API routers (app/api/v1/*)     thin controllers — auth, params, HTTP shape
        ↓
Services (app/services/*)      business rules, transactions, authorisation
        ↓
Repositories (app/repositories/*)  raw DB access, no business logic
        ↓
Models (app/models/*)          SQLAlchemy ORM
```

`app/ai/` sits alongside as an independently swappable package, and `app/core/` holds config + security + rate limiting.

### The access-control model — the strongest part of the design

`organization_service.can_student_access_exam` is the **single authority** for "may this student see or sit this exam," and it is reused (not reimplemented) by three call sites: the student exam list, the exam detail route, and `attempt_service.start_attempt`. `attempt_service._get_owned_in_progress_attempt` then re-checks it on *every* read and write inside a live attempt — so revoking a student's roster membership mid-exam actually revokes it, rather than leaving the already-started attempt working.

Two subtle things done right that are easy to get wrong:

- **The enumeration oracle is closed.** The identity gate (`require_exam_ready`) runs *after* the access check, so an outsider can't distinguish "verify your ID first" (403, exam exists) from "exam not available" (404, exam doesn't) and map out exam IDs across organisations.
- **The failure mode is "no access", not "all access".** An exam with a `NULL` organization_id is treated as inaccessible, not public. A student nobody enrolled sees nothing.

### Proctoring architecture (two-tier, both tiers interchangeable)

Face identity runs on ArcFace either in the optional `ai_worker` (when `AI_SERVICE_URL` is set) or in-process — deliberately the *same* 512-d L2-normalised embedding and the same cosine threshold on both paths, so a profile registered against one verifies against the other. That's an improvement over an earlier dlib fallback that produced an incompatible 128-d Euclidean embedding.

Per `PROCTORING_TUNING.md`, presence / face count / head pose / gaze moved client-side to MediaPipe Tasks Vision at ~12fps; only identity matching still round-trips to the server, now every 12s instead of 3s.

### Lockdown (server-authoritative, correctly)

Strike counts are derived from persisted `ProctorEvent` rows, not client state — so a page refresh, a `localStorage` wipe, or unhooking every JS listener cannot reset them. The debounce window collapses one physical Alt-Tab (which fires blur + visibilitychange + fullscreenchange together) into a single strike. `lockdown.js`'s own header is refreshingly honest about the ENFORCED / DETERRED / NOT POSSIBLE boundary.

---

## 3. What's already right (and doesn't need attention)

- **Code-execution sandbox.** `--network none`, read-only rootfs with a size-capped `noexec,nosuid` tmpfs, `--cap-drop ALL`, `no-new-privileges`, non-root UID, `--pids-limit 64`, memory *and* memory-swap capped (so the container can't swap past its "hard" limit), a bounded concurrency semaphore released on every path including the timeout branch, and output caps enforced **inside** the container as well as on the host. Student code is base64'd through argv, never interpolated into a shell string. This is the highest-risk component in the project and nothing here needs changing.
- **Secrets validation.** `config._validate_secrets` hard-fails startup in production if `SECRET_KEY` is the placeholder or under 32 chars. (See §4.3 for why it's currently inert.)
- **Security headers.** `nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy`, `Permissions-Policy`, and `CSP: default-src 'none'` on every response. HSTS is deliberately delegated to the TLS proxy with a good reason given.
- **Health checks split correctly.** `/api/health` is dependency-free (liveness — so a DB blip doesn't trigger a restart storm), `/api/health/ready` touches the DB (readiness).
- **Thread safety.** MediaPipe Solution objects and the ArcFace model are each behind a lock, because FastAPI runs sync `def` routes on a threadpool and concurrent `.process()` calls on one MediaPipe instance can *hang* rather than raise.
- **Race conditions handled.** Double-submit → `IntegrityError` caught and the existing result returned rather than a 503. Lockdown termination racing a manual submit → logged and swallowed.
- **No SQL injection.** Every query is parameterised through SQLAlchemy; no f-string interpolation into SQL. `get_or_create` deliberately uses `func.lower(...) ==` rather than `ilike()` specifically because `%`/`_` in a caller-supplied org name would otherwise act as wildcards and bond a new examiner into someone else's tenant.
- **No XSS surface in React.** Zero uses of `dangerouslySetInnerHTML`, `innerHTML`, or `eval`.
- **Image decoding is validated.** `image_utils.decode_image` enforces size, pixel count, and an allow-list of formats before anything touches Pillow's decoders.
- **Accessibility.** Skip-to-content link, a real focus trap with keyboard cycling in the lockdown overlay, `aria-live` regions rather than colour-only signalling.

---

## 4. Findings

### 4.1 🔴 Critical — two enum values will crash against a real PostgreSQL database

**Verified mechanically** by replaying every `sa.Enum(...)` creation and every `ALTER TYPE ... ADD VALUE` across all 15 migrations, then diffing against `app/models/enums.py`:

```
eventtype    values reachable via migrations: 19
MISSING from migrations (eventtype):    ['screen_share_stopped']
MISSING from migrations (questiontype): ['multi_select']
```

- `questiontype` was created in migration `0004` as exactly `("mcq", "coding")`. Migration `0011` added the `selected_option_ids_json` column for multi-select but **never added the enum value**. Creating a multi-select question against Postgres raises `InvalidTextRepresentation`.
- `eventtype` gained values in migrations `0002`, `0003`, `0005`, and `0007`, but `screen_share_stopped` was never added. This one is worse than it looks: it's in `lockdown_service.STRIKE_EVENT_TYPES`, so **a student who stops screen-sharing mid-exam triggers a failed insert** — surfacing as a 503 from the global SQLAlchemy handler at the exact moment lockdown is supposed to act.

Both are invisible to the test suite because tests run on SQLite via `Base.metadata.create_all()`, which always reflects the current Python enum regardless of migration history. **Fix:** one new migration adding both `ALTER TYPE ... ADD VALUE IF NOT EXISTS` statements. This was correctly identified in `CODEBASE_REVIEW.md`; this analysis confirms it independently.

### 4.2 🔴 Critical — no version control

There is no `.git` anywhere. A project with 314 tests, 15 migrations, and this much visible iteration behind it has no history, no branching, no revert. `git init` + the existing `.gitignore` (which is already comprehensive and correctly excludes `.env`, `models/`, `uploads/`, `*.onnx`) + an initial commit is a ten-minute, very high-leverage fix.

### 4.3 🟠 High — the production secrets gate is currently inert

`backend/.env` contains only 8 keys: `DATABASE_URL`, `SECRET_KEY`, `ALGORITHM`, `ACCESS_TOKEN_EXPIRE_MINUTES`, `UPLOAD_DIR`, `CORS_ORIGINS`, `AI_SERVICE_URL`, `AI_SERVICE_TIMEOUT_SECONDS`.

**`ENVIRONMENT` is not among them**, so it defaults to `"development"` and `_validate_secrets` degrades every problem to a log warning. The `SECRET_KEY` in the current file is a real 86-character secret (good), but the guard that's supposed to *guarantee* that in production never engages as configured. Anyone deploying by copying this `.env` gets a silent downgrade of the single highest-severity misconfiguration check in the project.

**Fix:** add `ENVIRONMENT=production` to the deployment `.env` (and consider making the default `"production"` in `config.py`, so the fail-safe direction is "refuse to boot" rather than "warn and continue").

### 4.4 🟠 High — CPU-expensive AI endpoints have no rate limit

Rate limiting is applied to exactly two routes: `/auth/login` and `/auth/register/student`. Not rate-limited:

| Endpoint | Cost |
|---|---|
| `POST /proctoring/face/verify` | ArcFace inference, **serialised behind a global lock** |
| `POST /proctoring/objects/detect` | YOLO11s inference |
| `POST /proctoring/pose/check` | MediaPipe FaceMesh, **serialised behind a global lock** |
| `POST /proctoring/id-card/verify` | EasyOCR (PyTorch), **serialised behind a global lock** |

Any authenticated student — no in-progress attempt required, `detect_objects` and `check_pose` don't even take an `attempt_id` — can loop these and monopolise the worker threadpool. Because three of them serialise on process-wide locks, a single client can stall proctoring for *every* concurrent exam. The same `rate_limit()` dependency already in the codebase applies cleanly here.

### 4.5 🟠 High — unbounded request body on the batch events endpoint

`ProctorEventBatchCreate` caps the list at 50 events, but each event's `screenshot_base64` is `max_length=7_000_000`. That's a **~350 MB request body**, fully parsed into the API process's memory by Pydantic, decoding to up to **250 MB written to disk in one request**. There is no app-level body-size limit and no reverse proxy in the documented setup.

Compounding it: `_save_violation_screenshot` validates that the payload is base64 and under 5 MB, but — unlike `image_utils.decode_image` — **never validates that the bytes are actually an image**. Arbitrary content gets written with a `.jpg` extension and later served by `FileResponse(..., media_type="image/jpeg")`. The `nosniff` header plus `CSP: default-src 'none'` make that largely inert as an execution vector, but it remains a "write arbitrary bytes to server disk" primitive with no quota, per attempt or per student.

**Fix:** drop the per-event screenshot ceiling in a batch (or cap total batch bytes), route screenshots through `decode_image` so only real images land on disk, and add a per-attempt cap on stored evidence.

### 4.6 🟠 High — no retention or deletion path for biometric data

The platform stores, indefinitely and with no expiry: face embeddings (`face_profiles.encoding`), registered face photos (`uploads/faces/`), ID-card photos (`uploads/id_cards/`), and webcam violation screenshots (`uploads/violations/`). There is no delete endpoint, no purge job, and no retention policy anywhere in the codebase. `POST /users/students/{id}/unlock-identity` explicitly *keeps* the face profile ("deleting a biometric should be an explicit, separate action") — but that explicit separate action was never built.

For a system whose entire purpose is collecting biometrics from students, this is the most significant compliance gap in the project. There is a `/privacy` page in the frontend; whatever it promises is not currently enforceable in code.

### 4.7 🟡 Medium — third-party scripts loaded with no Subresource Integrity

`frontend/index.html` loads Chart.js and CodeMirror 5 (JS + CSS + two language modes) from cdnjs with **no `integrity` attribute**. `lib/faceMesh.js` dynamically imports MediaPipe Tasks Vision, its WASM bundle, and a `.task` model file from jsDelivr, also unpinned by hash.

This matters more here than in a typical app: the page executing that code is the **exam page**, which is the one origin holding camera, microphone, and screen-share permissions. A CDN compromise or a hostile DNS response inside a school network yields arbitrary JS with access to all three, plus the bearer token in `localStorage`.

**Fix:** add SRI hashes to the `index.html` tags (cdnjs publishes them), and either self-host the MediaPipe bundle or pin it behind a hash. `PROCTORING_TUNING.md` already documents self-hosting as the workaround for jsDelivr being blocked — the same change closes this.

### 4.8 🟡 Medium — README documents a certificate endpoint that no longer exists

The root README §8 documents `GET /api/v1/attempts/{attempt_id}/certificate/pdf` in detail ("a landscape completion certificate"), §1 advertises "report/certificate PDF downloads," and `frontend/README.md` repeats it twice more.

The endpoint does not exist. `pdf_service.py` exposes only `build_result_report`. The frontend never calls it. And the test suite contains `test_certificate_route_no_longer_exists` — which **passes** — so this was a deliberate removal that four pieces of documentation never caught up with. Either restore it or delete those four claims.

### 4.9 🟡 Medium — user-facing copy promises emails the system cannot send

Three places tell an access requester "you'll receive an email with your login credentials once an administrator approves your account": `RequestAccess.jsx`'s success panel, the backend's own response message in `access_requests.py`, and `Home.jsx`'s "Institutions & Examiners" card.

There is no SMTP client, no SendGrid, no mail capability of any kind in the backend. The actual mechanism is `CredentialsHandoff.jsx` — a one-time on-screen panel the admin copies and delivers manually. That design is deliberate and sound (passwords are bcrypt-hashed and genuinely unrecoverable); the copy just describes a different product.

### 4.10 🟡 Medium — the default admin credential is documented and unguarded

`utils/seed.py` creates `admin@examproctor.com` / `Admin@12345` and *prints the password*, with no production check and no forced rotation. Both READMEs publish the pair. `config.py` fail-fasts on a placeholder `SECRET_KEY` in production but gives the seeded admin no equivalent treatment — even though it's the same class of "credential anyone who has seen this repo already knows."

### 4.11 🟡 Medium — examiner-authored exam instructions are never shown to students

`Exam.instructions` is wired end to end — column, schema, service, and the examiner-facing `ExamDetailsForm`. All five frontend references live in `ExaminerDashboard.jsx`; **zero** live in `Exam.jsx`. The student's pre-exam "Instructions & exam rules" panel is a hardcoded generic list. An examiner who writes custom instructions has no way to discover the student never sees them.

### 4.12 🟡 Medium — three tests are non-portable and fail on Windows

`test_ai_helpers.py` hardcodes `/tmp/does-not-matter.jpg` in three tests:

```
test_register_face_uses_arcface_worker_when_configured
test_register_face_uses_local_arcface_when_worker_not_configured
test_local_and_worker_embeddings_share_one_format
```

They fail with `PermissionError` in a restricted sandbox and will fail with `FileNotFoundError` on Windows, which is the platform this project is being developed on. `tmp_path` (pytest's built-in fixture, already available) fixes all three. Every other test in the suite passes.

### 4.13 🟢 Low — documentation drift beyond the items above

| Claim | Location | Reality |
|---|---|---|
| "17 Violation Types Tracked" | `Home.jsx` stats strip | `EventType` has **20** members; **19** by the file's own stated methodology (excluding `lockdown_terminated` as an outcome) |
| "face frames sent every 8 seconds" | README §4 | `FACE_IDENTITY_INTERVAL_MS = 12000` |
| README §4 proctoring table | README | Predates the client-side move documented in `PROCTORING_TUNING.md` — face count, head pose, and gaze no longer poll the server on the primary path |
| "280-plus backend tests" | `CODEBASE_REVIEW.md` | **314** collected |
| `docker compose up -d` | README §3.1 | No `docker-compose.yml` in the tree |
| `python tools/liveness_probe.py` | README §3.2 | No `tools/` directory |
| `backend/ai_worker/` | README §1, §3.2, §4 | Directory does not exist |
| "add screenshot capture on face_mismatch" | README §12 "Extending" | Already done — `proctoring.js` attaches a frame to every violation |
| Structure section | `frontend/README.md` | Predates `lockdown.js`, `eventLogger.js`, `RequestAccess.jsx`, `CredentialsHandoff`, `EmptyState`, `RosterManager`, `Tagline`, `LogoMark` |

Undocumented-but-shipped features: the access-request/approval flow, multi-select questions, exam scheduling windows with candidate-status bucketing, draft-exam deletion, examiner attempt reset, and the screen-share requirement.

### 4.14 🟢 Low — code quality odds and ends

- **Dead code:** `exam_repository.list_published_exams` (superseded by `organization_service.list_accessible_published_exams`, zero callers) and `schemas/attempt.py::AttemptSummaryOut` (never referenced — the endpoints it was written for return raw `list[dict]`).
- **No pagination anywhere.** No `limit`/`offset` in any repository. Admin list endpoints (`/admin/examiners`, `/admin/candidates`, `/admin/violations`) return every row. Fine at current scale, a wall at a few thousand.
- **No frontend tooling.** `package.json` has `dev`, `build`, `preview` and nothing else — no lint, no test runner, no formatter. A sharp asymmetry against 314 backend tests.
- **`Exam.jsx` is 2,236 lines** and owns the precheck flow, timer, navigator, three question renderers, submit modal, and two overlays. Well-organised for its size, but it's the one file that should be split before it grows further.
- **Form validation duplicated three times** across `Login.jsx`, `Register.jsx`, and `RequestAccess.jsx` — same `computeErrors` / `update` / `handleBlur` / `touched` pattern, three independent implementations.
- **`Icon.jsx` returns `null` for unknown names** — a typo renders nothing and warns nowhere.
- **`python-jose 3.3.0` carries CVE-2024-33664.** This is knowingly pinned with an excellent explanatory comment in `requirements.txt` (the JWE decompression bomb is reachable from `jwt.decode` on an attacker-supplied bearer token; the algorithm-confusion CVE is not, on an HS256-only config). Migrating `core/security.py` to PyJWT is a small, well-contained change — it's the only remaining known-vulnerable dependency.
- **Password policy is length-only** (8–128 chars, no complexity or breach check). Note also that bcrypt truncates at 72 bytes, so the 128-char ceiling is partly cosmetic.
- **JWT has no refresh token, no `jti`, and no revocation list.** Logout is client-side only; a stolen token stays valid for its full 120 minutes. Account deactivation *does* invalidate immediately (`get_current_user` re-checks `is_active`), which covers the important case.
- **Two directories named `models`:** `backend/app/models/` (ORM) and `backend/models/` (downloaded AI weights, gitignored).
- **Legacy branding:** `aep_` localStorage prefix, `@examproctor.com` seed email, `"AI Exam Proctor API"` as the FastAPI title. Deliberate compatibility choices, now that the legacy frontend is retired they're safe to clean up.

---

## 5. Verification performed

**Test suite executed** (Python 3.10, SQLite in-memory, `mediapipe` stubbed since it isn't needed for any assertion):

```
314 tests collected
310 passed · 1 skipped · 3 failed
```

All 3 failures are the hardcoded-`/tmp` path issue in §4.12, not logic defects. Coverage is meaningful where it matters most — `test_exam_access_control.py` alone is 714 lines covering the tenancy boundary, and there are dedicated suites for face-photo access, ID-card-photo access, rate limiting, DB transactions, production hardening, and code-runner hardening.

**Enum/migration gap** proved by parsing all 15 migrations and diffing the reachable enum values against `app/models/enums.py` (output quoted in §4.1).

**`.env` parsed** to confirm the missing `ENVIRONMENT` key and a real (non-placeholder, 86-char) `SECRET_KEY`.

**Grep sweeps** confirming: zero SQL string interpolation, zero `dangerouslySetInnerHTML`/`innerHTML`/`eval`, zero `integrity=` attributes, `rate_limit` applied to exactly two routes, no certificate endpoint, no email capability, no `.instructions` reference in `Exam.jsx`, no `.git`.

---

## 6. Recommended order of work

**Before any real traffic hits Postgres**

1. Add the migration for `questiontype.multi_select` and `eventtype.screen_share_stopped` (§4.1). Multi-select questions and screen-share strikes are both broken against Postgres today.
2. `git init` + initial commit (§4.2).
3. Set `ENVIRONMENT=production` in the deployment `.env`, and consider flipping the code default (§4.3).
4. Change the seeded admin password, or add a production guard to `seed.py` (§4.10).

**Before students use it at scale**

5. Rate-limit the four AI inference endpoints (§4.4).
6. Cap total batch-request bytes and validate violation screenshots through `decode_image` (§4.5).
7. Add SRI hashes / self-host the CDN assets loaded into the exam page (§4.7).
8. Decide a biometric retention policy and build the delete path (§4.6).

**Correctness and polish**

9. Render `exam.instructions` on the student precheck screen (§4.11).
10. Fix the three `/tmp` tests with `tmp_path` (§4.12).
11. Reconcile the docs: certificate endpoint, "you'll receive an email" copy in three places, the `docker-compose.yml` / `tools/` / `ai_worker/` setup instructions, the "17" statistic, and the frontend README structure section (§4.8, §4.9, §4.13).
12. Migrate `core/security.py` from python-jose to PyJWT (§4.14).

**Whenever convenient**

13. Delete the two dead symbols; add pagination to admin lists; add ESLint + Vitest to the frontend; split `Exam.jsx`; extract the shared form-validation hook.
