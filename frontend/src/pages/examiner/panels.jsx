/* Split out of ExaminerDashboard.jsx -- see examiner/shared.jsx for why. */
import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import Icon from "../../components/Icon.jsx";
import EmptyState from "../../components/EmptyState.jsx";
import IdentityPhotoModal from "../../components/IdentityPhotoModal.jsx";
import { Api, ApiError } from "../../lib/api.js";
import { btnPrimary, btnGhost, fieldInput, fieldLabel, fieldInputCompact, fieldLabelCompact } from "../../lib/ui.js";
import "../../lib/vendorChart.js";
import { CHART_PALETTE, AsyncSection, ErrorState, LoadingRows, PageControls } from "./shared.jsx";

function AttemptsPanel({ examId }) {
  const [attempts, setAttempts] = useState([]);
  const [pageInfo, setPageInfo] = useState({ page: 1, total: 0, total_pages: 0 });
  const [page, setPage] = useState(1);
  const [state, setState] = useState("loading");
  const [loadError, setLoadError] = useState("");
  const [resetTargetId, setResetTargetId] = useState(null); // student_id currently showing the reason form
  const [reason, setReason] = useState("");
  const [resetting, setResetting] = useState(false);
  const [resetError, setResetError] = useState(null); // { studentId, message } | null
  const [viewingStudent, setViewingStudent] = useState(null); // { id, name } | null
  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState("");

  async function handleExport() {
    setExporting(true);
    setExportError("");
    try {
      await Api.downloadFile(`/attempts/exam/${examId}/export`, `exam_${examId}_results.csv`);
    } catch (err) {
      setExportError(err instanceof ApiError ? err.message : "Couldn't export results.");
    } finally {
      setExporting(false);
    }
  }

  // `.catch(() => setAttempts([]))` rendered a failed request as "No attempts
  // yet" -- indistinguishable from an exam nobody had sat. An examiner checking
  // whether their cohort had turned up would read the failure as an answer.
  function reload() {
    setState("loading");
    Api.get(`/attempts/exam/${examId}?page=${page}`)
      .then((data) => {
        setAttempts(data.items || []);
        setPageInfo({ page: data.page, total: data.total, total_pages: data.total_pages });
        setState("ready");
      })
      .catch((err) => {
        setLoadError(err instanceof ApiError ? err.message : "Couldn't load attempts.");
        setState("error");
      });
  }

  useEffect(() => {
    let cancelled = false;
    setState("loading");
    Api.get(`/attempts/exam/${examId}?page=${page}`)
      .then((data) => {
        if (cancelled) return;
        setAttempts(data.items || []);
        setPageInfo({ page: data.page, total: data.total, total_pages: data.total_pages });
        setState("ready");
      })
      .catch((err) => {
        if (cancelled) return;
        setLoadError(err instanceof ApiError ? err.message : "Couldn't load attempts.");
        setState("error");
      });
    return () => { cancelled = true; };
  }, [examId, page]);

  function startReset(studentId) {
    setResetTargetId(studentId);
    setReason("");
    setResetError(null);
  }

  async function confirmReset(studentId) {
    if (reason.trim().length < 3) {
      setResetError({ studentId, message: "Please enter a reason (at least 3 characters)." });
      return;
    }
    setResetting(true);
    setResetError(null);
    try {
      await Api.post(`/attempts/exam/${examId}/student/${studentId}/reset`, { reason: reason.trim() });
      setResetTargetId(null);
      setReason("");
      reload();
    } catch (err) {
      setResetError({ studentId, message: err instanceof ApiError ? err.message : "Couldn't reset this student's exam." });
    } finally {
      setResetting(false);
    }
  }

  if (state === "loading") return (
    <div className="rounded-2xl border border-border bg-surface shadow-card p-5 mt-6">
      <div className="font-semibold text-ink mb-3">Student Attempts</div>
      <LoadingRows rows={4} />
    </div>
  );
  if (state === "error") return (
    <div className="rounded-2xl border border-border bg-surface shadow-card p-5 mt-6">
      <div className="font-semibold text-ink mb-3">Student Attempts</div>
      <ErrorState message={loadError} onRetry={reload} />
    </div>
  );

  return (
    <div className="rounded-2xl border border-border bg-surface shadow-card p-5 mt-6">
      <div className="flex flex-wrap items-center justify-between gap-3 mb-3">
        <div className="font-semibold text-ink">Student Attempts</div>
        <div className="flex flex-col items-end gap-1">
          <button type="button" onClick={handleExport} disabled={exporting}
                  className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold rounded-lg border border-border text-ink hover:border-primary hover:text-primary transition-colors disabled:opacity-60">
            <Icon name="doc" width={13} height={13} />
            {exporting ? "Preparing…" : "Export results (CSV)"}
          </button>
          {exportError && <span className="text-xs text-danger">{exportError}</span>}
        </div>
      </div>
      <div className="overflow-x-auto">
        <table className="min-w-full text-sm">
          <thead>
            <tr className="text-left text-xs text-muted uppercase tracking-wide border-b border-border">
              <th className="py-2 pr-4 font-semibold">Student</th>
              <th className="py-2 pr-4 font-semibold">Status</th>
              <th className="py-2 pr-4 font-semibold">Score</th>
              <th className="py-2 pr-4 font-semibold">Submitted</th>
              <th className="py-2 pr-4 font-semibold">Violations</th>
              <th className="py-2 pr-4 font-semibold">Actions</th>
            </tr>
          </thead>
          <tbody>
            {attempts.length ? (
              attempts.map((a) => (
                <tr key={a.attempt_id} className="border-b border-border last:border-0 hover:bg-page/60 transition-colors align-top">
                  <td className="py-2 pr-4 font-medium">{a.student_name}</td>
                  <td className="py-2 pr-4 capitalize">{a.status}</td>
                  <td className="py-2 pr-4 tabular-nums">{a.scored_marks !== null ? `${a.scored_marks}/${a.total_marks}` : "-"}</td>
                  <td className="py-2 pr-4 text-muted">{a.submitted_at ? new Date(a.submitted_at).toLocaleString() : "-"}</td>
                  <td className="py-2 pr-4">
                    {a.violation_count > 0 ? (
                      <span className="inline-flex items-center rounded-full px-2 py-0.5 text-xs font-semibold bg-danger/10 text-danger">
                        {a.violation_count}
                      </span>
                    ) : (
                      <span className="text-xs text-muted">—</span>
                    )}
                  </td>
                  {/* GET /attempts/{id}/staff-report -- the full per-question
                      breakdown, identity check, risk score and every
                      violation with its evidence -- reached here via
                      "Review", the only place an examiner sees violations. */}
                  <td className="py-2 pr-4">
                    <div className="flex items-center gap-3 mb-1.5">
                      <Link
                        to={`/examiner/attempts/${a.attempt_id}`}
                        className="text-xs font-semibold text-primary hover:underline"
                      >
                        Review
                      </Link>
                      <button
                        type="button"
                        onClick={() => setViewingStudent({ id: a.student_id, name: a.student_name })}
                        className="text-xs font-semibold text-primary hover:underline"
                      >
                        View ID
                      </button>
                    </div>
                    {resetTargetId === a.student_id ? (
                      <div className="flex flex-col gap-1.5 min-w-[220px]">
                        <input
                          autoFocus
                          value={reason}
                          onChange={(e) => setReason(e.target.value)}
                          placeholder="Reason (e.g. browser crash)"
                          className={`${fieldInputCompact} text-xs`}
                        />
                        <div className="flex items-center gap-3">
                          <button
                            type="button"
                            onClick={() => confirmReset(a.student_id)}
                            disabled={resetting}
                            className="text-xs font-semibold text-danger hover:underline disabled:opacity-60"
                          >
                            {resetting ? "Resetting…" : "Confirm reset"}
                          </button>
                          <button
                            type="button"
                            onClick={() => setResetTargetId(null)}
                            disabled={resetting}
                            className="text-xs font-semibold text-muted hover:text-ink disabled:opacity-60"
                          >
                            Cancel
                          </button>
                        </div>
                        {resetError?.studentId === a.student_id && <p className="text-xs text-danger">{resetError.message}</p>}
                      </div>
                    ) : (
                      <button type="button" onClick={() => startReset(a.student_id)} className="text-xs font-semibold text-primary hover:underline">
                        Reset
                      </button>
                    )}
                  </td>
                </tr>
              ))
            ) : (
              <tr><td colSpan={6} className="py-6 text-muted text-center">No attempts yet.</td></tr>
            )}
          </tbody>
        </table>
      </div>
      <p className="text-xs text-muted mt-3">
        Resetting clears a student's answers and lets them retake the exam from scratch -- use it for connectivity
        problems, browser crashes, power failures, or other technical interruptions. Every reset is logged with your
        name, the time, and the reason you enter.
      </p>
      <PageControls
        page={pageInfo.page}
        totalPages={pageInfo.total_pages}
        total={pageInfo.total}
        onChange={setPage}
        noun="attempt"
      />
      <IdentityPhotoModal
        studentId={viewingStudent?.id}
        studentName={viewingStudent?.name}
        onClose={() => setViewingStudent(null)}
      />
    </div>
  );
}

