# Merit.Ai — React Frontend

This is the React (Vite) frontend for the AI Exam Proctor project, rebranded as **Merit.Ai** — "Conduct. Monitor. Evaluate."
It is the **complete, sole frontend** for the project — the legacy static HTML/CSS/JS frontend it
replaced has been removed. This app covers everything end to end: the marketing/home page, a fully working **login and
registration flow**, fully working **student, admin, and examiner dashboards** (including the examiner's
drag-and-drop question builder and bulk import), **Profile** (face registration + ID card OCR), and the
full **proctored exam-taking interface** — timer, question navigator, MCQ + CodeMirror coding questions
with sandboxed "Run Sample", autosave with retry/backoff, mark-for-review, submit, and live AI proctoring
(face/object/pose/gaze checks, tab-switch and fullscreen-exit detection, mic noise detection) — plus a
standalone **Results** page with report/certificate PDF downloads. All wired to the real backend.

## Getting started

```bash
cd frontend
npm install
npm run dev      # http://localhost:5173
```

`npm run dev` proxies any request to `/api/*` to `http://localhost:8000` (the FastAPI backend), so `/login`
and `/register` work out of the box as long as the backend is running — no CORS setup needed in dev.

**Try it:** register a student account at `/register`, or log in as a student at `/login`. You'll land on
`/dashboard`, which shows the real student dashboard — stat cards, ongoing/upcoming exams, a completed-
exams table with working PDF report/certificate downloads, and derived announcements, all backed by live
API calls. "Start/Resume Exam" goes to the real proctored exam interface at `/exam/:examId`, and submitting
lands on `/results/:attemptId`.

Log in as the seeded admin (`admin@examproctor.com` / `Admin@12345`) and you'll land on the real admin
dashboard — collapsible sidebar, platform stat cards, live Chart.js charts (exams by status, violations by
type/severity), students/examiners tables, and a working "create examiner" form.

Log in as an examiner and you'll land on the real examiner dashboard — create an exam, add sections, add
MCQ or coding questions (with test cases), drag-and-drop reorder questions, bulk-import MCQs by pasting
pipe-delimited lines, publish the exam, and monitor attempts/active sessions/violations/analytics per exam.

Students can also visit `/profile` to register their face and verify an ID card via webcam — both post real
images to the backend.

```bash
npm run build     # production build -> dist/
npm run preview   # serve the production build locally
```

## Stack

- **Vite 5** + **React 18** — no experimental/bleeding-edge tooling, chosen deliberately for stability.
- **React Router 6** for client-side routing.
- **Tailwind CSS 3** (compiled via PostCSS, not the CDN build) with the same design tokens (`--primary`,
  `--card-bg`, `--border`, etc.) as the existing static frontend's `css/style.css`, so dark mode and the
  overall visual language stay consistent while the migration is in progress. Dark mode is toggled by
  setting `data-theme="dark"` on `<html>`, same as before, persisted to `localStorage` under `aep_theme`.
- **Chart.js** (loaded via CDN `<script>` in `index.html`, used as `window.Chart` — not an npm dependency)
  for the admin dashboard's charts. Same approach the legacy frontend used.
- **CodeMirror 5** (also loaded via CDN `<script>`/`<link>` in `index.html`, used as `window.CodeMirror`)
  for coding-question answering in the exam interface.

## Structure

```
src/
  components/
    Navbar, Footer, Logo, ThemeToggle   shared marketing-page chrome
    Icon, FormField, AuthLayout          shared auth-page building blocks
    DashboardHeader                      shared authed top bar (logo, theme, name/role, logout)
    CaptureCard                          reusable webcam-capture-then-submit card (used twice in Profile.jsx)
    CodeEditor                           thin React wrapper around window.CodeMirror, used by Exam.jsx
    AdminSidebar (inside AdminDashboard.jsx)   collapsible nav, same behavior as the legacy sidebar
  lib/
    theme.js       dark mode helpers (localStorage "aep_theme")
    ui.js          shared button/badge className recipes (design-system-in-a-file)
    api.js         fetch wrapper for /api/v1 — auto-attaches the bearer token, clears session on 401,
                   supports get/post/put/del + downloadFile (blob) for PDF report/certificate downloads
    auth.js        session helpers (localStorage "aep_token"/"aep_role"/"aep_name"/"aep_user_id",
                   same keys as the legacy frontend/js/auth.js)
    proctoring.js  AI proctoring controller (createProctoring() factory) — camera/mic capture, periodic
                   face/object/pose/gaze checks against the backend, external-monitor detection,
                   fullscreen/tab-switch watching, copy-paste blocking, violation logging + toasts
  pages/
    Home.jsx              the landing page
    Login.jsx              /login — real API call, redirects to /dashboard on success
    Register.jsx           /register — student self-registration, same as above
    Dashboard.jsx           /dashboard — dispatches by role: StudentDashboard, AdminDashboard,
                            or ExaminerDashboard
    StudentDashboard.jsx     the real student dashboard (see above)
    AdminDashboard.jsx       the real admin dashboard (see above)
    ExaminerDashboard.jsx    the real examiner dashboard: exam list/create, section+question builder
                            (drag-and-drop reorder, MCQ/coding forms, bulk import), and per-exam
                            attempts/active/violations/analytics panels
    Profile.jsx              /profile — face registration + ID card OCR (two CaptureCard instances)
    Exam.jsx                 /exam/:examId — the real proctored exam interface: pre-check overlay,
                            timer, question navigator, MCQ/coding answering, autosave, mark-for-review,
                            submit, and live AI proctoring
    Results.jsx              /results/:attemptId — score summary + report/certificate PDF downloads
    Placeholder.jsx          generic "coming soon" stub, used for the 404 route
  App.jsx       route table
  main.jsx      entry point
```

## What's next

- Migration to React is functionally complete. Remaining work is the final verification pass and
  retiring `frontend/` (see the root `README.md`, §1.1).
