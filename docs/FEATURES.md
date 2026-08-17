# Features

## Organizations and exam access

Exams are scoped to an **organization**. An examiner belongs to one, derived from
the organization name on their account; every exam they create inherits it; and
only candidates enrolled in that organization can see or start it.

The check lives in `organization_service.can_student_access_exam`, shared by the
exam list, the exam detail route, and `start_attempt` — filtering the list alone
would be cosmetic.

**How a candidate gets access**

1. An examiner adds their email to the organization roster (Examiner Dashboard →
   *Student roster*). Pasting a spreadsheet column works: commas, semicolons,
   newlines, and spaces all split.
2. The candidate registers with that address and is linked automatically. Order
   does not matter — enrolling someone who already has an account links them
   immediately.

A candidate nobody has enrolled has no organization and can therefore see and
start **nothing**. The failure mode of an access check should be "no access", not
"all access".

**Per-exam restriction.** By default an exam is open to the whole organization.
*Who can take this* accepts a pasted list of emails; adding even one flips that
exam to an allow-list, and "Remove all" reopens it.

Entry is by email rather than by picking registered candidates, because an
examiner sets an exam up before the cohort has signed up. Addresses with no
account yet show as **Invited** and start working the moment that person
registers.

Being on an exam's list is necessary, not sufficient — the organization check
still applies. An email that is not on the student roster is accepted (you may be
about to enrol them) but flagged **Not enrolled**.

**Notes**

- Two examiners entering the same organization name share one tenant and one
  roster, matched case- and whitespace-insensitively. That is how a department
  with several examiners works from one candidate list.
- Removing a candidate from the roster revokes access to future exams but keeps
  results they have already submitted.
- Migration `0008` backfills existing installs. The one case it deliberately will
  not guess: a candidate with no attempt history on an install that already has
  several organizations is left unassigned, for an examiner to enrol. Assigning
  them arbitrarily could hand someone another institution's exams.

## Accounts and credentials

Passwords are bcrypt-hashed and can never be read back — not by an admin, not
from the database. A lost password is replaced, not recovered. Making passwords
retrievable would mean one database leak exposes every account.

**Approving an access request does not create a password.** It creates the
account with an unusable random secret and emails a single-use activation link
(`ACTIVATION_TTL_HOURS`, default 72). The new examiner chooses their own password
from that link and is the only person who ever knows it.

An admin *can* set a password directly — the `+ New Examiner` form, and candidate
password resets, which candidates cannot do themselves. Those accounts are
flagged `must_change_password`, and every authenticated page shows the owner a
banner saying somebody else knows their password, with the route to replace it.

**Invitation-only signup.** `REGISTRATION_MODE` defaults to `open`, which is right
for evaluating the software and wrong for an institution. Set it to `invite` and
only addresses already on an exam or organization roster can register — reusing
the roster the exam already required, so there is no second allow-list to keep in
sync.

## Identity verification

A candidate registers a face and an ID card on their profile before sitting an
exam:

- **Face** — a captured frame produces a 512-dimensional L2-normalised ArcFace
  embedding, stored once per candidate. The image is kept so staff can see what
  was enrolled.
- **ID card** — EasyOCR reads the card and token-overlap-matches the text against
  the registered name. The image is kept regardless of whether the name matched,
  so a reviewer can see what was actually presented.

Once both are confirmed the candidate's legal name and email are frozen —
otherwise someone could verify as one person and rename the account to sit an
exam as another.

**Re-verification** is deliberately not the same action as erasing biometrics.
Erasure is a privacy action: someone asked for their data to be removed, and it
goes. Re-verification is an integrity action: somebody looked at the enrolled
face or ID and doubted it, which is exactly when the existing photos must be
*kept*, because they are the evidence that prompted the doubt. The stored images
stay on file until the candidate replaces them; the flag closes the exam gate in
the meantime, and the stated reason is shown to the candidate verbatim.

## Exam taking

- Timer with auto-submit on timeout
- One question at a time, with Previous/Next and a jump-to-question grid
- Question order randomised per attempt when `randomize_questions` is set
- Fullscreen requested on start; exiting is logged as a violation
- Mark-for-review, persisted client-side so it survives a reload
- Results computed server-side on submit — client-side scoring is never trusted

**Autosave and recovery**

- Every answer is saved immediately. Failed saves queue client-side and retry
  with exponential backoff (2 s → 20 s cap), both on a timer and immediately on
  the browser's `online` event. A banner shows only while something is unsynced.
- Submission gets the same treatment, so a network drop at submit time retries
  quietly instead of stranding the candidate with a stopped timer.
- `POST /attempts/start/{exam_id}` is idempotent for an in-progress attempt: it
  returns the *same* attempt with a server-computed `remaining_seconds`, so the
  timer cannot be rewound by reloading.
- `GET /attempts/{id}/answers` returns every saved answer in one call, so the
  navigator repaints answered/unanswered state immediately after a reload.
- The client re-syncs against the server's `remaining_seconds` every 90 s, so a
  long disconnect does not leave the countdown adrift.

