import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import RedirectToLogin from "../components/RedirectToLogin.jsx";
import DashboardHeader from "../components/DashboardHeader.jsx";
import Breadcrumbs from "../components/Breadcrumbs.jsx";
import EmptyState from "../components/EmptyState.jsx";
import Icon from "../components/Icon.jsx";
import Pagination from "../components/Pagination.jsx";
import { usePhotoBlob } from "../components/IdentityPhotoModal.jsx";
import { Api, ApiError } from "../lib/api.js";
import { btnPrimary, btnGhost } from "../lib/ui.js";
import { badgeClass, eventTypeLabel, fmtDateTime } from "../lib/adminUi.js";
import { isLoggedIn, getRole } from "../lib/auth.js";

const PAGE_SIZE = 10;

/**
 * The reviewer's worklist.
 */
function Evidence({ eventId }) {
  const { loading, url, error } = usePhotoBlob(
    eventId ? `/proctoring/events/${eventId}/screenshot` : "", Boolean(eventId),
  );
  if (loading) return <div className="h-48 rounded-lg bg-border/40 animate-pulse" />;
  if (error) return <p className="text-sm text-danger">{error}</p>;
  if (!url) return null;
  return <img src={url} alt="Captured at the moment of the violation"
              className="max-h-64 rounded-lg border border-border" />;
}

export default function AdminReviewQueue() {
  const [rows, setRows] = useState([]);
  const [pageInfo, setPageInfo] = useState({ total: 0, total_pages: 1 });
  const [page, setPage] = useState(1);
  const [state, setState] = useState("loading");
  const [error, setError] = useState("");
  const [deciding, setDeciding] = useState(null); // event id being saved
  const [rowError, setRowError] = useState(null); // { id, message }

  const load = useCallback(() => {
    let cancelled = false;
    setState("loading");
    Api.get(`/admin/review-queue?page=${page}&page_size=${PAGE_SIZE}`)
      .then((data) => {
        if (cancelled) return;
        setRows(data.items || []);
        setPageInfo({ total: data.total, total_pages: Math.max(1, data.total_pages) });
        setState("ready");
      })
      .catch((err) => {
        if (cancelled) return;
        // Not an empty list: "nothing to review" and "we could not ask" must not look the same
        // on the screen whose whole job is showing what is outstanding.
        setError(err instanceof ApiError ? err.message : "Couldn't load the review queue.");
        setState("error");
      });
    return () => { cancelled = true; };
  }, [page]);

  useEffect(() => load(), [load]);

  async function decide(eventId, decision) {
    setDeciding(eventId);
    setRowError(null);
    try {
      await Api.patch(`/proctoring/events/${eventId}/decision`, { decision });
      // Remove just this row rather than reloading: the reviewer is working
      // down a list, and a full reload would move everything under them.
      setRows((prev) => prev.filter((row) => row.id !== eventId));
      setPageInfo((prev) => ({ ...prev, total: Math.max(0, prev.total - 1) }));
    } catch (err) {
      setRowError({ id: eventId, message: err instanceof ApiError ? err.message : "Couldn't save that decision." });
    } finally {
      setDeciding(null);
    }
  }

  if (!isLoggedIn() || getRole() !== "admin") return <RedirectToLogin />;

  return (
    <div className="min-h-screen bg-page text-ink">
      <DashboardHeader title="Admin" />
      <main className="max-w-5xl mx-auto w-full p-4 sm:p-6">
        <Breadcrumbs items={[{ label: "Admin", to: "/dashboard" }, { label: "Review Queue" }]} />

        <div className="flex flex-wrap items-end justify-between gap-3 mt-4 mb-5">
          <div>
            <h1 className="text-2xl font-extrabold tracking-tight">Review Queue</h1>
            <p className="text-sm text-muted mt-1">
              Flags waiting on a decision, most serious and longest-waiting first. Until one is
              decided it counts toward the candidate's risk score.
            </p>
          </div>
          <span className="inline-flex items-center rounded-full px-3 py-1.5 text-sm font-semibold bg-warning/10 text-warning">
            {pageInfo.total} awaiting review
          </span>
        </div>

        {state === "loading" && (
          <div className="space-y-3" aria-hidden="true">
            {[0, 1, 2].map((i) => <div key={i} className="h-32 rounded-2xl border border-border bg-surface animate-pulse" />)}
          </div>
        )}

        {state === "error" && (
          <div role="alert" className="rounded-2xl border border-danger/30 bg-danger/5 p-5">
            <p className="font-semibold text-ink mb-1">Couldn't load the review queue.</p>
            <p className="text-sm text-muted mb-3">{error}</p>
            <button onClick={load} className={`${btnGhost} px-4 py-2 text-sm`}>Try again</button>
          </div>
        )}

        {state === "ready" && rows.length === 0 && (
          <EmptyState icon="check" title="Nothing waiting"
                      description="Every recorded flag has been reviewed." />
        )}

        {state === "ready" && rows.length > 0 && (
          <div className="space-y-4">
            {rows.map((row) => (
              <article key={row.id} className="rounded-2xl border border-border bg-surface shadow-card p-5">
                <div className="flex flex-wrap items-start justify-between gap-3 mb-3">
                  <div>
                    <div className="flex flex-wrap items-center gap-2 mb-1">
                      <span className={badgeClass(row.severity)}>{row.severity}</span>
                      <span className="font-semibold text-ink">{eventTypeLabel(row.event_type)}</span>
                    </div>
                    <p className="text-sm text-muted">
                      <Link to={`/admin/candidates/${row.student_id}`} className="text-primary hover:underline">
                        {row.student_name || `Candidate ${row.student_id}`}
                      </Link>
                      {" · "}
                      {row.exam_title}
                      {" · "}
                      {fmtDateTime(row.created_at)}
                    </p>
                  </div>
                  {row.waiting_hours != null && (
                    <span className={`text-xs font-semibold ${row.waiting_hours > 48 ? "text-danger" : "text-muted"}`}>
                      waiting {row.waiting_hours < 24
                        ? `${Math.round(row.waiting_hours)}h`
                        : `${Math.round(row.waiting_hours / 24)}d`}
                    </span>
                  )}
                </div>

                {row.description && (
                  <p className="text-sm text-ink bg-page rounded-lg px-3 py-2 mb-3">{row.description}</p>
                )}

                {row.has_screenshot && <div className="mb-3"><Evidence eventId={row.id} /></div>}

                <div className="flex flex-wrap items-center gap-2">
                  <button
                    type="button" disabled={deciding === row.id}
                    onClick={() => decide(row.id, "confirmed")}
                    className={`${btnPrimary.replace("px-5 py-3", "px-4 py-2")} text-sm disabled:opacity-60`}
                  >
                    Confirm violation
                  </button>
                  <button
                    type="button" disabled={deciding === row.id}
                    onClick={() => decide(row.id, "dismissed")}
                    className={`${btnGhost} px-4 py-2 text-sm disabled:opacity-60`}
                  >
                    Dismiss
                  </button>
                  <Link to={`/admin/attempts/${row.attempt_id}`}
                        className="text-sm font-semibold text-primary hover:underline ml-1">
                    Open full report
                  </Link>
                </div>
                {rowError?.id === row.id && (
                  <p role="alert" className="mt-2 text-sm text-danger">{rowError.message}</p>
                )}
              </article>
            ))}

            <Pagination page={page} pageCount={pageInfo.total_pages} onChange={setPage}
                        totalCount={pageInfo.total} pageSize={PAGE_SIZE} />
          </div>
        )}
      </main>
    </div>
  );
}
