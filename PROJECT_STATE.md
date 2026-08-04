# Merit.Ai — Current State

**Last verified:** 4 August 2026
**Method:** every number below was measured, not remembered. Test counts come from
actual runs, the migration head from the filesystem, the bundle sizes from a real
`vite build`, and the "not built yet" list from grepping for the code that would
implement it.

---

## 1. At a glance

| | |
|---|---|
| **Backend** | 87 Python files · 12,150 lines · **102 endpoints** · 20 tables |
| **Frontend** | 65 files · 15,689 lines · React 18 + Vite 5 + Tailwind 3 |
| **Tests** | 35 files · 7,541 lines · **414 pass, 2 skipped, 0 fail** |
| **Migrations** | 21, linear, head **0021** |
| **Config** | 54 settings in `config.py`, 40 keys in `.env` |
| **Frontend build** | ✅ clean — entry 223 KB (70 KB gzip), route-split |
| **Git** | `d738287` initial commit + 89 files changed since |

**Test breakdown:** 408 SQLite (fast suite) + 6 PostgreSQL (real Alembic chain,
`-m postgres`).

---

## 2. Architecture

```
Browser ──┐  MediaPipe Tasks Vision @12fps (local: face count, pose, gaze)
          │  Web Audio (noise) · lockdown.js (fullscreen/blur/screen-share)
          ▼
    nginx proxy :80 ─┬─ /api/*  → core-api :8000 ─┬─ postgres      (data-net)
                     │                             ├─ ai-worker :8010 (ai-net)
                     └─ /*      → frontend :8080   └─ docker.sock → code sandbox
```

Layering holds throughout: **API → Service → Repository → Model**. 17 services,
no API→repository shortcuts. The proxy is the only published port.

---

## 3. What works

### Accounts & auth
- JWT (HS256), three roles, organization-scoped access
- **Student signup with emailed OTP** — code verified *before* the account is
  created, so no unverified rows exist. Falls back to direct signup if the
  server has no SMTP (503 → graceful degrade)
- **Password policy:** students are administrator-managed; examiners and admins
  change their own. A locked-out student self-recovers by emailed code
- **Forgot password** — full OTP flow, single-use, 10-min TTL, 5-attempt budget,
  60s resend cooldown, constant-time comparison, HMAC-SHA256 digests only
- Per-IP rate limiting on auth; **per-user** limits on the AI endpoints
- Admin: create examiner, disable/enable, **delete**, reset password with
  **one-time reveal** (never stored recoverably)

### Identity & biometrics
- Face registration (ArcFace/InsightFace) + ID-card OCR (EasyOCR)
- Signup is name/email/password only — identity capture lives on Profile,
  gated before the first proctored exam, re-verified in every pre-exam check
- **Students can see their own** verified face and ID card
- Consent version stamped per capture; erasure endpoints (self + admin);
  opt-in retention sweep (default: never auto-delete)
- Erasure blanks the payload but **keeps the row** as an audit record

### Exams
- Draft → published → closed. Sections, questions, drag reorder
- MCQ, **multi-select**, and sandboxed coding questions
- **Bulk import** with multiple correct answers (`2,4` → multi-select),
  per-line error reporting
- **Delete section** (draft-only, owner-only, refuses the last one)
- Scheduling, randomisation, pass marks, per-attempt option order

### Exam runner
- Timer, navigator, mark-for-review, autosave with retry/backoff
- **Autosave is race-safe** — client-owned `answer_version` + idempotency key;
  a delayed request can no longer overwrite a newer answer
- Server-authoritative lockdown: strikes derived from persisted events, so a
  refresh or `localStorage` wipe cannot reset them
- Redesigned submit dialog showing answered / unanswered / flagged

### Proctoring
- Browser: face count, head pose, gaze @12fps (no network)
- Server: face identity, object detection (YOLO11s), ID OCR, anti-spoof
- **Frames capped at 640px** before upload — ~4× smaller at 720p, ~9× at 1080p,
  with zero accuracy cost (both models resize to 640 internally)
- Object detection polls every **5s** (was 10s)
- **Kill switches** for face matching / object detection / pose — a disabled
  signal reports "not collected", never a pass or a failure

### Email
- Gmail SMTP. Never raises, never blocks a request, off by default
- OTP codes, access-request notice, credential handoff, exam published,
  **exam address added**, 5-minute reminder
