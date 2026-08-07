# Merit.Ai — React Frontend

The React (Vite) frontend for Merit.Ai — "Conduct. Monitor. Evaluate." This is
the **complete, sole frontend** for the project: the marketing/home pages, a
full login/registration flow (with OTP signup and OTP password reset), and
three role-based dashboards (student, admin, examiner) covering exam
creation, the drag-and-drop question builder and bulk MCQ import, per-student
violation review with proof screenshots and reviewer decisions, live exam CSV
export, and the full proctored exam-taking interface — timer, question
navigator, MCQ + CodeMirror coding questions with sandboxed "Run Sample",
autosave with retry/backoff, mark-for-review, submit, and live AI proctoring
(face/object/pose/gaze checks, tab-switch and fullscreen-exit detection, mic
noise detection) — plus a Results page with report PDF downloads. All wired
to the real backend; see the root `README.md` for the full feature and API
reference.

## Getting started

```bash
cd frontend
npm install
npm run dev      # http://localhost:5173
```

`npm run dev` proxies any request to `/api/*` to `http://localhost:8000` (the
FastAPI backend), so login/register work out of the box as long as the
backend is running — no CORS setup needed in dev.

```bash
npm run build     # production build -> dist/
npm run preview   # serve the production build locally
npm run lint       # eslint
```

## Stack

- **Vite 5** + **React 18** — no experimental/bleeding-edge tooling, chosen deliberately for stability.
- **React Router 6** for client-side routing.
- **Tailwind CSS 3** (compiled via PostCSS, not the CDN build) with a consistent design-token set
  (`--primary`, `--card-bg`, `--border`, etc.). Dark mode is toggled by setting `data-theme="dark"`
  on `<html>`, persisted to `localStorage` under `aep_theme`.
- **Chart.js** (loaded via CDN `<script>` in `index.html`, used as `window.Chart` — not an npm dependency)
  for the admin/examiner analytics charts.
- **CodeMirror 5** (also loaded via CDN `<script>`/`<link>` in `index.html`, used as `window.CodeMirror`)
  for coding-question answering in the exam interface.
- **MediaPipe Tasks Vision** (`src/lib/faceMesh.js`) — face/pose/gaze proctoring signals run
  client-side at ~12fps; see the root `PROCTORING_TUNING.md` for the full rationale.

## Structure

```
src/
  components/      Navbar, Footer, Logo, ThemeToggle, DashboardHeader, CaptureCard,
                    CodeEditor, Breadcrumbs, Pagination, EmptyState, IdentityPhotoModal, ...
  lib/
    theme.js          dark mode helpers (localStorage "aep_theme")
    ui.js             shared button/badge className recipes (design-system-in-a-file)
    api.js            fetch wrapper for /api/v1 — auto-attaches the bearer token, clears
                       session on 401, supports get/post/put/del + downloadFile (blob)
    auth.js           session helpers (localStorage "aep_token"/"aep_role"/"aep_name"/"aep_user_id")
    proctoring.js     AI proctoring controller (createProctoring() factory)
    faceMesh.js       in-browser MediaPipe face/pose/gaze pipeline
    lockdown.js       fullscreen/tab-switch/copy-paste strike tracking
  pages/
    Home, Features, Pricing, About, FAQ, Contact, Privacy, Terms   marketing pages
    Login, Register, Activate, ForgotPassword, RequestAccess       auth flows
    Dashboard                    dispatches by role to Student/Admin/ExaminerDashboard
    StudentDashboard, Profile, Exam, Results                       student-facing app
    ExaminerDashboard                                              examiner home
    examiner/
      ExamsListPage, ExamBuilderPage, QuestionBuilder, panels.jsx    exam authoring +
      AttemptReport.jsx                                              per-student violation review
    AdminDashboard, AdminExaminers(Detail), AdminExams(Detail),
    AdminCandidates(Detail), AdminAttemptReport, AdminLiveSessions,
    AdminReviewQueue, AdminOrganizations                           admin console
    SystemCheck                  pre-exam webcam/mic/browser capability check
    Placeholder                  generic 404 stub
  App.jsx       route table
  main.jsx      entry point
```
