import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import RedirectToLogin from "../../components/RedirectToLogin.jsx";
import DashboardHeader from "../../components/DashboardHeader.jsx";
import Icon from "../../components/Icon.jsx";
import Breadcrumbs from "../../components/Breadcrumbs.jsx";
import StatCard from "../../components/StatCard.jsx";
import IdentityPhotoModal, { usePhotoBlob } from "../../components/IdentityPhotoModal.jsx";
import { Api, ApiError } from "../../lib/api.js";
import { btnPrimary, fieldInput } from "../../lib/ui.js";
import { badgeClass, eventTypeLabel, fmtDateTime, fmtPercent, fmtSeconds, proctoringCardCounts, toneFor } from "../../lib/adminUi.js";
import { isLoggedIn, getRole } from "../../lib/auth.js";

const BROWSER_EVENT_TYPES = new Set([
  "tab_switch", "fullscreen_exit", "right_click_attempt", "copy_paste_attempt", "screenshot_attempt", "screen_share_stopped",
]);

// "dismissed" is the stored value (AdminDecision.DISMISSED, unchanged on the
// backend) but "Misleading" is what the judgement actually means here: the
// flag was looked at and was not genuine misconduct.
const DECISIONS = [
  { value: "pending", label: "Pending" },
  { value: "confirmed", label: "Confirmed" },
  { value: "dismissed", label: "Misleading" },
];

function EvidenceModal({ eventId, onClose }) {
  const { loading, url, error } = usePhotoBlob(eventId ? `/proctoring/events/${eventId}/screenshot` : "", !!eventId);
  if (!eventId) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4 animate-fade-in" onClick={onClose} role="dialog" aria-modal="true">
      <div className="w-full max-w-lg rounded-2xl border border-border bg-surface shadow-card p-5" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between mb-4">
          <h3 className="font-bold text-ink">Captured evidence</h3>
          <button type="button" onClick={onClose} aria-label="Close" className="text-muted hover:text-ink transition-colors">
            <Icon name="x" width={18} height={18} />
          </button>
        </div>
        <div className="aspect-video rounded-xl border border-border bg-page flex items-center justify-center overflow-hidden">
          {loading ? (
            <span className="skeleton w-full h-full block" />
          ) : url ? (
            <img src={url} alt="Violation evidence" className="w-full h-full object-contain" />
          ) : (
            <span className="text-xs text-muted px-3 text-center">{error || "Not available."}</span>
          )}
        </div>
      </div>
    </div>
  );
}

/**
 * The examiner's per-student attempt review: every violation this candidate
 * triggered, with proof images and the risk score, plus the ability to mark
 * each one pending/confirmed/misleading.
 *
 * Deliberately the ONLY place an examiner can see violations. There used to
 * also be a "Violations" tab on the exam builder listing every flag across
 * the whole exam at once -- that's gone (see AttemptsPanel's "Review"
 * button, which is how you get here) because reviewing a candidate's
 * conduct is inherently a per-candidate judgement, and a flat cross-exam
 * list encouraged deciding on rows out of context.
 *
 * Reuses the exact endpoint the admin's Candidate Exam Report is built on
 * (GET /attempts/{id}/staff-report is role-aware: admin or the exam's own
 * owning examiner) -- nothing here needed a new backend shape, only a
 * frontend page and an examiner-facing route to reach it from.
 */
