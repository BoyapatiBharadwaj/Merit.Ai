# Merit.Ai

**Conduct. Monitor. Evaluate.**

An online examination platform with real-time AI proctoring, sandboxed coding
assessments, and role-based dashboards for administrators, examiners, and candidates.

<p>
  <a href="https://github.com/BoyapatiBharadwaj/Merit.Ai/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/BoyapatiBharadwaj/Merit.Ai/actions/workflows/ci.yml/badge.svg"></a>
  <img alt="Python" src="https://img.shields.io/badge/Python-3.11-3776AB?logo=python&logoColor=white">
  <img alt="FastAPI" src="https://img.shields.io/badge/FastAPI-0.111-009688?logo=fastapi&logoColor=white">
  <img alt="React" src="https://img.shields.io/badge/React-18.3-61DAFB?logo=react&logoColor=black">
  <img alt="PostgreSQL" src="https://img.shields.io/badge/PostgreSQL-16-4169E1?logo=postgresql&logoColor=white">
  <img alt="Redis" src="https://img.shields.io/badge/Redis-7-DC382D?logo=redis&logoColor=white">
  <img alt="Docker" src="https://img.shields.io/badge/Docker%20Compose-8%20services-2496ED?logo=docker&logoColor=white">
  <img alt="License" src="https://img.shields.io/badge/License-MIT-green">
</p>

---

## Contents