## Coding questions

Exams mix MCQ (single and multi-select) and coding questions. Each coding
question carries a language (Python or JavaScript), starter code, stdin/stdout
test cases — sample cases shown to the candidate, hidden ones grading-only — and
a per-case time limit.

**Sandboxing.** `app/services/code_runner_service.py` runs every test case in its
own ephemeral `--network none` container (`python:3.11-slim` / `node:20-slim`)
with memory, CPU, and process-count caps, using the `docker` CLI already on the
host. No separate microservice, no Docker-in-Docker. The submitted code is
base64-encoded and handed to a small trusted bootstrap script rather than
interpolated into a shell string, so the container's real stdin and stdout stay
free for the test case's own input and output.

**Fails closed.** If `docker` is unavailable, `is_available()` returns `False` and
coding questions are reported as ungraded rather than crashing the request.

**Grading flow**, split so sandbox cost stays predictable:

| Endpoint | What it does |
|---|---|
| `PUT /attempts/{id}/code-answer` | Pure autosave, debounced ~1.2 s after the last keystroke and flushed on navigation or submit. Never runs the code |
| `POST /attempts/{id}/code-answer/run` | The candidate's "Run Sample Tests". Sample cases only, ungraded. Hidden cases are never sent to the client |
| `POST /attempts/{id}/submit` | Authoritative grading, once. Every case runs; marks are awarded proportionally (`marks × passed / total`, rounded), so partial credit is possible |

**Known limitation, scoped deliberately:** each test case starts a fresh container
rather than using a warm pool, so grading N cases costs roughly N container
startups. Fine at this scale; a larger deployment would want a pool or a
queue-based worker.

## Violations and review

**No standalone violations feed.** Every violation is reviewed in the context of
the candidate who triggered it, via `GET /attempts/{id}/staff-report`, surfaced at
`/examiner/attempts/:attemptId` and `/admin/attempts/:attemptId`. Each violation
shows its proof screenshot alongside a computed risk score for the attempt as a
whole.

**Reviewer decisions.** `PATCH /proctoring/events/{id}/decision` sets `pending`,
`confirmed`, or `dismissed` (labelled "Misleading" in the UI) on a single
violation. This is an audit annotation only — it never touches scoring or the
attempt's final status. The owning examiner can decide on their candidates'
violations; an admin can decide on any.

**Review queue.** The admin queue orders unreviewed violations by severity then
age, so the oldest untouched high-severity event surfaces first. Reverse
chronological order is the wrong order for a reviewer — an unreviewed
high-severity flag from last week would sink below a week of routine tab
switches, and an undecided flag counts against the candidate.

## Results and reporting

**Results visibility** is set per exam at creation time (`show_results`,
`results_release_mode`): whether a candidate sees their score and pass/fail
outcome at all, and if so, immediately on submission or only after the exam's
`end_time`.

This is a separate gate from `release_results_at` / `show_answers_on_release`,
which govern only the answer *key*. Withholding the score implies withholding the
key; delaying the key alone does not delay the score. See `Exam.results_released`
versus `Exam.answer_key_released`.

**PDF reports.** `GET /attempts/{id}/result/pdf` returns a one-page report —
score, percentage, correct/incorrect/unattempted breakdown, violation count —
generated on the fly with `reportlab` and streamed back with no temp files. The
Results page fetches it with the auth token via `Api.downloadFile()`, since a
plain `<a href>` cannot carry a Bearer token.

**Live CSV export.** `GET /attempts/exam/{exam_id}/export` returns one row per
candidate who has sat the exam — name, roll number, violation count, start and
end time, status, score, result — regenerated from the database on every
download, so it is always current with no sync step to forget.

**Candidate corrections.** An admin can edit a candidate's name, email, or roll
number. Editing an already-verified candidate's name or email unlocks their
identity and flags the account for re-verification automatically; an admin can
also flag it manually without changing anything else. Every edit, and every
re-verification it triggers, is written to the activity log with the actual
before/after values rather than "candidate updated".

## Analytics

`GET /analytics/exams/{exam_id}` (owning examiner or admin) returns the attempt
status breakdown, score minimum/average/maximum, a five-bucket score
distribution, and violation counts by type and severity.
`GET /analytics/platform` (admin only) returns platform-wide totals. Both render
as Chart.js charts — a doughnut and bar chart on the Admin Dashboard, and the
same pair behind the Analytics button in the examiner's per-exam panel.

## Background jobs

Redis backs four things: rate limiting, OTP storage, short-lived distributed
locks, and an RQ job queue with three queues (`emails`, `reports`, `default`) so a
burst of OTP emails never queues behind a slow PDF report.

Two dedicated containers, both sharing the `core-api` image so a job imports the
same services and models the API uses:

- **`scheduler`** — a plain asyncio process serving no traffic. Exam reminders,
  OTP purge, and biometric retention on their own intervals, each pass taking a
  Redis lock first. Pinned to one replica.
- **`worker`** — an RQ worker draining all three queues. Scales freely; RQ workers
  claim jobs atomically.