export default function ExaminerAttemptReport() {
  const { attemptId } = useParams();
  const [report, setReport] = useState(null);
  const [loadError, setLoadError] = useState("");
  const [viewingEvidenceId, setViewingEvidenceId] = useState(null);
  const [viewingIdentity, setViewingIdentity] = useState(false);

  const [comment, setComment] = useState("");
  const [savingComment, setSavingComment] = useState(false);
  const [commentSaved, setCommentSaved] = useState(false);

  const [downloading, setDownloading] = useState(false);

  async function load() {
    try {
      const data = await Api.get(`/attempts/${attemptId}/staff-report`);
      setReport(data);
      setComment(data.examiner_comment || "");
    } catch (err) {
      setLoadError(err instanceof ApiError ? err.message : "Couldn't load this report.");
    }
  }

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [attemptId]);

  if (!isLoggedIn() || getRole() !== "examiner") return <RedirectToLogin />;

  async function handleDecisionChange(eventId, decision) {
    const previous = report.violation_timeline;
    setReport((r) => ({
      ...r,
      violation_timeline: r.violation_timeline.map((v) => (v.id === eventId ? { ...v, admin_decision: decision } : v)),
    }));
    try {
      await Api.patch(`/proctoring/events/${eventId}/decision`, { decision });
    } catch (err) {
      setReport((r) => ({ ...r, violation_timeline: previous }));
      setLoadError(err instanceof ApiError ? err.message : "Couldn't update that decision.");
    }
  }

  async function handleSaveComment() {
    setSavingComment(true);
    setCommentSaved(false);
    try {
      const res = await Api.patch(`/attempts/${attemptId}/comment`, { comment });
      setReport((r) => ({ ...r, examiner_comment: res.examiner_comment }));
      setCommentSaved(true);
    } catch (err) {
      setLoadError(err instanceof ApiError ? err.message : "Couldn't save that comment.");
    } finally {
      setSavingComment(false);
    }
  }

  async function handleDownload() {
    setDownloading(true);
    try {
      await Api.downloadFile(`/attempts/${attemptId}/result/pdf`, `result_attempt_${attemptId}.pdf`);
    } catch (err) {
      setLoadError(err instanceof ApiError ? err.message : "Couldn't download the report.");
    } finally {
      setDownloading(false);
    }
  }

  if (loadError && !report) {
    return (
      <div className="min-h-screen bg-page text-ink">
        <DashboardHeader title="Candidate Exam Report" />
        <main className="max-w-5xl mx-auto w-full p-4 sm:p-6">
          <div className="flex items-start gap-2.5 rounded-xl border border-danger/30 bg-danger/5 px-4 py-3 text-sm text-danger">
            <Icon name="alert" width={16} height={16} className="mt-0.5 shrink-0" />
            <span>{loadError}</span>
          </div>
        </main>
      </div>
    );
  }

  if (!report) {
    return (
      <div className="min-h-screen bg-page text-ink">
        <DashboardHeader title="Candidate Exam Report" />
        <main className="max-w-5xl mx-auto w-full p-4 sm:p-6">
          <div className="skeleton skeleton-card h-96" />
        </main>
      </div>
    );
  }

  const cards = proctoringCardCounts(report.violation_type_counts);
  const attempted = report.correct_count + report.incorrect_count;
  const browserEvents = report.violation_timeline.filter((v) => BROWSER_EVENT_TYPES.has(v.event_type));
  const evidenceEvents = report.violation_timeline.filter((v) => v.has_screenshot);

  return (
    <div className="min-h-screen bg-page text-ink">
      <DashboardHeader title="Candidate Exam Report" />
      <main className="max-w-5xl mx-auto w-full p-4 sm:p-6 pb-16">
        <Breadcrumbs
          trail={[
            { label: "Dashboard", to: "/examiner" },
            { label: report.exam_title, to: `/examiner/exams/${report.exam_id}?tab=attempts` },
            { label: report.candidate_name },
          ]}
        />

        {loadError && (
          <div className="mb-4 flex items-start gap-2.5 rounded-xl border border-danger/30 bg-danger/5 px-4 py-3 text-sm text-danger">
            <Icon name="alert" width={16} height={16} className="mt-0.5 shrink-0" />
            <span>{loadError}</span>
          </div>
        )}

        <div className="flex flex-wrap items-start justify-between gap-3 mb-6">
          <div>
            <h1 className="text-xl font-extrabold tracking-tight">{report.candidate_name}</h1>
            <p className="text-sm text-muted mt-0.5">{report.exam_title}</p>
          </div>
          <button type="button" disabled={downloading} onClick={handleDownload}
                  className={`${btnPrimary.replace("px-5 py-3", "px-4 py-2")} text-xs ${downloading ? "opacity-70 pointer-events-none" : ""}`}>
            {downloading && <Icon name="spinner" width={14} height={14} className="animate-spin" />}
            {downloading ? "Preparing…" : "Download Exam Report"}
          </button>
        </div>

        {/* Candidate information */}
        <div className="rounded-2xl border border-border bg-surface shadow-card p-5 mb-6">
          <h2 className="text-sm font-bold uppercase tracking-wide text-muted mb-3">Candidate Information</h2>
          <dl className="grid grid-cols-2 sm:grid-cols-3 gap-x-6 gap-y-3 text-sm mb-4">
            <div><dt className="text-xs text-muted">Candidate ID</dt><dd className="font-medium">{report.roll_number || "-"}</dd></div>
            <div><dt className="text-xs text-muted">Email</dt><dd className="font-medium break-all">{report.candidate_email}</dd></div>
          </dl>
          <div className="flex flex-wrap items-center gap-2">
            <span className={badgeClass(report.face_registered ? "success" : "muted")}>Face: {report.face_registered ? "Registered" : "Not registered"}</span>
            <span className={badgeClass(report.id_verified ? "success" : "warning")}>ID Card: {report.id_verified ? "Verified" : "Pending"}</span>
            <span className={badgeClass(report.identity_locked ? "success" : "muted")}>Identity: {report.identity_locked ? "Locked" : "Unlocked"}</span>
            <button type="button" onClick={() => setViewingIdentity(true)} className="text-xs font-semibold text-primary hover:underline ml-1">
              View photo &amp; ID
            </button>
          </div>
        </div>

        {/* Exam attempt details */}
        <div className="rounded-2xl border border-border bg-surface shadow-card p-5 mb-6">
          <h2 className="text-sm font-bold uppercase tracking-wide text-muted mb-3">Exam Attempt Details</h2>
          <dl className="grid grid-cols-2 sm:grid-cols-4 gap-x-6 gap-y-3 text-sm">
            <div><dt className="text-xs text-muted">Started</dt><dd className="font-medium">{fmtDateTime(report.started_at)}</dd></div>
            <div><dt className="text-xs text-muted">Submitted</dt><dd className="font-medium">{fmtDateTime(report.submitted_at)}</dd></div>
            <div><dt className="text-xs text-muted">Time Spent</dt><dd className="font-medium">{fmtSeconds(report.time_taken_seconds)}</dd></div>
            <div><dt className="text-xs text-muted">Status</dt><dd><span className={badgeClass(toneFor(report.status.replace("_", " ")))}>{report.status.replace("_", " ")}</span></dd></div>
            <div><dt className="text-xs text-muted">Questions Attempted</dt><dd className="font-medium">{attempted}</dd></div>
            <div><dt className="text-xs text-muted">Unanswered</dt><dd className="font-medium">{report.unattempted_count}</dd></div>
            <div><dt className="text-xs text-muted">Marks Obtained</dt><dd className="font-medium">{report.scored_marks} / {report.total_marks}</dd></div>
            <div><dt className="text-xs text-muted">Percentage</dt><dd className="font-medium">{fmtPercent(report.percentage)}</dd></div>
          </dl>
          {report.passed !== null && (
            <div className="mt-3">
              <span className={badgeClass(report.passed ? "success" : "danger")}>{report.passed ? "Passed" : "Failed"}</span>
            </div>
          )}
          {report.results_released === false && (
            <p className="mt-3 text-xs text-muted">
              This candidate cannot see these results yet -- results for this exam are held back until you release them.
            </p>
          )}
        </div>

        {/* Proctoring data */}
        <h2 className="text-sm font-bold uppercase tracking-wide text-muted mb-3">Proctoring Data</h2>
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mb-3">
          <StatCard label="Risk Score" value={report.risk_score} tone={toneFor(report.risk_tier)} />
          <StatCard label="Total Violations" value={report.total_violations} tone="danger" />
          <StatCard label="Tab Switches" value={cards.tabSwitches} tone="warning" />
          <StatCard label="Fullscreen Exits" value={cards.fullscreenExits} tone="warning" />
        </div>
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mb-6">
          <StatCard label="Face Mismatches" value={cards.faceMismatches} tone="danger" />
          <StatCard label="Multiple-Person Detections" value={cards.multiplePersons} tone="danger" />
          <StatCard label="Phone Detections" value={cards.phoneDetections} tone="danger" />
          <StatCard label="Audio Alerts" value={cards.audioAlerts} tone="warning" />
        </div>

        {/* Automated proctoring summary */}
        <div className="rounded-2xl border border-primary/20 bg-primary/5 p-5 mb-6">
          <h2 className="text-sm font-bold uppercase tracking-wide text-primary mb-2">Automated Proctoring Summary</h2>
          <p className="text-sm text-ink leading-relaxed">{report.proctoring_summary}</p>
          <p className="text-xs text-muted mt-2">
            Generated from the recorded events by a fixed rule, not by a model. It summarises what was
            flagged; whether any of it was misconduct is a judgement for you to make below.
          </p>
        </div>

        {/* Violation timeline -- every violation this candidate triggered,
            with evidence and a decision you can set. This is the whole point
            of this page: there is no other place in the examiner portal that
            lists violations. */}
        <h2 className="text-sm font-bold uppercase tracking-wide text-muted mb-3">Violation Timeline</h2>
        {report.violation_timeline.length === 0 ? (
          <div className="rounded-2xl border border-dashed border-border bg-page/60 px-6 py-8 text-center text-sm text-muted mb-6">
            No proctoring violations were recorded during this attempt.
          </div>
        ) : (
          <div className="rounded-2xl border border-border bg-surface shadow-card overflow-x-auto mb-6">
            <table className="min-w-full text-sm">
              <thead>
                <tr className="text-left text-xs text-muted uppercase tracking-wide border-b border-border">
                  <th className="px-4 py-3 font-semibold">Time</th>
                  <th className="px-4 py-3 font-semibold">Violation</th>
                  <th className="px-4 py-3 font-semibold">Severity</th>
                  <th className="px-4 py-3 font-semibold">Evidence</th>
                  <th className="px-4 py-3 font-semibold">Decision</th>
                </tr>
              </thead>
              <tbody>
                {report.violation_timeline.map((v) => (
                  <tr key={v.id} className="border-b border-border last:border-0 hover:bg-page/60 transition-colors">
                    <td className="px-4 py-3 text-muted whitespace-nowrap">{fmtDateTime(v.created_at)}</td>
                    <td className="px-4 py-3 font-medium">{eventTypeLabel(v.event_type)}</td>
                    <td className="px-4 py-3"><span className={badgeClass(toneFor(v.severity))}>{v.severity}</span></td>
                    <td className="px-4 py-3">
                      {v.has_screenshot ? (
                        <button type="button" onClick={() => setViewingEvidenceId(v.id)} className="text-xs font-semibold text-primary hover:underline">
                          View screenshot
                        </button>
                      ) : (
                        <span className="text-xs text-muted">—</span>
                      )}
                    </td>
                    <td className="px-4 py-3">
                      <select
                        value={v.admin_decision}
                        onChange={(e) => handleDecisionChange(v.id, e.target.value)}
                        className="rounded-lg border border-border bg-input text-ink text-xs font-semibold px-2 py-1.5 outline-none focus:border-primary"
                      >
                        {DECISIONS.map((d) => (
                          <option key={d.value} value={d.value}>{d.label}</option>
                        ))}
                      </select>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {/* Browser activity + captured evidence */}
        <div className="grid md:grid-cols-2 gap-4 mb-6">
          <div className="rounded-2xl border border-border bg-surface shadow-card p-4">
            <h3 className="text-sm font-bold mb-3">Browser Activity</h3>
            {browserEvents.length === 0 ? (
              <p className="text-xs text-muted">No tab switches, fullscreen exits, or blocked shortcuts were recorded.</p>
            ) : (
              <ul className="flex flex-col gap-2 text-sm">
                {browserEvents.map((v) => (
                  <li key={v.id} className="flex items-center justify-between gap-2 text-xs">
                    <span>{eventTypeLabel(v.event_type)}</span>
                    <span className="text-muted whitespace-nowrap">{fmtDateTime(v.created_at)}</span>
                  </li>
                ))}
              </ul>
            )}
          </div>
          <div className="rounded-2xl border border-border bg-surface shadow-card p-4">
            <h3 className="text-sm font-bold mb-1">Webcam Evidence</h3>
            <p className="text-xs text-muted mb-3">Frames captured from the candidate's webcam at the moment of a violation — this platform does not separately record the screen.</p>
            {evidenceEvents.length === 0 ? (
              <p className="text-xs text-muted">No screenshots were captured during this attempt.</p>
            ) : (
              <div className="flex flex-wrap gap-2">
                {evidenceEvents.map((v) => (
                  <button key={v.id} type="button" onClick={() => setViewingEvidenceId(v.id)}
                          className="text-xs font-semibold text-primary hover:underline rounded-lg border border-border px-3 py-1.5">
                    {eventTypeLabel(v.event_type)}
                  </button>
                ))}
              </div>
            )}
          </div>
        </div>

        {/* Comments */}
        <div className="grid md:grid-cols-2 gap-4 mb-6">
          <div className="rounded-2xl border border-border bg-surface shadow-card p-4">
            <h3 className="text-sm font-bold mb-2">Your Comment</h3>
            <textarea
              value={comment}
              onChange={(e) => { setComment(e.target.value); setCommentSaved(false); }}
              rows={3}
              maxLength={2000}
              placeholder="Add a note about this candidate's attempt…"
              className={`${fieldInput} resize-none`}
            />
            <div className="flex items-center gap-3 mt-2">
              <button type="button" disabled={savingComment} onClick={handleSaveComment}
                      className={`${btnPrimary.replace("px-5 py-3", "px-4 py-2")} text-xs ${savingComment ? "opacity-70 pointer-events-none" : ""}`}>
                {savingComment ? "Saving…" : "Save comment"}
              </button>
              {commentSaved && <span className="text-xs text-success font-semibold">Saved.</span>}
            </div>
          </div>
          <div className="rounded-2xl border border-border bg-surface shadow-card p-4">
            <h3 className="text-sm font-bold mb-2">Admin Comment</h3>
            {report.admin_comment ? (
              <p className="text-sm text-ink whitespace-pre-wrap leading-relaxed">{report.admin_comment}</p>
            ) : (
              <p className="text-xs text-muted">No admin note on this attempt.</p>
            )}
          </div>
        </div>
      </main>

      <EvidenceModal eventId={viewingEvidenceId} onClose={() => setViewingEvidenceId(null)} />
      {viewingIdentity && (
        <IdentityPhotoModal studentId={report.candidate_id} studentName={report.candidate_name} onClose={() => setViewingIdentity(false)} />
      )}
    </div>
  );
}