- Branded template: inline **CID logo**, `Merit` black + `.Ai` blue, tagline,
  inbox preheaders

### Admin
- Dashboards, drill-downs, live sessions, violation review
- **Activity trail** — 17 event types, actor separate from subject, survives
  account deletion, never contains a secret

---

## 4. Recently fixed (worth not re-breaking)

| Bug | Why it mattered |
|---|---|
| Face registration failed for everyone | `_local_face_encoding` refused any frame with >1 detected face. Classmates in the background = permanent failure. Now enrols the largest face |
| `multi_select` / `screen_share_stopped` missing from PG enums | Would crash on first insert against real PostgreSQL. Migration 0017 |
| Migration 0016 `DuplicateObject` | Explicit `CREATE TYPE` + `create_table` both emitting it. Found by the PostgreSQL test |
| Migration 0020 killed the container | `create_check_constraint` scans existing rows; one legacy exam aborted boot. Now `NOT VALID` |
| `npm ci` failing silently | `package.json` and `package-lock.json` out of sync → frontend image never rebuilt → stale page served |
| Autosave last-write-wins | Delayed request silently reverted a newer answer |
| Admin dashboard N+1 | 247 queries → **8** for 60 candidates |
| `models/` in `.gitignore` | Excluded `backend/app/models/` — the entire ORM layer — from the repo |
| CDN scripts, no SRI, no CSP | Exam page holds camera/mic/screen + JWT. All self-hosted now |
| `/docs` + port 8000 public | Whole API enumerable outside the proxy |

---

## 5. Not built yet

**Explicitly deferred, API exists:**
- Admin UI panels for the **activity feed** and **identity photos** on
  `AdminCandidateDetail.jsx` / `AdminExaminerDetail.jsx`. Endpoints are live and
  tested (`GET /admin/users/{id}/activity`, `GET /admin/activity`) — only the
  React panels are missing

**Known gaps:**
- **Prometheus / Grafana** — no metrics endpoint. Should land before load testing
- **`python-jose` 3.3.0** (CVE-2024-33664) — triaged in `requirements.txt`,
  migration to PyJWT still pending. ~30 lines in `security.py`
- **Compose secrets** — secrets still in `.env` on disk
- **Refresh tokens / session revocation** — JWTs are stateless, so a password
  reset does not invalidate existing tokens
- **Redis + task queue** — OCR, reports and bulk email run inline
- **`RUN_MIGRATIONS_ON_START=true`** — still the default, so rollback doesn't
  fully work. Flip it once deploys take a backup first
- **No frontend tests** — no Vitest, no Playwright. The exam runner has no
  browser-level regression cover
- **Attempt reset deletes the original attempt** — only a summary survives in
  `attempt_resets`
- **`Exam.jsx` (~2,300 lines) and `ExaminerDashboard.jsx` (~2,000)** are still
  monolithic
- **Text JSON columns** (`option_order_json`, `test_cases_json`, …) — not JSONB

---

## 6. Operational notes

- **Deploy:** `.\deploy.ps1` — checks folder, `.env`, builds, waits for health,
  verifies email initialised and the frontend isn't stale
- **`.env` has 40 keys.** New ones must be copied from `.env.example` manually;
  `.env` is gitignored and never overwritten
- **Rebuild rules:** `.env` or compose only → `up -d`. Any source, `entrypoint.sh`
  or Dockerfile → `up -d --build`
- **A failed image build still reports success** if another service starts —
  `deploy.ps1` catches this by fetching the served page
- **Gmail App Password** must be 16 chars, no spaces, 2FA enabled.
  The one in the current `.env` appeared in a chat transcript — **rotate it**
- **PostgreSQL migration tests** need `pip install pgserver` (or
  `TEST_POSTGRES_URL`); they skip cleanly otherwise
- Migration 0020's CHECK constraints are `NOT VALID` — historical bad rows still
  exist. Find them, clean them, then `VALIDATE CONSTRAINT`

---

## 7. Immediate priorities

1. **Rotate the Gmail App Password**
2. Wire the admin activity + photo panels (API ready)
3. `python-jose` → PyJWT
4. Prometheus + Grafana, then load test
5. Compose secrets, then `RUN_MIGRATIONS_ON_START=false` with a backup gate
