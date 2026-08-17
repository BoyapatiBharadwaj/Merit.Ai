# Merit.Ai — Frontend

The React (Vite) single-page application: marketing pages, the full
authentication flow, three role-based dashboards, and the proctored
exam-taking interface.

See the [root README](../README.md) for the platform overview, and
[docs/PROCTORING.md](../docs/PROCTORING.md) for proctoring tuning.

## Getting started

```bash
npm install
npm run dev       # http://localhost:5173
```

`npm run dev` proxies `/api/*` to `http://localhost:8000`, so sign-in works out
of the box against a running backend with no CORS setup.

| Script | What it does |
|---|---|
| `npm run dev` | Dev server with hot reload |
| `npm run build` | Production build to `dist/` |
| `npm run preview` | Serve the production build locally |
| `npm run lint` | ESLint over `src/` |
| `npm test` | `node --test` over `src/**/*.test.mjs` |
| `npm run check` | Finds identifiers used but never imported |
| `npm run vendor:mediapipe` | Copies the MediaPipe WASM bundle out of `node_modules` (wired into `dev` and `build`) |
| `npm run fetch:model` | Downloads the face-landmarker weights for self-hosting |

## Stack

- **Vite 5** and **React 18** — stable tooling, no experimental features
- **React Router 6** for client-side routing
- **Tailwind CSS 3** compiled through PostCSS, on a design-token set
  (`--primary`, `--card-bg`, `--border`, …). Dark mode sets `data-theme="dark"`
  on `<html>` and persists to `localStorage`
- **Chart.js** and **CodeMirror 5**, loaded from CDN `<script>` tags in
  `index.html` and used as globals rather than npm dependencies
- **MediaPipe Tasks Vision** for the in-browser face, pose, and gaze signals

## Structure

```
src/
  components/     Navbar, Footer, Logo, ThemeToggle, DashboardHeader, CaptureCard,
                  CodeEditor, Breadcrumbs, Pagination, EmptyState, ErrorBoundary,
                  IdentityPhotoModal, RosterManager, CredentialsHandoff, ...
  lib/
    api.js        fetch wrapper for /api/v1 — attaches the bearer token, clears the
                  session on 401, exposes get/post/put/del and downloadFile
    auth.js       session helpers backed by localStorage
    theme.js      dark-mode helpers
    ui.js         shared className recipes for buttons, fields and badges
    proctoring.js the proctoring controller — createProctoring()
    faceMesh.js   in-browser MediaPipe face, pose and gaze pipeline
    lockdown.js   fullscreen, tab-switch and copy/paste strike tracking
    autosave.js   the retry queue behind answer autosave
    eventLogger.js violation batching
    seo.js        per-route document metadata
  pages/
    Home, Features, Pricing, About, FAQ, Contact, Privacy, Terms
    Login, Register, Activate, ForgotPassword, RequestAccess
    Dashboard                          dispatches by role
    StudentDashboard, Profile, Exam, Results, SystemCheck
    ExaminerDashboard
    examiner/  ExamsListPage, ExamBuilderPage, QuestionBuilder, panels, AttemptReport
    AdminDashboard, AdminExaminers, AdminExaminerDetail, AdminExams, AdminExamDetail,
    AdminCandidates, AdminCandidateDetail, AdminAttemptReport, AdminLiveSessions,
    AdminReviewQueue, AdminOrganizations
    Placeholder                        404 stub
  App.jsx         route table
  main.jsx        entry point
```

## Conventions

- Function components with hooks. `ErrorBoundary` is the one class component,
  since `componentDidCatch` has no hooks equivalent.
- Never call `fetch` from a component — go through `src/lib/api.js`.
- Routes are code-split; `RouteFallback` covers the loading state and
  `ErrorBoundary` catches a chunk that fails to download.
