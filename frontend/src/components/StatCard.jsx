import { Link } from "react-router-dom";

const TONES = { primary: "text-primary", success: "text-success", warning: "text-warning", danger: "text-danger", muted: "text-muted" };

/**
 * One overview number, shared by the admin dashboard's top-level cards and
 * every detail page's read-only summary cards (Examiner Detail, Exam Detail,
 * Candidate Exam Report, Candidate Profile). Renders as a <Link> when `to`
 * is given -- every admin dashboard overview card is clickable per spec --
 * and a plain <div> otherwise, so the same component covers both cases
 * instead of two near-duplicate ones drifting apart over time.
 *
 * `value === null` renders the same loading skeleton the original
 * AdminDashboard.jsx StatCard used, so a card never flashes "0" while its
 * fetch is still in flight.
 */
/**
 * `hint` says what the number actually counts.
 *
 * Several of these labels were ambiguous in ways that matter to somebody making
 * a decision from them: "Total Violations" does not say whether dismissed flags
 * are included, and "Total Exams" on a candidate does not say whether an exam
 * they were assigned but never opened is in there. A number an administrator
 * cannot interpret is worse than no number, because it gets acted on anyway.
 */
export default function StatCard({ label, value, hint, tone = "primary", to, prominent = false }) {
  const Wrapper = to ? Link : "div";
  return (
    <Wrapper
      {...(to ? { to } : {})}
      className={`rounded-2xl border border-border bg-surface shadow-card p-4 transition-all duration-200 hover:-translate-y-0.5 block ${
        to ? "hover:border-primary/40 cursor-pointer" : ""
      } ${prominent ? "sm:p-5 ring-1 ring-primary/15" : ""}`}
    >
      <div className={`font-extrabold tabular-nums ${TONES[tone] || TONES.primary} ${prominent ? "text-3xl" : "text-2xl"}`}>
        {value === null || value === undefined ? (
          <span className="skeleton skeleton-text inline-block" style={{ width: "2ch" }}>-</span>
        ) : (
          value
        )}
      </div>
      <div className={`text-muted mt-1 ${prominent ? "text-sm font-medium" : "text-xs"}`}>{label}</div>
      {hint && (
        <div className="text-[11px] leading-snug text-muted/80 mt-0.5">{hint}</div>
      )}
    </Wrapper>
  );
}