function ActivePanel({ examId }) {
  const [active, setActive] = useState(null);
  const [pollError, setPollError] = useState("");

  useEffect(() => {
    let cancelled = false;
    async function refresh() {
      try {
        // The dedicated endpoint, not every attempt filtered in the browser.
        // This polls every ten seconds for as long as the page is open, so the
        // old approach re-downloaded the exam's entire sitting history each
        // time to find the two people currently writing.
        const attempts = await Api.get(`/attempts/exam/${examId}/active`);
        if (!cancelled) { setActive(attempts); setPollError(""); }
      } catch (err) {
        // Keep the last known list on screen rather than replacing live
        // candidates with "nobody is taking this exam" because one poll failed.
        if (!cancelled) setPollError(err instanceof ApiError ? err.message : "Live updates interrupted.");
      }
    }
    refresh();
    const interval = setInterval(refresh, 10000);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [examId]);

  return (
    <div className="rounded-2xl border border-border bg-surface shadow-card p-5 mt-6">
      <div className="flex items-center justify-between mb-3">
        <div className="font-semibold text-ink">
          Active Now <span className="text-xs font-normal text-muted">(auto-refreshes every 10s)</span>
        </div>
        <span className="inline-flex items-center rounded-full px-2.5 py-1 text-xs font-semibold bg-warning/10 text-warning">
          {active ? active.length : 0} in progress
        </span>
      </div>
      {pollError && (
        <p role="status" className="mb-3 text-xs text-warning">
          {pollError} Showing the last successful update.
        </p>
      )}
      <div className="overflow-x-auto">
        <table className="min-w-full text-sm">
          <thead>
            <tr className="text-left text-xs text-muted uppercase tracking-wide border-b border-border">
              <th className="py-2 pr-4 font-semibold">Student</th>
              <th className="py-2 pr-4 font-semibold">Started</th>
              <th className="py-2 pr-4 font-semibold">Elapsed</th>
              <th className="py-2 pr-4 font-semibold">Time left</th>
              <th className="py-2 font-semibold">Flags</th>
            </tr>
          </thead>
          <tbody>
            {!active ? (
              <tr><td colSpan={5} className="py-3"><span className="skeleton skeleton-line block" /></td></tr>
            ) : active.length ? (
              active.map((a, i) => {
                const startedAt = a.started_at ? new Date(a.started_at) : null;
                const elapsedMin = startedAt ? Math.max(0, Math.round((Date.now() - startedAt.getTime()) / 60000)) : null;
                return (
                  <tr key={i} className="border-b border-border last:border-0 hover:bg-page/60 transition-colors">
                    <td className="py-2 pr-4 font-medium">{a.student_name}</td>
                    <td className="py-2 pr-4 text-muted">{startedAt ? startedAt.toLocaleTimeString() : "-"}</td>
                    <td className="py-2 pr-4">{elapsedMin !== null ? `${elapsedMin} min` : "-"}</td>
                    {/* From the server's clock, not this browser's. */}
                    <td className="py-2 pr-4 tabular-nums">
                      {typeof a.remaining_seconds === "number"
                        ? `${Math.floor(a.remaining_seconds / 60)}:${String(a.remaining_seconds % 60).padStart(2, "0")}`
                        : "-"}
                    </td>
                    <td className="py-2">
                      {a.violation_count > 0 ? (
                        <span className="inline-flex items-center rounded-full px-2 py-0.5 text-xs font-semibold bg-danger/10 text-danger">
                          {a.violation_count}
                        </span>
                      ) : (
                        <span className="text-xs text-muted">—</span>
                      )}
                    </td>
                  </tr>
                );
              })
            ) : (
              <tr><td colSpan={5} className="py-6 text-muted text-center">No one is taking this exam right now.</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/* There used to be a ViolationsPanel/ViolationEvidence pair here: a
 * cross-candidate "every violation in this exam" tab. It's gone -- an
 * examiner now reviews violations only per student, via the "Review" link
 * in AttemptsPanel above, which opens examiner/AttemptReport.jsx (every
 * violation for THAT candidate, with evidence, risk score, and a decision
 * control). Reviewing conduct is inherently a per-candidate judgement, and
 * a flat list across the whole exam encouraged deciding on rows out of
 * context. */

function AnalyticsPanel({ examId }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const scoreChartRef = useRef(null);
  const severityChartRef = useRef(null);
  const chartInstances = useRef({});

  useEffect(() => {
    let cancelled = false;
    Api.get(`/analytics/exams/${examId}`)
      .then((d) => !cancelled && setData(d))
      .catch((err) => !cancelled && setError(err instanceof ApiError ? err.message : "Couldn't load analytics."));
    return () => {
      cancelled = true;
    };
  }, [examId]);

  useEffect(() => {
    if (!data || !window.Chart) return;
    const styles = getComputedStyle(document.documentElement);
    const textColor = styles.getPropertyValue("--text-muted").trim() || "#64748b";
    const gridColor = styles.getPropertyValue("--border").trim() || "#e2e8f0";

    Object.values(chartInstances.current).forEach((c) => c?.destroy());

    chartInstances.current.score = new window.Chart(scoreChartRef.current, {
      type: "bar",
      data: {
        labels: data.score_distribution.map((b) => b.range),
        datasets: [{ label: "Students", data: data.score_distribution.map((b) => b.count), backgroundColor: "#2563eb" }],
      },
      options: {
        plugins: { legend: { display: false } },
        scales: {
          x: { ticks: { color: textColor }, grid: { color: gridColor } },
          y: { beginAtZero: true, ticks: { color: textColor, precision: 0 }, grid: { color: gridColor } },
        },
      },
    });

    const severityLabels = Object.keys(data.violations_by_severity);
    const severityCounts = Object.values(data.violations_by_severity);
    chartInstances.current.severity = new window.Chart(severityChartRef.current, {
      type: "doughnut",
      data: { labels: severityLabels.length ? severityLabels : ["No violations yet"], datasets: [{ data: severityCounts.length ? severityCounts : [1], backgroundColor: CHART_PALETTE }] },
      options: { plugins: { legend: { labels: { color: textColor } } } },
    });

    return () => Object.values(chartInstances.current).forEach((c) => c?.destroy());
  }, [data]);

  if (error) {
    return (
      <div className="rounded-2xl border border-danger/30 bg-danger/5 p-5 mt-6 text-sm text-danger">{error}</div>
    );
  }

  const totalViolations = data ? Object.values(data.violations_by_type).reduce((a, b) => a + b, 0) : null;

  return (
    <div className="mt-6">
      <div className="grid md:grid-cols-2 gap-4">
        <div className="rounded-2xl border border-border bg-surface shadow-card p-4">
          <div className="font-semibold text-ink mb-3">Score Distribution</div>
          <canvas ref={scoreChartRef} height="220" />
        </div>
        <div className="rounded-2xl border border-border bg-surface shadow-card p-4">
          <div className="font-semibold text-ink mb-3">Violations by Severity</div>
          <canvas ref={severityChartRef} height="220" />
        </div>
      </div>
      <div className="rounded-2xl border border-border bg-surface shadow-card p-4 mt-4">
        <div className="grid grid-cols-3 gap-4 text-center">
          <div>
            <div className="text-2xl font-extrabold text-primary tabular-nums">{data ? data.total_attempts : "-"}</div>
            <div className="text-xs text-muted mt-1">Total Attempts</div>
          </div>
          <div>
            <div className="text-2xl font-extrabold text-primary tabular-nums">{data ? (data.average_percentage !== null ? `${data.average_percentage}%` : "-") : "-"}</div>
            <div className="text-xs text-muted mt-1">Average Score</div>
          </div>
          <div>
            <div className="text-2xl font-extrabold text-danger tabular-nums">{totalViolations !== null ? totalViolations : "-"}</div>
            <div className="text-xs text-muted mt-1">Total Violations</div>
          </div>
        </div>
      </div>
    </div>
  );
}

/* ===================== Exam schedule editing ===================== */

// Formats an ISO datetime string as the local-time value a <input
// type="datetime-local"> expects ("YYYY-MM-DDTHH:mm") -- the inverse of the
// `new Date(value).toISOString()` conversion CreateExamForm uses going the
// other way. Needed here (and not there) because this form, unlike create,
// has to pre-fill from an existing value.

function ExamAccessPanel({ examId }) {
  const [access, setAccess] = useState(null);
  const [emails, setEmails] = useState("");
  const [saving, setSaving] = useState(false);
  const [feedback, setFeedback] = useState(null);
  const [loadError, setLoadError] = useState("");
  const [busyIds, setBusyIds] = useState(() => new Set());

  const load = useCallback(async () => {
    setLoadError("");
    try {
      setAccess(await Api.get(`/organizations/exams/${examId}/access`));
    } catch (err) {
      setAccess({ participants: [], restricted: false, organization_name: null });
      setLoadError(err instanceof ApiError ? err.message : "Couldn't load exam access.");
    }
  }, [examId]);

  useEffect(() => {
    load();
  }, [load]);

  async function handleAdd(event) {
    event.preventDefault();
    if (!emails.trim()) return;
    setSaving(true);
    setFeedback(null);
    try {
      const result = await Api.post(`/organizations/exams/${examId}/participants`, { emails });
      const parts = [];
      if (result.added.length) parts.push(`${result.added.length} added`);
      if (result.already_present.length) parts.push(`${result.already_present.length} already on the list`);
      const invalid = result.invalid || [];
      if (invalid.length) {
        parts.push(`${invalid.length} skipped (not an email): ${invalid.slice(0, 3).join(", ")}${invalid.length > 3 ? "…" : ""}`);
      }
      setFeedback({
        tone: invalid.length ? "warn" : result.added.length ? "success" : "muted",
        message: parts.join(" · ") || "Nothing to add.",
      });
      if (!invalid.length) setEmails("");
      await load();
    } catch (err) {
      setFeedback({ tone: "error", message: err instanceof ApiError ? err.message : "Couldn't add those students." });
    } finally {
      setSaving(false);
    }
  }

  async function handleRemove(participant) {
    setBusyIds((prev) => new Set(prev).add(participant.id));
    try {
      await Api.del(`/organizations/exams/${examId}/participants/${participant.id}`);
      await load();
    } catch (err) {
      setFeedback({ tone: "error", message: err instanceof ApiError ? err.message : "Couldn't remove that entry." });
    } finally {
      setBusyIds((prev) => {
        const next = new Set(prev);
        next.delete(participant.id);
        return next;
      });
    }
  }

  async function handleClear() {
    setSaving(true);
    setFeedback(null);
    try {
      await Api.del(`/organizations/exams/${examId}/participants`);
      setFeedback({ tone: "success", message: "Reopened to everyone in your organization." });
      await load();
    } catch (err) {
      setFeedback({ tone: "error", message: err instanceof ApiError ? err.message : "Couldn't reopen the exam." });
    } finally {
      setSaving(false);
    }
  }

  const participants = access?.participants || [];

  return (
    <section className="mt-6 rounded-2xl border border-border bg-surface shadow-card p-5">
      <h3 className="font-bold text-ink mb-1">Who can take this exam</h3>
      <p className="text-sm text-muted">
        {participants.length === 0 ? (
          <>Everyone enrolled in <strong className="text-ink">{access?.organization_name || "your organization"}</strong> can take this exam. Add emails below to restrict it.</>
        ) : (
          <>Only the <strong className="text-ink">{participants.length}</strong> listed below can see or start this exam.</>
        )}
      </p>

      {loadError && (
        <div className="mt-4 flex items-start gap-2.5 rounded-xl border border-danger/30 bg-danger/5 px-4 py-3 text-sm text-danger">
          <Icon name="alert" width={16} height={16} className="mt-0.5 shrink-0" />
          <span>{loadError}</span>
        </div>
      )}

      <form onSubmit={handleAdd} className="mt-4">
        <label className={fieldLabelCompact} htmlFor={`exam-emails-${examId}`}>
          Add students to this exam by email
        </label>
        <textarea
          id={`exam-emails-${examId}`}
          value={emails}
          onChange={(e) => setEmails(e.target.value)}
          rows={3}
          placeholder={"anna@college.edu, ben@college.edu\nOr paste a column straight from a spreadsheet"}
          className="w-full rounded-xl border border-border bg-page px-4 py-3 text-sm text-ink placeholder:text-muted/70 focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20 transition"
        />
        <div className="mt-3 flex flex-wrap items-center gap-3">
          <button type="submit" disabled={saving || !emails.trim()}
                  className={`${btnPrimary.replace("px-5 py-3", "px-4 py-2")} text-sm disabled:opacity-60`}>
            {saving ? "Adding…" : "Add to this exam"}
          </button>
          {participants.length > 0 && (
            <button type="button" onClick={handleClear} disabled={saving}
                    className={`${btnGhost.replace("px-5 py-3", "px-4 py-2")} text-sm disabled:opacity-60`}>
              Remove all (open to everyone)
            </button>
          )}
        </div>
        <p className="mt-2 text-xs text-muted">
          Students who haven't registered yet can be added now — they're linked automatically when they sign up.
        </p>
        {feedback && (
          <p className={`mt-3 text-sm ${
            feedback.tone === "error" ? "text-danger"
              : feedback.tone === "warn" ? "text-amber-600 dark:text-amber-400"
              : feedback.tone === "success" ? "text-success" : "text-muted"
          }`}>
            {feedback.message}
          </p>
        )}
      </form>

      {access === null ? (
        <div className="mt-5 flex flex-col gap-2">
          <div className="skeleton skeleton-text" />
          <div className="skeleton skeleton-text" />
        </div>
      ) : participants.length > 0 ? (
        <ul className="mt-5 border-t border-border pt-4 max-h-72 overflow-y-auto flex flex-col divide-y divide-border">
          {participants.map((p) => (
            <li key={p.id} className="flex items-center justify-between gap-3 py-2.5">
              <div className="min-w-0">
                <div className="text-sm font-medium text-ink truncate">{p.student_name || p.email}</div>
                {p.student_name && <div className="text-xs text-muted truncate">{p.email}</div>}
              </div>
              <div className="flex items-center gap-3 shrink-0">
                <span className={`text-[11px] font-semibold px-2 py-0.5 rounded-full ${
                  p.student_id
                    ? "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400"
                    : "bg-amber-500/10 text-amber-600 dark:text-amber-400"
                }`}>
                  {p.student_id ? "Registered" : "Invited"}
                </span>
                <button type="button" onClick={() => handleRemove(p)} disabled={busyIds.has(p.id)}
                        title="Remove from this exam"
                        className="text-muted hover:text-danger transition-colors disabled:opacity-50">
                  <Icon name="x" width={15} height={15} />
                </button>
              </div>
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}

/* ===================== Top level ===================== */


export { AttemptsPanel, ActivePanel, AnalyticsPanel, ExamAccessPanel };


/**
 * Superseded attempts and the resets that produced them.
 *
 * Two APIs existed and nothing called either: GET /attempts/exam/{id}/resets
 * (who reset whose exam, when, and why) and, now, the archived attempts
 * themselves. Before this change the second could not have existed -- a reset
 * DELETED the attempt, so the audit trail pointed at an id that was gone.
 *
 * This is the screen that makes a granted retake reviewable afterwards.
 */
function ResetHistoryPanel({ examId }) {
  const [rows, setRows] = useState([]);
  const [resets, setResets] = useState([]);
  const [state, setState] = useState("loading");
  const [error, setError] = useState("");

  const load = useCallback(() => {
    let cancelled = false;
    setState("loading");
    Promise.all([
      Api.get(`/attempts/exam/${examId}/archived`),
      Api.get(`/attempts/exam/${examId}/resets`),
    ])
      .then(([archived, resetRows]) => {
        if (cancelled) return;
        setRows(archived || []);
        setResets(resetRows || []);
        setState("ready");
      })
      .catch((err) => {
        if (cancelled) return;
        setError(err instanceof ApiError ? err.message : "Couldn't load the reset history.");
        setState("error");
      });
    return () => { cancelled = true; };
  }, [examId]);

  useEffect(() => load(), [load]);

  const reasonFor = new Map(resets.map((r) => [r.previous_attempt_id, r]));

  return (
    <div className="rounded-2xl border border-border bg-surface shadow-card p-5 mt-6">
      <div className="font-semibold text-ink mb-1">Reset History</div>
      <p className="text-xs text-muted mb-4">
        Attempts replaced by a retake. Their answers, results and proctoring evidence are kept, so a
        query about an exam months later can still be answered.
      </p>
      <AsyncSection
        state={state}
        error={error}
        onRetry={load}
        isEmpty={rows.length === 0 && resets.length === 0}
        empty={<p className="py-6 text-muted text-center text-sm">No attempts have been reset on this exam.</p>}
      >
        <div className="overflow-x-auto">
          <table className="min-w-full text-sm">
            <thead>
              <tr className="text-left text-xs text-muted uppercase tracking-wide border-b border-border">
                <th className="py-2 pr-4 font-semibold">Candidate</th>
                <th className="py-2 pr-4 font-semibold">Previous result</th>
                <th className="py-2 pr-4 font-semibold">Reset by</th>
                <th className="py-2 pr-4 font-semibold">Reason</th>
                <th className="py-2 font-semibold">When</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => {
                const reset = reasonFor.get(row.attempt_id);
                return (
                  <tr key={row.attempt_id} className="border-b border-border last:border-0 align-top">
                    <td className="py-2.5 pr-4">
                      <div className="font-medium text-ink">{row.student_name || `Student ${row.student_id}`}</div>
                      <div className="text-xs text-muted">Attempt {row.attempt_id} · {row.status.replace(/_/g, " ")}</div>
                    </td>
                    <td className="py-2.5 pr-4 tabular-nums">
                      {row.scored_marks !== null ? `${row.scored_marks}/${row.total_marks}` : "Not graded"}
                    </td>
                    <td className="py-2.5 pr-4">{reset?.examiner_name || "—"}</td>
                    <td className="py-2.5 pr-4 text-muted">{reset?.reason || "—"}</td>
                    <td className="py-2.5 text-muted whitespace-nowrap">
                      {row.archived_at ? new Date(row.archived_at).toLocaleString() : "—"}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </AsyncSection>
    </div>
  );
}

export { ResetHistoryPanel };