- [Overview](#overview)
- [Features](#features)
- [Architecture](#architecture)
- [Quick start](#quick-start)
- [Local development](#local-development)
- [Configuration](#configuration)
- [Testing](#testing)
- [Project layout](#project-layout)
- [Documentation](#documentation)
- [License](#license)

---

## Overview

Merit.Ai runs the full lifecycle of a proctored online assessment: an examiner
builds an exam, enrols a cohort, and publishes it; a candidate verifies their
identity, sits the exam under continuous AI monitoring, and receives a scored
report; a reviewer adjudicates any flagged behaviour against captured evidence.

Three roles, each with its own dashboard:

| Role | Can do |
|---|---|
| **Administrator** | Platform-wide analytics, organizations, examiner provisioning, candidate records, violation review queue, live sessions |
| **Examiner** | Exam and question builder, cohort rosters, per-attempt violation review, results and CSV export, per-exam analytics |
| **Candidate** | Identity registration (face + ID card), proctored exam sitting, results and PDF report |

**Scale of the codebase:** 122 REST endpoints across 9 routers, 21 database
tables under 31 Alembic migrations, and 628 tests across 41 test modules.

---

## Features

### Proctoring

Signals run where they belong — high-frequency tracking in the browser, identity
and object inference on the server:

| Signal | Where | How |
|---|---|---|
| Face presence and count | Browser, ~12 fps | MediaPipe Tasks Vision |
| Head pose and gaze | Browser, ~12 fps | Face Mesh landmarks, calibrated against the candidate's own neutral posture |
| Identity matching | Server, every 12 s | ArcFace 512-d embeddings, cosine distance |
| Liveness / anti-spoofing | Server | Trained ONNX classifier when installed, otherwise a texture/colour/glare/FFT-moiré detector |
| Phone, book, extra person | Server | YOLO11s via ONNX Runtime, per-class confidence floors |
| ID card verification | Server | EasyOCR text recognition, token-overlap match against the registered name |
| Tab switch, fullscreen exit, copy/paste | Browser | DOM event listeners |
| Background noise | Browser | Web Audio speech-band energy ratio with an adaptive ambient floor |
| External monitor | Browser | Window Management API |

Every signal must persist across several consecutive checks before it logs a
violation, so a single glance or blink never counts. Each signal degrades
independently: a missing model disables one check rather than breaking the exam.

### Assessment

- MCQ (single and multi-select) and coding questions in one exam
- Coding questions run in ephemeral `--network none` Docker containers with
  memory, CPU, and process caps; sample tests are student-visible, hidden tests
  are grading-only, and marks are awarded proportionally
- Autosave on every answer, with client-side retry queue and exponential backoff
- Idempotent attempt start, so a reload or crash resumes the same attempt with a
  server-computed remaining time that cannot be rewound
- Server-side scoring, per-exam results-visibility rules, and PDF result reports

### Platform

- JWT authentication with role-based access control and attempt-scoped tokens
- Organization-scoped exam access — a candidate nobody has enrolled sees nothing
- OTP email verification for signup and password reset
- Examiner provisioning by single-use activation link; passwords are never emailed
- Redis-backed rate limiting, distributed locks, and an RQ job queue — all
  failing closed with a `503` rather than silently letting requests through
- Transactional email written to a Postgres outbox before delivery is attempted

---

## Architecture

```
                        ┌──────────┐
        browser ───────▶│  proxy   │  nginx — the only published port
                        └────┬─────┘
                   ┌─────────┴─────────┐
                   ▼                   ▼
             ┌──────────┐        ┌──────────┐
             │ frontend │        │ core-api │  FastAPI
             └──────────┘        └────┬─────┘
                                      │
              ┌───────────┬───────────┼───────────┐
              ▼           ▼           ▼           ▼
        ┌──────────┐┌──────────┐┌──────────┐┌──────────┐
        │ postgres ││  redis   ││ai-worker ││scheduler │
        └──────────┘└──────────┘└──────────┘│ + worker │
                                            └──────────┘
```

Eight containers on five isolated Docker networks. The frontend has no network
path to Postgres, Redis, or the AI worker; the AI worker reaches neither the
database nor Redis; only the proxy is published to the host.

`core-api` owns every business transaction and the single Postgres database.
Face and object inference are delegated to `ai-worker`, with a transparent
in-process fallback if it is unreachable — a bad moment during an exam is a
slower one, not an unmonitored one.

Full service, network, and data-model detail: **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)**.

---

## Quick start

**Requirements:** Docker Engine 24+ with Compose v2.

```bash
git clone https://github.com/BoyapatiBharadwaj/Merit.Ai.git
cd Merit.Ai
cp .env.example .env
```

Set three values in `.env` before starting (Compose refuses to boot without them):

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(64))"   # SECRET_KEY
python3 -c "import secrets; print(secrets.token_urlsafe(32))"   # POSTGRES_PASSWORD
python3 -c "import secrets; print(secrets.token_urlsafe(32))"   # REDIS_PASSWORD
```

```bash
docker compose up --build
```

Open **http://localhost**. The API's Swagger UI is at `/docs` on the `core-api`
container.

On first boot `core-api` runs migrations and seeds the roles plus the first
administrator (idempotent, safe on every restart), and `ai-worker` pre-downloads
its model weights into a named volume.

```bash
docker compose logs -f core-api ai-worker
docker compose down        # stop
docker compose down -v     # stop and delete database and model volumes
```

Windows users can run `.\deploy.ps1` instead, which validates `.env`, rebuilds,
waits for health checks, and reports what is actually running.

Deployment specifics — TLS, scaling, backups, coding-question sandbox setup:
**[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)**.

---

## Local development

Running the pieces directly gives hot reload on both sides.

**Backend**

```bash
docker run -d --name merit-ai-postgres -p 5432:5432 \
  -e POSTGRES_USER=merit_ai -e POSTGRES_PASSWORD=change_this_password \
  -e POSTGRES_DB=merit_ai postgres:16-alpine
docker run -d --name merit-ai-redis -p 6379:6379 redis:7-alpine

cd backend
python -m venv venv && source venv/bin/activate    # Windows: venv\Scripts\activate
pip install -r requirements-dev.txt
cp .env.example .env

alembic upgrade head           # schema
python -m app.utils.seed       # roles + first admin
python -m app.utils.fetch_models   # AI weights (see below)
uvicorn app.main:app --reload --port 8000
```

**Frontend**

```bash
cd frontend
npm install
npm run dev          # http://localhost:5173, proxies /api/* to :8000
```

**Model weights.** `python -m app.utils.fetch_models` downloads everything up
front so the first exam of the day does not pay for it. It is idempotent, takes
`--only NAME`, and reports each failure instead of dying on the first one.

| Name | What | Size | Needed for |
|---|---|---|---|
| `face` | ArcFace identity matching (InsightFace `buffalo_l`) | ~280 MB | Face matching |
| `yolo` | YOLO11s object detection plus an ONNX export | ~19 MB | Phone / book / person detection |
| `ocr` | EasyOCR recognition weights | ~100 MB | ID card verification |
| `antispoof` | Trained liveness classifier | ~2 MB | Optional — a built-in detector runs without it |

Set `FACE_MODEL_NAME=buffalo_s` for a ~16 MB pack if download size matters more
than accuracy.

---

## Configuration

Every setting is an environment variable, documented inline in `.env.example`
(Docker stack) and `backend/.env.example` (running the backend directly). The
values that matter most:

| Variable | Default | Notes |
|---|---|---|
| `ENVIRONMENT` | `development` | `production` refuses to boot on a placeholder or short `SECRET_KEY`, or empty `CORS_ORIGINS` |
| `SECRET_KEY` | placeholder | Signs every JWT. Required in production |
| `REGISTRATION_MODE` | `open` | `invite` restricts signup to addresses already on a roster |
| `FACE_MATCH_TOLERANCE` | `0.38` | Cosine distance; lower is stricter |
| `LOCKDOWN_STRIKE_LIMIT` | `3` | Violations before an attempt is auto-submitted |
| `WEB_CONCURRENCY` | `4` | uvicorn workers; total DB connections are `(DB_POOL_SIZE + DB_MAX_OVERFLOW) × this` |
| `BIOMETRIC_RETENTION_DAYS` | `0` | `0` never auto-deletes; set a value to purge after the last attempt |
| `CODE_EXECUTION_ENABLED` | `true` | Needs the host Docker socket; see `DOCKER_GID` |

Full reference, including the email and OTP settings:
**[docs/CONFIGURATION.md](docs/CONFIGURATION.md)**.

---

## Testing

```bash
cd backend
pip install -r requirements-dev.txt
pytest
```

628 tests run against in-memory SQLite and `fakeredis` — no Postgres, no Redis,
no GPU, no network, no model downloads. A second suite exercises migrations and
row-lock concurrency against a real Postgres; CI runs both on every push.

```bash
cd frontend
npm run lint
npm test
```

More on suites, fixtures, and CI: **[docs/TESTING.md](docs/TESTING.md)**.

---

## Project layout

```
Merit.Ai/
├── backend/
│   ├── app/
│   │   ├── api/v1/        FastAPI routers — thin controllers
│   │   ├── services/      business logic
│   │   ├── repositories/  database access, no business logic
│   │   ├── models/        SQLAlchemy ORM
│   │   ├── schemas/       Pydantic request/response models
│   │   ├── ai/            face, object, pose, OCR, anti-spoofing
│   │   ├── core/          config, security, Redis, locks, queues, rate limiting
│   │   ├── scheduler/     periodic jobs (single replica)
│   │   ├── worker/        RQ worker
│   │   └── main.py
│   ├── ai_worker/         standalone inference microservice
│   ├── alembic/           migrations
│   └── tests/
├── frontend/
│   └── src/
│       ├── components/    shared UI
│       ├── pages/         routes, including examiner/ and admin views
│       └── lib/           api client, auth, proctoring, autosave, theme
├── deploy/nginx/          reverse proxy image and config
├── docs/
└── docker-compose.yml
```

---

## Documentation

| Document | Covers |
|---|---|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Services, networks, data model, request flow |
| [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) | Production deployment, TLS, scaling, backups |
| [docs/CONFIGURATION.md](docs/CONFIGURATION.md) | Every environment variable |
| [docs/FEATURES.md](docs/FEATURES.md) | Organizations, exams, coding questions, results, review |
| [docs/PROCTORING.md](docs/PROCTORING.md) | Signal-by-signal tuning guide |
| [docs/TESTING.md](docs/TESTING.md) | Test suites, fixtures, CI |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Development workflow and conventions |
| [SECURITY.md](SECURITY.md) | Reporting a vulnerability, security posture |
| [frontend/README.md](frontend/README.md) | Frontend structure |

---

## Roadmap

- Pagination on the remaining admin and examiner list endpoints
- Refresh tokens (the current JWT is a single long-lived access token)
- Verifiable completion certificates with a public verification endpoint
- WebSocket live monitoring for examiners
- A warm container pool for coding-question grading

---

## License

MIT — see [LICENSE](LICENSE).
