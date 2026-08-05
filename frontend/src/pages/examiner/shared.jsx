/* Extracted from the 2,080-line ExaminerDashboard.jsx.
 *
 * That single component held every examiner screen -- the exam list, the
 * builder, the schedule form, five panels and the access editor -- and kept the
 * selected exam and the active tab in React state. So refresh reset the whole
 * portal to the exam list, browser Back left the app entirely, and no view had
 * a URL anyone could return to or send to a colleague.
 *
 * The split is by screen, and the screens are now routed, so those three things
 * follow from the structure rather than needing to be handled.
 */
import Icon from "../../components/Icon.jsx";
import { btnGhost } from "../../lib/ui.js";

const CHART_PALETTE = ["#2563eb", "#16a34a", "#d97706", "#dc2626", "#7c3aed", "#0891b2", "#db2777", "#65a30d"];
const EXAM_STATUS_STYLES = {
  draft: "bg-page text-muted border border-border",
  published: "bg-success/10 text-success",
  closed: "bg-muted/10 text-muted",
};
const SEVERITY_STYLES = {
  low: "bg-primary/10 text-primary",
  medium: "bg-warning/10 text-warning",
  high: "bg-danger/10 text-danger",
};

function ExamStatusBadge({ status }) {
  return <span className={`inline-flex items-center rounded-full px-2.5 py-1 text-xs font-semibold capitalize ${EXAM_STATUS_STYLES[status] || "bg-page text-muted border border-border"}`}>{status}</span>;
}

/* ===================== Exams list view ===================== */


export { CHART_PALETTE, EXAM_STATUS_STYLES, SEVERITY_STYLES, ExamStatusBadge };

export const MIN_OPTIONS = 2;
export const emptyOptions = () => [
  { text: "", isCorrect: true },
  { text: "", isCorrect: false },
  { text: "", isCorrect: false },
  { text: "", isCorrect: false },
];
export const emptyTestCases = () => [
  { input: "", expected_output: "", is_sample: true },
  { input: "", expected_output: "", is_sample: false },
];

// Reshape an existing QuestionOut (id/is_correct-keyed, from the API) into
// this form's local editable-row shape (isCorrect). Keeps however many
// options the question actually has -- the count is dynamic, not a fixed 4
// -- only padding up to the MIN_OPTIONS floor for the (shouldn't-happen)
// case of a question with fewer than that already stored.
export function optionsFromExisting(question) {
  const rows = (question.options || []).map((o) => ({ text: o.text, isCorrect: !!o.is_correct }));
  while (rows.length < MIN_OPTIONS) rows.push({ text: "", isCorrect: false });
  return rows;
}
export function testCasesFromExisting(question) {
  const rows = (question.test_cases || []).map((c) => ({
    input: c.input || "", expected_output: c.expected_output || "", is_sample: !!c.is_sample,
  }));
  return rows.length ? rows : emptyTestCases();
}

// Shared by "+ Add Question" (question=null) and each card's "Edit" action
// (question=the existing QuestionOut). Editing pre-fills every field from
// the existing question and PUTs a full replacement on save, instead of
// POSTing a new one.

export function toDatetimeLocalValue(isoString) {
  if (!isoString) return "";
  const d = new Date(isoString);
  if (Number.isNaN(d.getTime())) return "";
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

// Mirrors exam_service.has_exam_started -- a client-side estimate used only
// to decide which fields to show as editable. The server re-checks the same
// rule authoritatively on every PATCH, so a stale/wrong guess here (e.g. the
// device clock is off) fails safely as a rejected save with a clear message,
// never as a silently-accepted change it shouldn't have allowed.
export function hasExamStarted(exam) {
  if (!exam || exam.status !== "published") return false;
  if (!exam.start_time) return true;
  return new Date() >= new Date(exam.start_time);
}


/* ===================== request state =====================
 *
 * Every panel used a single `null` for both "still loading" and "the request
 * failed", and rendered EmptyState for both. A failed violations fetch was
 * therefore indistinguishable from a clean sitting -- an examiner would read
 * "No violations recorded" and reasonably conclude nothing had happened, when
 * in fact nothing had been asked. These three make the difference visible, and
 * the error one offers a way out rather than a dead end.
 */

export function LoadingRows({ rows = 3 }) {
  return (
    <div className="space-y-2 animate-pulse" aria-hidden="true">
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="h-11 rounded-lg bg-page border border-border" />
      ))}
    </div>
  );
}

export function ErrorState({ message, onRetry }) {
  return (
    <div role="alert" className="rounded-xl border border-danger/30 bg-danger/5 px-4 py-4 text-sm">
      <div className="flex items-start gap-2.5">
        <span className="text-danger mt-0.5 shrink-0"><Icon name="alert" width={16} height={16} /></span>
        <div className="flex-1">
          <p className="text-ink font-semibold mb-0.5">Couldn't load this.</p>
          <p className="text-muted">{message || "Something went wrong."}</p>
          {onRetry && (
            <button onClick={onRetry} className={`${btnGhost} mt-3 px-3 py-1.5 text-xs`}>
              Try again
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

/** Resolves a request into exactly one of loading / error / empty / content. */
export function AsyncSection({ state, error, onRetry, isEmpty, empty, children, rows = 3 }) {
  if (state === "loading") return <LoadingRows rows={rows} />;
  if (state === "error") return <ErrorState message={error} onRetry={onRetry} />;
  if (isEmpty) return empty;
  return children;
}

/**
 * Page controls for a SERVER-paginated list.
 *
 * The existing components/Pagination.jsx slices an array the browser already
 * holds, which was the problem rather than the solution: the whole list had to
 * arrive before page 1 could be drawn. This one only reports which page to ask
 * for; the rows never all exist on the client at once.
 */
export function PageControls({ page, totalPages, total, onChange, noun = "row" }) {
  if (total === 0) return null;
  return (
    <div className="flex flex-wrap items-center justify-between gap-3 mt-4 pt-3 border-t border-border">
      <span className="text-xs text-muted">
        {total.toLocaleString()} {noun}{total === 1 ? "" : "s"}
        {totalPages > 1 ? ` · page ${page} of ${totalPages}` : ""}
      </span>
      {totalPages > 1 && (
        <div className="flex items-center gap-1.5">
          <button
            type="button"
            onClick={() => onChange(page - 1)}
            disabled={page <= 1}
            className={`${btnGhost} px-3 py-1.5 text-xs disabled:opacity-40 disabled:cursor-not-allowed`}
          >
            Previous
          </button>
          <button
            type="button"
            onClick={() => onChange(page + 1)}
            disabled={page >= totalPages}
            className={`${btnGhost} px-3 py-1.5 text-xs disabled:opacity-40 disabled:cursor-not-allowed`}
          >
            Next
          </button>
        </div>
      )}
    </div>
  );
}
