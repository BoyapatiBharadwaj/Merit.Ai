import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import DashboardHeader from "../components/DashboardHeader.jsx";
import Icon from "../components/Icon.jsx";
import EmptyState from "../components/EmptyState.jsx";
import { btnPrimary, btnGhost } from "../lib/ui.js";
import { Api, ApiError } from "../lib/api.js";
import { getName } from "../lib/auth.js";

// Every exam returned by GET /exams/available now carries a server-computed `candidate_status`.
const CANDIDATE_STATUS_META = {
  ongoing: { label: "Ongoing", cls: "bg-warning/10 text-warning" },
  upcoming: { label: "Upcoming", cls: "bg-primary/10 text-primary" },
  completed: { label: "Completed", cls: "bg-success/10 text-success" },
  missed: { label: "Missed", cls: "bg-danger/10 text-danger" },
};

function StatusBadge({ status, meta = CANDIDATE_STATUS_META }) {
  const style = meta[status] || { label: status, cls: "bg-page text-muted border border-border" };
  return <span className={`inline-flex items-center rounded-full px-2.5 py-1 text-xs font-semibold ${style.cls}`}>{style.label}</span>;
}

const ATTEMPT_STATUS_LABELS = {
  in_progress: "In Progress",
  submitted: "Submitted",
  auto_submitted: "Auto-submitted",
  terminated: "Terminated",
};

function StatCard({ label, value, tone = "primary", icon, delay = 0 }) {
  const tones = { primary: "text-primary", warning: "text-warning", success: "text-success", danger: "text-danger" };
  const badgeTones = { primary: "bg-primary/10 text-primary", warning: "bg-warning/10 text-warning", success: "bg-success/10 text-success", danger: "bg-danger/10 text-danger" };
  return (
    <div
      className="rounded-2xl border border-border bg-surface shadow-card p-4 animate-slide-up transition-transform duration-200 hover:-translate-y-0.5"
      style={{ animationDelay: `${delay}s` }}
    >
      <div className="flex items-start justify-between gap-2 mb-1.5">
        <div className={`text-2xl font-extrabold tabular-nums ${tones[tone]}`}>
          {value === null ? <span className="skeleton skeleton-text inline-block" style={{ width: "2ch" }}>-</span> : value}
        </div>
        {icon && (
          <span className={`inline-flex items-center justify-center w-8 h-8 rounded-lg shrink-0 ${badgeTones[tone]}`}>
            <Icon name={icon} width={15} height={15} />
          </span>
        )}
      </div>
      <div className="text-xs text-muted">{label}</div>
    </div>
  );
}

/**
 * Live "Xm Ys remaining" countdown for an exam the student is actively taking, computed from
 * the attempt's own started_at + the exam's duration_minutes.
 */
function OngoingCountdown({ startedAt, durationMinutes }) {
  const [remainingSeconds, setRemainingSeconds] = useState(() => computeRemaining(startedAt, durationMinutes));

  useEffect(() => {
    setRemainingSeconds(computeRemaining(startedAt, durationMinutes));
    const id = setInterval(() => setRemainingSeconds(computeRemaining(startedAt, durationMinutes)), 1000);
    return () => clearInterval(id);
  }, [startedAt, durationMinutes]);

  if (remainingSeconds === null) return null;
  const expired = remainingSeconds <= 0;
  const minutes = Math.floor(remainingSeconds / 60);
  const seconds = remainingSeconds % 60;

  return (
    <div className={`flex items-center gap-1.5 text-xs font-semibold tabular-nums ${expired ? "text-danger" : remainingSeconds <= 60 ? "text-danger" : "text-warning"}`}>
      <Icon name="clock" width={13} height={13} />
      {expired ? "Time's up — finalizing" : `${minutes}m ${String(seconds).padStart(2, "0")}s remaining`}
    </div>
  );
}
function computeRemaining(startedAt, durationMinutes) {
  if (!startedAt || !durationMinutes) return null;
  const deadline = new Date(startedAt).getTime() + durationMinutes * 60 * 1000;
  return Math.max(0, Math.round((deadline - Date.now()) / 1000));
}

