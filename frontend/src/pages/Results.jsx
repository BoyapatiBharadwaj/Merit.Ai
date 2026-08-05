import { useEffect, useState } from "react";
import { useParams, useNavigate, useLocation } from "react-router-dom";
import RedirectToLogin from "../components/RedirectToLogin.jsx";
import DashboardHeader from "../components/DashboardHeader.jsx";
import Icon from "../components/Icon.jsx";
import { btnPrimary, btnGhost } from "../lib/ui.js";
import { Api, ApiError } from "../lib/api.js";
import { isLoggedIn } from "../lib/auth.js";

const OUTCOME_META = {
  correct: { label: "Correct", cls: "bg-success/10 text-success", icon: "check" },
  incorrect: { label: "Incorrect", cls: "bg-danger/10 text-danger", icon: "x" },
  unattempted: { label: "Unanswered", cls: "bg-page text-muted border border-border", icon: "clock" },
};

function formatDuration(totalSeconds) {
  if (totalSeconds === null || totalSeconds === undefined) return "-";
  const s = Math.max(0, Math.round(totalSeconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  if (h > 0) return `${h}h ${m}m ${sec}s`;
  if (m > 0) return `${m}m ${sec}s`;
  return `${sec}s`;
}

/** Comprehensive post-exam report -- lands here right after submit, and is
 * also a shareable direct link (GET /attempts/{id}/report is role-aware
 * server side: the owning student, the exam's examiner, or an admin can view
 * it). Pulls every field the report needs from one endpoint
 * (attempt_service.build_full_report) rather than assembling it from
 * several. */
export default function Results() {
  const { attemptId } = useParams();
  const navigate = useNavigate();
  const location = useLocation();

  const [report, setReport] = useState(null);
  const [error, setError] = useState("");
  const [downloading, setDownloading] = useState(false);
  const [downloadError, setDownloadError] = useState("");

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await Api.get(`/attempts/${attemptId}/report`);
        if (!cancelled) setReport(res);
      } catch (err) {
        if (!cancelled) setError(err instanceof ApiError ? err.message : "Couldn't load this report.");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [attemptId]);

  if (!isLoggedIn()) return <RedirectToLogin />;

  async function handleDownloadReport() {
    setDownloadError("");
    setDownloading(true);
    try {
      await Api.downloadFile(`/attempts/${attemptId}/result/pdf`, `result_attempt_${attemptId}.pdf`);
    } catch (err) {
      setDownloadError(err instanceof ApiError ? err.message : "Download failed. Please try again.");
    } finally {
      setDownloading(false);
    }
  }

  const pct = report ? Math.round(report.percentage * 10) / 10 : null;
  const passed = report ? report.passed : null;
  /**
   * Was this attempt auto-submitted?
   *
   * Read from the REPORT, with router state only as a hint. It used to come
   * from router state alone, which meant the notice appeared exactly once --
   * on the navigation straight out of the exam -- and vanished on reload, on a
   * bookmark, and for anyone opening the report later. Whether the clock ran
   * out is a permanent fact about the attempt, and the server records it as
   * such (AttemptStatus.AUTO_SUBMITTED); reading it from the thing that knows
   * makes the notice survive a refresh.
   */
  const autoSubmitted = report?.status
    ? report.status === "auto_submitted"
    : Boolean(location.state?.autoSubmitted);

  return (
    <div className="min-h-screen flex flex-col bg-page text-ink">
      <DashboardHeader title="Report" />
      <main className="flex-1 max-w-4xl mx-auto w-full px-5 sm:px-6 py-10">
        {error && (
          <div className="rounded-2xl border border-danger/30 bg-danger/5 p-8 text-center animate-fade-in">
            <span className="inline-flex items-center justify-center w-14 h-14 rounded-2xl bg-danger/10 text-danger mb-4">
              <Icon name="alert" width={22} height={22} />
            </span>
            <p className="text-sm text-danger font-medium mb-6">{error}</p>
            <button onClick={() => navigate("/dashboard")} className={btnGhost}>
              Back to Dashboard
            </button>
          </div>
        )}

        {!error && !report && (
          <div className="rounded-2xl border border-border bg-surface shadow-card p-8">
            <div className="skeleton skeleton-card h-52" />
          </div>
        )}

        {!error && report && (
          <div className="animate-fade-in flex flex-col gap-6">
            {autoSubmitted && (
              <div className="flex items-start gap-2.5 rounded-xl border border-warning/30 bg-warning/10 px-4 py-3 text-sm text-warning font-medium">
                <Icon name="clock" width={16} height={16} className="mt-0.5 shrink-0" />
                <span>
                  Your exam was submitted automatically when the time ran out. Everything you had
                  answered was saved — nothing was lost, and this does not count against you.
                </span>
              </div>
            )}

            {/* ---------- summary ---------- */}
            <div className="rounded-2xl border border-border bg-surface shadow-card p-8 text-center">
              <span
                className={`inline-flex items-center justify-center w-16 h-16 rounded-2xl mb-4 ${
                  passed ? "bg-success/10 text-success" : "bg-danger/10 text-danger"
                }`}
              >
                <Icon name={passed ? "shield-check" : "alert"} width={26} height={26} />
              </span>
              <h1 className="text-xl font-extrabold tracking-tight mb-0.5">{report.exam_title}</h1>
              <p className="text-sm text-muted mb-6">{report.candidate_name}{report.roll_number ? ` · Roll No. ${report.roll_number}` : ""}</p>
              <div className={`text-5xl font-extrabold tabular-nums mb-2 ${passed ? "text-success" : "text-ink"}`}>{pct}%</div>
              <p className="text-sm text-muted mb-3">
                {report.scored_marks} / {report.total_marks} marks · Pass mark {report.pass_percentage}%
              </p>
              <span className={`inline-flex items-center rounded-full px-3 py-1 text-xs font-bold uppercase tracking-wide ${passed ? "bg-success/10 text-success" : "bg-danger/10 text-danger"}`}>
                {passed ? "Passed" : "Failed"}
              </span>
            </div>

            {/* ---------- key facts ---------- */}
            <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
              <FactBlock label="Correct" value={report.correct_count} tone="success" />
              <FactBlock label="Incorrect" value={report.incorrect_count} tone="danger" />
              <FactBlock label="Unanswered" value={report.unattempted_count} tone="muted" />
              <FactBlock label="Duration" value={`${report.duration_minutes} min`} tone="muted" />
              <FactBlock label="Time Taken" value={formatDuration(report.time_taken_seconds)} tone="muted" />
              <FactBlock label="Submitted" value={report.submitted_at ? new Date(report.submitted_at).toLocaleString() : "-"} tone="muted" small />
            </div>

            {downloadError && (
              <div className="rounded-xl border border-danger/30 bg-danger/5 text-danger text-sm font-medium px-4 py-3">{downloadError}</div>
            )}

            <div className="flex flex-wrap items-center gap-3">
              <button onClick={handleDownloadReport} disabled={downloading} className={`${btnGhost} disabled:opacity-60`}>
                <Icon name="doc" width={16} height={16} />
                {downloading ? "Preparing…" : "Download PDF Report"}
              </button>
              <button onClick={() => navigate("/dashboard")} className={btnPrimary}>
                Back to Dashboard
              </button>
            </div>

            {/* ---------- per-question breakdown ---------- */}
            <div>
              <h2 className="text-xs font-bold uppercase tracking-wider text-muted mb-3">Question-by-question review</h2>
              <div className="flex flex-col gap-4">
                {report.questions.map((q, index) => (
                  <QuestionReviewCard key={q.question_id} question={q} index={index} />
                ))}
              </div>
            </div>
          </div>
        )}
      </main>
    </div>
  );
}

function FactBlock({ label, value, tone, small }) {
  const tones = { success: "text-success", danger: "text-danger", muted: "text-ink" };
  return (
    <div className="rounded-2xl border border-border bg-surface shadow-card p-4 text-center transition-all duration-200 hover:-translate-y-0.5 hover:border-primary/30">
      <div className={`font-extrabold tabular-nums ${tones[tone]} ${small ? "text-sm" : "text-2xl"}`}>{value}</div>
      <div className="text-xs text-muted mt-1">{label}</div>
    </div>
  );
}

function QuestionReviewCard({ question, index }) {
  const outcome = OUTCOME_META[question.outcome] || OUTCOME_META.unattempted;
  const isCoding = question.question_type === "coding";
  const isMultiSelect = question.question_type === "multi_select";

  return (
    <div className="rounded-2xl border border-border bg-surface shadow-card p-5">
      <div className="flex items-start justify-between gap-3 mb-3">
        <div className="text-sm font-semibold text-ink">
          <span className="text-muted mr-1.5">Q{index + 1}.</span>
          {question.text}
        </div>
        <span className={`shrink-0 inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-xs font-semibold ${outcome.cls}`}>
          <Icon name={outcome.icon} width={12} height={12} />
          {outcome.label}
        </span>
      </div>

      {isCoding ? (
        <div className="mb-3">
          <div className="text-[11px] font-semibold text-muted uppercase tracking-wide mb-1">Your submission</div>
          <pre className="rounded-lg bg-page border border-border p-3 text-xs font-mono overflow-x-auto whitespace-pre-wrap">
            {question.selected_answer || "(no code submitted)"}
          </pre>
          {question.code_test_results?.results && (
            <div className="mt-2 flex flex-wrap gap-1.5">
              {question.code_test_results.results.map((tc, i) => (
                <span
                  key={i}
                  className={`inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-semibold ${tc.passed ? "bg-success/10 text-success" : "bg-danger/10 text-danger"}`}
                >
                  Test {i + 1}: {tc.passed ? "Passed" : "Failed"}
                </span>
              ))}
            </div>
          )}
        </div>
      ) : (
        question.options.length > 0 && (
          <div className="flex flex-col gap-1.5 mb-3">
            {isMultiSelect && (
              <p className="text-[11px] text-muted mb-0.5">Select-all-that-apply -- every correct option must be chosen for the marks.</p>
            )}
            {question.options.map((opt) => {
              const isSelected = isMultiSelect
                ? (question.selected_option_ids || []).includes(opt.id)
                : opt.id === question.selected_option_id;
              const isCorrect = isMultiSelect
                ? (question.correct_option_ids || []).includes(opt.id)
                : opt.text === question.correct_answer;
              return (
                <div
                  key={opt.id}
                  className={`flex items-center gap-2 rounded-lg border px-3 py-2 text-sm ${
                    isCorrect
                      ? "border-success/40 bg-success/5 text-ink"
                      : isSelected
                        ? "border-danger/40 bg-danger/5 text-ink"
                        : "border-border text-muted"
                  }`}
                >
                  <span className="flex-1">{opt.text}</span>
                  {isCorrect && <span className="text-[11px] font-semibold text-success shrink-0">Correct answer</span>}
                  {isSelected && !isCorrect && <span className="text-[11px] font-semibold text-danger shrink-0">Your answer</span>}
                  {isSelected && isCorrect && <span className="text-[11px] font-semibold text-success shrink-0">Your answer</span>}
                </div>
              );
            })}
          </div>
        )
      )}

      <div className="flex items-center justify-between gap-3 text-xs text-muted pt-2 border-t border-border">
        <span>
          Marks: <strong className="text-ink">{question.marks_awarded}</strong> / {question.marks}
        </span>
      </div>

      {question.explanation && (
        <div className="mt-3 rounded-lg bg-page/60 border border-border px-3 py-2.5 text-xs text-muted leading-relaxed">
          <span className="font-semibold text-ink">Explanation: </span>
          {question.explanation}
        </div>
      )}
    </div>
  );
}
