import { useEffect, useState } from "react";
import { Navigate, useNavigate } from "react-router-dom";
import DashboardHeader from "../components/DashboardHeader.jsx";
import Icon from "../components/Icon.jsx";
import EmptyState from "../components/EmptyState.jsx";
import Breadcrumbs from "../components/Breadcrumbs.jsx";
import Pagination, { paginate } from "../components/Pagination.jsx";
import { usePhotoBlob } from "../components/IdentityPhotoModal.jsx";
import { Api, ApiError } from "../lib/api.js";
import { fieldInput } from "../lib/ui.js";
import { badgeClass, eventTypeLabel, fmtDateTime, toneFor } from "../lib/adminUi.js";
import { isLoggedIn, getRole } from "../lib/auth.js";

const PAGE_SIZE = 15;
const DECISIONS = ["pending", "confirmed", "dismissed"];

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

export default function AdminViolations() {
  const navigate = useNavigate();
  const [rows, setRows] = useState(null);
  const [loadError, setLoadError] = useState("");
  const [severity, setSeverity] = useState("");
  const [decision, setDecision] = useState("");
  const [searchInput, setSearchInput] = useState("");
  const [page, setPage] = useState(1);
  const [viewingEvidenceId, setViewingEvidenceId] = useState(null);

  async function load() {
    try {
      const params = new URLSearchParams();
      if (severity) params.set("severity", severity);
      if (decision) params.set("decision", decision);
      const qs = params.toString();
      setRows(await Api.get(`/admin/violations${qs ? `?${qs}` : ""}`));
    } catch (err) {
      setLoadError(err instanceof ApiError ? err.message : "Couldn't load violations.");
    }
  }

  useEffect(() => {
    setRows(null);
    setPage(1);
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [severity, decision]);

  if (!isLoggedIn() || getRole() !== "admin") return <Navigate to="/login" replace />;

  const search = searchInput.trim().toLowerCase();
  const filtered = (rows || []).filter((v) =>
    !search || v.student_name.toLowerCase().includes(search) || v.exam_title.toLowerCase().includes(search) || v.examiner_name.toLowerCase().includes(search),
  );
  const { pageRows, pageCount, safePage } = paginate(filtered, page, PAGE_SIZE);

  async function handleDecisionChange(eventId, next) {
    const previous = rows;
    setRows((rs) => rs.map((v) => (v.id === eventId ? { ...v, admin_decision: next } : v)));
    try {
      await Api.patch(`/proctoring/events/${eventId}/decision`, { decision: next });
    } catch (err) {
      setRows(previous);
      setLoadError(err instanceof ApiError ? err.message : "Couldn't update that decision.");
    }
  }

  return (
    <div className="min-h-screen bg-page text-ink">
      <DashboardHeader title="Violations" />
      <main className="max-w-6xl mx-auto w-full p-4 sm:p-6">
        <Breadcrumbs trail={[{ label: "Dashboard", to: "/dashboard" }, { label: "Violations" }]} />

        <h1 className="text-2xl font-extrabold tracking-tight mb-1">Violations</h1>
        <p className="text-sm text-muted mb-5">Every proctoring violation logged across the platform.</p>

        {loadError && (
          <div className="mb-4 flex items-start gap-2.5 rounded-xl border border-danger/30 bg-danger/5 px-4 py-3 text-sm text-danger">
            <Icon name="alert" width={16} height={16} className="mt-0.5 shrink-0" />
            <span>{loadError}</span>
          </div>
        )}

        <div className="flex flex-wrap gap-3 mb-4">
          <div className="relative flex-1 min-w-[220px]">
            <Icon name="user" width={16} height={16} className="absolute left-3.5 top-1/2 -translate-y-1/2 text-muted pointer-events-none" />
            <input type="search" placeholder="Search by candidate, exam, or examiner…" value={searchInput}
                   onChange={(e) => { setSearchInput(e.target.value); setPage(1); }} className={`${fieldInput} pl-10`} />
          </div>
          <select value={severity} onChange={(e) => setSeverity(e.target.value)} className={`${fieldInput} sm:w-36`}>
            <option value="">All severity</option>
            <option value="low">Low</option>
            <option value="medium">Medium</option>
            <option value="high">High</option>
          </select>
          <select value={decision} onChange={(e) => setDecision(e.target.value)} className={`${fieldInput} sm:w-40`}>
            <option value="">All decisions</option>
            <option value="pending">Pending</option>
            <option value="confirmed">Confirmed</option>
            <option value="dismissed">Dismissed</option>
          </select>
        </div>

        {!rows ? (
          <div className="skeleton skeleton-card h-64" />
        ) : filtered.length === 0 ? (
          <EmptyState icon="flag" title="No violations found" description="Try a different search or filter." />
        ) : (
          <div className="rounded-2xl border border-border bg-surface shadow-card overflow-x-auto">
            <table className="min-w-full text-sm">
              <thead>
                <tr className="text-left text-xs text-muted uppercase tracking-wide border-b border-border">
                  <th className="px-4 py-3 font-semibold">Time</th>
                  <th className="px-4 py-3 font-semibold">Candidate</th>
                  <th className="px-4 py-3 font-semibold">Exam</th>
                  <th className="px-4 py-3 font-semibold">Examiner</th>
                  <th className="px-4 py-3 font-semibold">Violation</th>
                  <th className="px-4 py-3 font-semibold">Severity</th>
                  <th className="px-4 py-3 font-semibold">Evidence</th>
                  <th className="px-4 py-3 font-semibold">Admin Decision</th>
                </tr>
              </thead>
              <tbody>
                {pageRows.map((v) => (
                  <tr key={v.id} onClick={() => navigate(`/admin/attempts/${v.attempt_id}`)}
                      className="border-b border-border last:border-0 hover:bg-page/60 transition-colors cursor-pointer">
                    <td className="px-4 py-3 text-muted whitespace-nowrap">{fmtDateTime(v.created_at)}</td>
                    <td className="px-4 py-3 font-medium">{v.student_name}</td>
                    <td className="px-4 py-3">{v.exam_title}</td>
                    <td className="px-4 py-3 text-muted">{v.examiner_name}</td>
                    <td className="px-4 py-3">{eventTypeLabel(v.event_type)}</td>
                    <td className="px-4 py-3"><span className={badgeClass(toneFor(v.severity))}>{v.severity}</span></td>
                    <td className="px-4 py-3">
                      {v.has_screenshot ? (
                        <button type="button" onClick={(e) => { e.stopPropagation(); setViewingEvidenceId(v.id); }} className="text-xs font-semibold text-primary hover:underline">
                          View
                        </button>
                      ) : (
                        <span className="text-xs text-muted">—</span>
                      )}
                    </td>
                    <td className="px-4 py-3" onClick={(e) => e.stopPropagation()}>
                      <select
                        value={v.admin_decision}
                        onChange={(e) => handleDecisionChange(v.id, e.target.value)}
                        className="rounded-lg border border-border bg-input text-ink text-xs font-semibold px-2 py-1.5 outline-none focus:border-primary capitalize"
                      >
                        {DECISIONS.map((d) => (
                          <option key={d} value={d}>{d}</option>
                        ))}
                      </select>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <Pagination page={safePage} pageCount={pageCount} onChange={setPage} totalCount={filtered.length} pageSize={PAGE_SIZE} />
          </div>
        )}
      </main>
      <EvidenceModal eventId={viewingEvidenceId} onClose={() => setViewingEvidenceId(null)} />
    </div>
  );
}