export default function StudentDashboard() {
  const navigate = useNavigate();
  const [exams, setExams] = useState(null);
  const [identity, setIdentity] = useState(null);
  const [loadError, setLoadError] = useState("");
  // "ok" | "unknown" -- whether the identity check itself could be reached.
  const [identityState, setIdentityState] = useState("ok");
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoadError("");
      try {
        // Settled, not all: a failing identity check must not blank the exam list, and a
        // failing exam list must not be masked by a working identity check.
        const [examsResult, identityResult] = await Promise.allSettled([
          Api.get("/exams/available"),
          // Mirrors the server-side gate in start_attempt so the student sees
          // *why* they can't start before clicking, instead of hitting a 403.
          Api.get("/proctoring/identity/status"),
        ]);
        if (cancelled) return;

        if (examsResult.status === "fulfilled") {
          setExams(examsResult.value);
        } else {
          const err = examsResult.reason;
          setLoadError(err instanceof ApiError ? err.message
            : "Couldn't load your exams. Please try again.");
        }

        if (identityResult.status === "fulfilled") {
          setIdentity(identityResult.value);
          setIdentityState("ok");
        } else {
          setIdentity(null);
          setIdentityState("unknown");
        }
      } catch (err) {
        if (cancelled) return;
        setLoadError(err instanceof ApiError ? err.message : "Couldn't load your dashboard. Please try again.");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [reloadKey]);

  const derived = useMemo(() => {
    if (!exams) return null;
    const byStatus = { ongoing: [], upcoming: [], completed: [], missed: [] };
    exams.forEach((e) => {
      (byStatus[e.candidate_status] || byStatus.upcoming).push(e);
    });

    const scored = byStatus.completed.filter((e) => e.percentage !== null && e.percentage !== undefined);
    const average = scored.length ? (scored.reduce((sum, e) => sum + e.percentage, 0) / scored.length).toFixed(1) : null;

    const now = Date.now();
    const announcements = [];
    byStatus.upcoming.forEach((e) => {
      if (!e.start_time) return;
      const startsInHrs = (new Date(e.start_time).getTime() - now) / 36e5;
      if (startsInHrs > 0 && startsInHrs <= 48) {
        announcements.push({
          key: `start-${e.id}`,
          tone: "info",
          text: `"${e.title}" opens in ${startsInHrs < 1 ? `${Math.round(startsInHrs * 60)} min` : `${Math.round(startsInHrs)} hr`}.`,
        });
      }
    });
    byStatus.ongoing.forEach((e) => {
      if (e.attempt_status || !e.end_time) return; // already started, or no closing deadline to warn about
      const closesInHrs = (new Date(e.end_time).getTime() - now) / 36e5;
      if (closesInHrs > 0 && closesInHrs <= 24) {
        announcements.push({
          key: `end-${e.id}`,
          tone: "warn",
          text: `"${e.title}" closes in ${closesInHrs < 1 ? `${Math.round(closesInHrs * 60)} min` : `${Math.round(closesInHrs)} hr`} — don't miss it.`,
        });
      }
    });

    return { ...byStatus, average, announcements };
  }, [exams]);

  const firstName = (getName() || "Student").split(" ")[0];

  return (
    <div className="min-h-screen flex flex-col bg-page text-ink">
      <DashboardHeader title="Dashboard" />

      <main className="flex-1 max-w-7xl mx-auto w-full px-5 sm:px-6 lg:px-8 py-8">
        <div className="mb-7 animate-fade-in">
          <h1 className="text-2xl font-extrabold tracking-tight">Welcome back, {firstName}</h1>
          <p className="text-sm text-muted mt-1">Here&apos;s what&apos;s on your plate today.</p>
        </div>

        {loadError && (
          <div role="alert"
               className="mb-6 flex flex-wrap items-start gap-2.5 rounded-xl border border-danger/30 bg-danger/5 px-4 py-3 text-sm text-danger">
            <Icon name="alert" width={16} height={16} className="mt-0.5 shrink-0" />
            <span className="flex-1 min-w-[12rem]">{loadError}</span>
            {/* A retry, not just a message. The banner previously left the only
                recovery as a full page reload, which a candidate mid-exam-day
                is entitled not to have to guess at. */}
            <button type="button" onClick={() => setReloadKey((n) => n + 1)}
                    className="font-semibold underline underline-offset-2 hover:no-underline">
              Try again
            </button>
          </div>
        )}

        {/* Distinct from "not verified". See identityState above. */}
        {identityState === "unknown" && (
          <div role="status"
               className="mb-6 flex flex-wrap items-start gap-2.5 rounded-xl border border-warning/30 bg-warning/5 px-4 py-3 text-sm text-warning">
            <Icon name="alert" width={16} height={16} className="mt-0.5 shrink-0" />
            <span className="flex-1 min-w-[12rem]">
              We couldn&apos;t check your identity verification status. This does not mean you are
              unverified — if you have already registered your face and ID, you are still verified.
            </span>
            <button type="button" onClick={() => setReloadKey((n) => n + 1)}
                    className="font-semibold underline underline-offset-2 hover:no-underline">
              Check again
            </button>
          </div>
        )}

        {/* Checked BEFORE the generic verification banner. A candidate whose
            face and ID are both on file is not "incomplete", and telling them
            so sends them to a Profile page showing two green ticks -- from
            which the only reasonable conclusion is that the platform is
            broken. This is a different situation and says so. */}
        {identityState === "ok" && identity?.reverification_required && (
          <div role="status"
               className="mb-6 rounded-2xl border border-warning/40 bg-warning/5 p-5 animate-fade-in">
            <div className="flex items-start gap-3">
              <span className="inline-flex items-center justify-center w-9 h-9 rounded-xl bg-warning/15 text-warning shrink-0">
                <Icon name="shield-check" width={17} height={17} />
              </span>
              <div className="min-w-0">
                <p className="font-bold text-ink mb-1">Verify your identity again</p>
                <p className="text-sm text-muted leading-relaxed mb-2">
                  Your institution has asked you to re-register your face and re-submit your ID
                  card. Until both are done you can&apos;t start a proctored exam.
                </p>
                {identity.reverification_reason && (
                  <p className="text-sm text-ink bg-page border border-border rounded-lg px-3 py-2 mb-3">
                    <span className="font-semibold">Reason given:</span>{" "}
                    {identity.reverification_reason}
                  </p>
                )}
                <button type="button" onClick={() => navigate("/profile")}
                        className={`${btnPrimary.replace("px-5 py-3", "px-4 py-2")} text-sm`}>
                  Go to my Profile
                </button>
              </div>
            </div>
          </div>
        )}

        {identityState === "ok" && identity && !identity.exam_ready
          && !identity.reverification_required && (
          <VerificationBanner identity={identity} />
        )}
        <div className="grid grid-cols-2 lg:grid-cols-5 gap-4 mb-8">
          <StatCard label="Ongoing" value={derived ? derived.ongoing.length : null} tone="warning" icon="clock" delay={0} />
          <StatCard label="Upcoming" value={derived ? derived.upcoming.length : null} tone="primary" icon="layout" delay={0.05} />
          <StatCard label="Completed" value={derived ? derived.completed.length : null} tone="success" icon="check" delay={0.1} />
          <StatCard label="Missed" value={derived ? derived.missed.length : null} tone="danger" icon="alert" delay={0.15} />
          <StatCard label="Average Score" value={derived ? (derived.average !== null ? `${derived.average}%` : "-") : null} tone="primary" icon="chart" delay={0.2} />
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-[1fr_300px] gap-6">
          <div className="flex flex-col gap-8 min-w-0">
            <section>
              <h2 className="text-xs font-bold uppercase tracking-wider text-muted mb-3">Ongoing</h2>
              {!derived ? (
                <div className="grid sm:grid-cols-2 gap-4">
                  <div className="skeleton skeleton-card" />
                  <div className="skeleton skeleton-card" />
                </div>
              ) : derived.ongoing.length ? (
                <div className="grid sm:grid-cols-2 gap-4">
                  {derived.ongoing.map((e) => (
                    <OngoingCard key={e.id} exam={e} identity={identity} navigate={navigate} />
                  ))}
                </div>
              ) : (
                <EmptyState icon="clock" title="Nothing ongoing" description="Exams that are currently open, or that you're actively taking, show up here." />
              )}
            </section>

            <section>
              <h2 className="text-xs font-bold uppercase tracking-wider text-muted mb-3">Upcoming</h2>
              {!derived ? (
                <div className="grid sm:grid-cols-2 gap-4">
                  <div className="skeleton skeleton-card" />
                  <div className="skeleton skeleton-card" />
                </div>
              ) : derived.upcoming.length ? (
                <div className="grid sm:grid-cols-2 gap-4">
                  {derived.upcoming.map((e) => (
                    <article
                      key={e.id}
                      className="rounded-2xl border border-border bg-surface shadow-card p-5 flex flex-col animate-fade-in transition-all duration-200 hover:-translate-y-0.5 hover:border-primary/30 hover:shadow-[0_16px_32px_-16px_rgba(15,23,42,0.2)]"
                    >
                      <div className="flex items-center justify-between gap-2 mb-2">
                        <strong className="text-sm">{e.title}</strong>
                        <StatusBadge status="upcoming" />
                      </div>
                      <p className="text-xs text-muted mb-3 line-clamp-2">{e.description || "No description provided."}</p>
                      <p className="text-xs text-muted mb-4">
                        {e.duration_minutes} minutes{e.proctoring_enabled ? " · AI Proctored" : ""}
                      </p>
                      <p className="text-xs font-semibold text-primary mb-4">
                        Opens {e.start_time ? new Date(e.start_time).toLocaleString() : "soon"}
                      </p>
                      <button disabled className={`${btnGhost.replace("px-5 py-3", "px-4 py-2")} mt-auto opacity-60 cursor-not-allowed`} title="Not open yet">
                        <Icon name="clock" width={14} height={14} />
                        Not open yet
                      </button>
                    </article>
                  ))}
                </div>
              ) : (
                // A student who no examiner has enrolled sees an empty list and has no way to
                // tell that from "my institution hasn't published anything yet".
                <EmptyState
                  icon="layout"
                  title="No upcoming exams"
                  description={
                    exams && exams.length === 0
                      ? "Exams appear here once your institution enrols you and publishes them. If you're expecting one, check with your examiner that they've added this email address to their student list."
                      : "New exams scheduled for a future date will show up here as soon as they're published."
                  }
                />
              )}
            </section>

            <section>
              <h2 className="text-xs font-bold uppercase tracking-wider text-muted mb-3">Completed</h2>
              {derived && derived.completed.length === 0 ? (
                <EmptyState icon="check" title="No completed exams yet" description="Finished exams and their scores will be listed here." />
              ) : (
                <div className="rounded-2xl border border-border bg-surface shadow-card overflow-x-auto">
                  <table className="min-w-full text-sm">
                    <thead>
                      <tr className="text-left text-xs text-muted uppercase tracking-wide border-b border-border">
                        <th className="px-4 py-3 font-semibold">Exam</th>
                        <th className="px-4 py-3 font-semibold">Score</th>
                        <th className="px-4 py-3 font-semibold">Percentage</th>
                        <th className="px-4 py-3 font-semibold">Submitted</th>
                        <th className="px-4 py-3 font-semibold"></th>
                      </tr>
                    </thead>
                    <tbody>
                      {!derived ? (
                        <>
                          <tr><td colSpan={5} className="px-4 py-3"><span className="skeleton skeleton-line block" /></td></tr>
                          <tr><td colSpan={5} className="px-4 py-3"><span className="skeleton skeleton-line block" /></td></tr>
                        </>
                      ) : (
                        derived.completed.map((e) => (
                          <tr key={e.id} className="border-b border-border last:border-0 hover:bg-page/60 transition-colors">
                            <td className="px-4 py-3 font-medium">
                              <div className="flex items-center gap-2">
                                {e.title}
                                <span className="text-[10px] font-semibold text-muted uppercase">{ATTEMPT_STATUS_LABELS[e.attempt_status] || e.attempt_status}</span>
                              </div>
                            </td>
                            <td className="px-4 py-3 tabular-nums">{e.scored_marks !== null ? `${e.scored_marks}/${e.total_marks}` : "-"}</td>
                            <td className="px-4 py-3 tabular-nums">{e.percentage !== null && e.percentage !== undefined ? `${e.percentage}%` : "-"}</td>
                            <td className="px-4 py-3 text-muted">{e.submitted_at ? new Date(e.submitted_at).toLocaleString() : "-"}</td>
                            <td className="px-4 py-3">
                              <button
                                onClick={() => navigate(`/results/${e.attempt_id}`)}
                                className={`${btnGhost.replace("px-5 py-3", "px-3 py-1.5")} text-xs`}
                              >
                                View Report
                              </button>
                            </td>
                          </tr>
                        ))
                      )}
                    </tbody>
                  </table>
                </div>
              )}
            </section>

            <section>
              <h2 className="text-xs font-bold uppercase tracking-wider text-muted mb-3">Missed</h2>
              {!derived ? (
                <div className="skeleton skeleton-card h-16" />
              ) : derived.missed.length === 0 ? (
                <EmptyState icon="alert" title="Nothing missed" description="Exams whose window closes before you start or submit them will be listed here." />
              ) : (
                <div className="rounded-2xl border border-danger/20 bg-danger/5 shadow-card overflow-x-auto">
                  <table className="min-w-full text-sm">
                    <thead>
                      <tr className="text-left text-xs text-muted uppercase tracking-wide border-b border-danger/20">
                        <th className="px-4 py-3 font-semibold">Exam</th>
                        <th className="px-4 py-3 font-semibold">Closed</th>
                      </tr>
                    </thead>
                    <tbody>
                      {derived.missed.map((e) => (
                        <tr key={e.id} className="border-b border-danger/10 last:border-0">
                          <td className="px-4 py-3 font-medium">{e.title}</td>
                          <td className="px-4 py-3 text-muted">{e.end_time ? new Date(e.end_time).toLocaleString() : "-"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </section>
          </div>

          <aside className="flex flex-col gap-4">
            <div className="rounded-2xl border border-border bg-surface shadow-card p-4">
              <h2 className="text-xs font-bold uppercase tracking-wider text-muted mb-3">Announcements</h2>
              <div className="flex flex-col gap-2.5 text-sm">
                {!derived ? (
                  <>
                    <span className="skeleton skeleton-line block" />
                    <span className="skeleton skeleton-line block" />
                  </>
                ) : derived.announcements.length ? (
                  derived.announcements.map((item) => (
                    <div
                      key={item.key}
                      className={`rounded-lg border px-3 py-2.5 ${
                        item.tone === "warn" ? "border-warning/30 bg-warning/10 text-warning" : "border-border bg-page text-ink"
                      }`}
                    >
                      {item.text}
                    </div>
                  ))
                ) : (
                  <p className="text-muted">You&apos;re all caught up. No new announcements.</p>
                )}
              </div>
            </div>
          </aside>
        </div>
      </main>
    </div>
  );
}

/** One "Ongoing" card -- either an exam the student is actively taking
 * (attempt in progress, shows a live countdown + Resume) or one whose window
 * is open but they haven't started yet (shows Start Exam, gated on identity
 * verification exactly like the old "Upcoming" section used to be). */
function OngoingCard({ exam, identity, navigate }) {
  const inProgress = exam.attempt_status === "in_progress";
  return (
    <article
      className={`rounded-2xl border shadow-card p-5 flex flex-col transition-all duration-200 hover:-translate-y-0.5 ${
        inProgress ? "border-warning/30 bg-warning/5 hover:shadow-[0_16px_32px_-16px_rgba(217,119,6,0.25)]" : "border-border bg-surface hover:border-primary/30"
      }`}
    >
      <div className="flex items-center justify-between gap-2 mb-2">
        <strong className="text-sm">{exam.title}</strong>
        <StatusBadge status="ongoing" />
      </div>
      {inProgress ? (
        <>
          <p className="text-xs text-muted mb-2">Started {exam.started_at ? new Date(exam.started_at).toLocaleString() : "-"}</p>
          <div className="mb-4">
            <OngoingCountdown startedAt={exam.started_at} durationMinutes={exam.duration_minutes} />
          </div>
          <button onClick={() => navigate(`/exam/${exam.id}`)} className={`${btnPrimary.replace("px-5 py-3", "px-4 py-2")} mt-auto`}>
            Resume Exam
          </button>
        </>
      ) : (
        <>
          <p className="text-xs text-muted mb-3 line-clamp-2">{exam.description || "No description provided."}</p>
          <p className="text-xs text-muted mb-4">
            {exam.duration_minutes} minutes{exam.proctoring_enabled ? " · AI Proctored" : ""}
          </p>
          {exam.proctoring_enabled && identity && identity.exam_ready === false ? (
            <button
              onClick={() => navigate("/profile")}
              className={`${btnGhost.replace("px-5 py-3", "px-4 py-2")} mt-auto`}
              title="Complete face and ID verification to unlock this exam"
            >
              <Icon name="lock" width={14} height={14} />
              Verify to Unlock
            </button>
          ) : (
            <button onClick={() => navigate(`/exam/${exam.id}`)} className={`${btnPrimary.replace("px-5 py-3", "px-4 py-2")} mt-auto`}>
              Start Exam
            </button>
          )}
        </>
      )}
    </article>
  );
}

/**
 * Shown until face + ID verification are both complete.
 */
function VerificationBanner({ identity }) {
  const steps = [
    { done: identity.face_registered, label: "Register your face" },
    { done: identity.id_verified, label: "Verify your ID card" },
  ];
  const remaining = steps.filter((s) => !s.done).length;

  return (
    <div className="mb-6 rounded-2xl border border-warning/30 bg-warning/5 p-5 animate-fade-in">
      <div className="flex flex-col sm:flex-row sm:items-center gap-4">
        <span className="inline-flex items-center justify-center w-11 h-11 rounded-xl bg-warning/15 text-warning shrink-0">
          <Icon name="shield" width={20} height={20} />
        </span>

        <div className="flex-1 min-w-0">
          <p className="text-sm font-bold text-ink mb-1">
            {remaining === 2 ? "Verify your identity to unlock proctored exams" : "One more step to unlock proctored exams"}
          </p>
          <ul className="flex flex-wrap items-center gap-x-4 gap-y-1">
            {steps.map((s) => (
              <li key={s.label} className="flex items-center gap-1.5 text-xs">
                <span
                  className={`inline-flex items-center justify-center w-4 h-4 rounded-full shrink-0 ${
                    s.done ? "bg-success text-white" : "border border-warning/40 text-warning"
                  }`}
                >
                  <Icon name={s.done ? "check" : "clock"} width={9} height={9} />
                </span>
                <span className={s.done ? "text-muted line-through" : "text-ink font-medium"}>{s.label}</span>
              </li>
            ))}
          </ul>
        </div>

        <Link to="/profile" className={`${btnPrimary.replace("px-5 py-3", "px-4 py-2")} shrink-0 self-start sm:self-auto`}>
          Verify Now
        </Link>
      </div>
    </div>
  );
}
