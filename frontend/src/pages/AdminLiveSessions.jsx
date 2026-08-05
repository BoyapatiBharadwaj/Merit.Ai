import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import RedirectToLogin from "../components/RedirectToLogin.jsx";
import DashboardHeader from "../components/DashboardHeader.jsx";
import Icon from "../components/Icon.jsx";
import EmptyState from "../components/EmptyState.jsx";
import Breadcrumbs from "../components/Breadcrumbs.jsx";
import { Api, ApiError } from "../lib/api.js";
import { fmtDateTime, fmtMinutes } from "../lib/adminUi.js";
import { isLoggedIn, getRole } from "../lib/auth.js";

const REFRESH_MS = 15000;

/** Every attempt currently IN_PROGRESS, platform-wide -- auto-refreshes on
 * an interval since "live" implies the list is expected to change out from
 * under the admin while they're looking at it (a candidate submits, another
 * starts) without them needing to manually reload the page each time. */
export default function AdminLiveSessions() {
  const navigate = useNavigate();
  const [rows, setRows] = useState(null);
  const [loadError, setLoadError] = useState("");
  const [refreshing, setRefreshing] = useState(false);
  const timerRef = useRef(null);

  async function load(showSpinner) {
    if (showSpinner) setRefreshing(true);
    try {
      setRows(await Api.get("/admin/live-sessions"));
      setLoadError("");
    } catch (err) {
      setLoadError(err instanceof ApiError ? err.message : "Couldn't load live sessions.");
    } finally {
      if (showSpinner) setRefreshing(false);
    }
  }

  useEffect(() => {
    load(false);
    timerRef.current = setInterval(() => load(false), REFRESH_MS);
    return () => clearInterval(timerRef.current);
  }, []);

  if (!isLoggedIn() || getRole() !== "admin") return <RedirectToLogin />;

  return (
    <div className="min-h-screen bg-page text-ink">
      <DashboardHeader title="Live Sessions" />
      <main className="max-w-6xl mx-auto w-full p-4 sm:p-6">
        <Breadcrumbs trail={[{ label: "Dashboard", to: "/dashboard" }, { label: "Live Sessions" }]} />

        <div className="flex flex-wrap items-center justify-between gap-3 mb-5">
          <div>
            <h1 className="text-2xl font-extrabold tracking-tight">Live Sessions</h1>
            <p className="text-sm text-muted mt-1">Candidates currently taking an exam, right now. Refreshes automatically every 15 seconds.</p>
          </div>
          <button type="button" onClick={() => load(true)}
                  className={`inline-flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-xs font-semibold text-ink hover:border-primary hover:text-primary transition-colors ${refreshing ? "opacity-60 pointer-events-none" : ""}`}>
            <Icon name="clock" width={14} height={14} className={refreshing ? "animate-spin" : ""} />
            Refresh
          </button>
        </div>

        {loadError && (
          <div className="mb-4 flex items-start gap-2.5 rounded-xl border border-danger/30 bg-danger/5 px-4 py-3 text-sm text-danger">
            <Icon name="alert" width={16} height={16} className="mt-0.5 shrink-0" />
            <span>{loadError}</span>
          </div>
        )}

        {!rows ? (
          <div className="skeleton skeleton-card h-64" />
        ) : rows.length === 0 ? (
          <EmptyState icon="eye" title="No one is taking an exam right now" description="In-progress attempts will show up here as soon as a candidate starts one." />
        ) : (
          <div className="rounded-2xl border border-border bg-surface shadow-card overflow-x-auto">
            <table className="min-w-full text-sm">
              <thead>
                <tr className="text-left text-xs text-muted uppercase tracking-wide border-b border-border">
                  <th className="px-4 py-3 font-semibold">Candidate</th>
                  <th className="px-4 py-3 font-semibold">Exam</th>
                  <th className="px-4 py-3 font-semibold">Examiner</th>
                  <th className="px-4 py-3 font-semibold">Started At</th>
                  <th className="px-4 py-3 font-semibold">Elapsed</th>
                  <th className="px-4 py-3 font-semibold">Action</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.attempt_id} onClick={() => navigate(`/admin/attempts/${r.attempt_id}`)}
                      className="border-b border-border last:border-0 hover:bg-page/60 transition-colors cursor-pointer">
                    <td className="px-4 py-3 font-medium">{r.student_name}</td>
                    <td className="px-4 py-3">{r.exam_title}</td>
                    <td className="px-4 py-3 text-muted">{r.examiner_name}</td>
                    <td className="px-4 py-3 text-muted">{fmtDateTime(r.started_at)}</td>
                    <td className="px-4 py-3">
                      <span className="inline-flex items-center gap-1.5 text-warning font-semibold">
                        <span className="h-1.5 w-1.5 rounded-full bg-warning animate-pulse" />
                        {fmtMinutes(r.elapsed_minutes)}
                      </span>
                    </td>
                    <td className="px-4 py-3">
                      <button type="button" onClick={(e) => { e.stopPropagation(); navigate(`/admin/attempts/${r.attempt_id}`); }}
                              className="text-xs font-semibold text-primary hover:underline">
                        View
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </main>
    </div>
  );
}
