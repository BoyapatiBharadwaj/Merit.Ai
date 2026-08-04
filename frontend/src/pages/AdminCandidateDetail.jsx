import { useEffect, useState } from "react";
import { Navigate, useNavigate, useParams } from "react-router-dom";
import DashboardHeader from "../components/DashboardHeader.jsx";
import Icon from "../components/Icon.jsx";
import EmptyState from "../components/EmptyState.jsx";
import Breadcrumbs from "../components/Breadcrumbs.jsx";
import StatCard from "../components/StatCard.jsx";
import IdentityPhotoModal from "../components/IdentityPhotoModal.jsx";
import { Api, ApiError } from "../lib/api.js";
import { badgeClass, fmtDateTime, fmtPercent } from "../lib/adminUi.js";
import { isLoggedIn, getRole } from "../lib/auth.js";

export default function AdminCandidateDetail() {
  const { studentId } = useParams();
  const navigate = useNavigate();
  const [candidate, setCandidate] = useState(null);
  const [loadError, setLoadError] = useState("");
  const [viewingIdentity, setViewingIdentity] = useState(false);

  useEffect(() => {
    setCandidate(null);
    Api.get(`/admin/candidates/${studentId}`)
      .then(setCandidate)
      .catch((err) => setLoadError(err instanceof ApiError ? err.message : "Couldn't load this candidate."));
  }, [studentId]);

  if (!isLoggedIn() || getRole() !== "admin") return <Navigate to="/login" replace />;

  return (
    <div className="min-h-screen bg-page text-ink">
      <DashboardHeader title="Candidate Profile" />
      <main className="max-w-6xl mx-auto w-full p-4 sm:p-6">
        <Breadcrumbs
          trail={[
            { label: "Dashboard", to: "/dashboard" },
            { label: "Candidates", to: "/admin/candidates" },
            { label: candidate ? candidate.full_name : "…" },
          ]}
        />

        {loadError && (
          <div className="mb-4 flex items-start gap-2.5 rounded-xl border border-danger/30 bg-danger/5 px-4 py-3 text-sm text-danger">
            <Icon name="alert" width={16} height={16} className="mt-0.5 shrink-0" />
            <span>{loadError}</span>
          </div>
        )}

        {!candidate ? (
          <div className="skeleton skeleton-card h-32 mb-6" />
        ) : (
          <div className="rounded-2xl border border-border bg-surface shadow-card p-5 mb-6">
            <div className="flex flex-wrap items-start justify-between gap-4">
              <div>
                <h1 className="text-xl font-extrabold tracking-tight">{candidate.full_name}</h1>
                <p className="text-sm text-muted mt-0.5">{candidate.email}</p>
                <p className="text-sm text-muted">
                  {candidate.roll_number ? `${candidate.roll_number} · ` : ""}{candidate.organization_name || "No organization"}
                </p>
                <div className="mt-2 flex flex-wrap items-center gap-2">
                  <span className={badgeClass(candidate.is_active ? "success" : "muted")}>{candidate.is_active ? "Active" : "Disabled"}</span>
                  <span className={badgeClass(candidate.face_registered ? "success" : "muted")}>Face: {candidate.face_registered ? "Registered" : "Not registered"}</span>
                  <span className={badgeClass(candidate.id_verified ? "success" : "warning")}>ID Card: {candidate.id_verified ? "Verified" : "Pending"}</span>
                </div>
              </div>
              <button type="button" onClick={() => setViewingIdentity(true)} className="text-xs font-semibold text-primary hover:underline shrink-0">
                View photo &amp; ID
              </button>
            </div>
          </div>
        )}

        <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mb-6">
          <StatCard label="Total Exams" value={candidate ? candidate.total_exams : null} tone="primary" />
          <StatCard label="Completed" value={candidate ? candidate.completed_exams : null} tone="success" />
          <StatCard label="In Progress" value={candidate ? candidate.in_progress_exams : null} tone="warning" />
          <StatCard label="Total Violations" value={candidate ? candidate.total_violations : null} tone="danger" />
        </div>

        <h2 className="text-lg font-bold mb-3">Exam History</h2>
        {!candidate ? (
          <div className="skeleton skeleton-card h-64" />
        ) : candidate.history.length === 0 ? (
          <EmptyState icon="doc" title="No exams taken yet" description="This candidate hasn't started any exam attempts." />
        ) : (
          <div className="rounded-2xl border border-border bg-surface shadow-card overflow-x-auto">
            <table className="min-w-full text-sm">
              <thead>
                <tr className="text-left text-xs text-muted uppercase tracking-wide border-b border-border">
                  <th className="px-4 py-3 font-semibold">Exam</th>
                  <th className="px-4 py-3 font-semibold">Examiner</th>
                  <th className="px-4 py-3 font-semibold">Started</th>
                  <th className="px-4 py-3 font-semibold">Status</th>
                  <th className="px-4 py-3 font-semibold text-right">Score</th>
                  <th className="px-4 py-3 font-semibold">Result</th>
                  <th className="px-4 py-3 font-semibold text-right">Violations</th>
                  <th className="px-4 py-3 font-semibold">Risk</th>
                  <th className="px-4 py-3 font-semibold">Action</th>
                </tr>
              </thead>
              <tbody>
                {candidate.history.map((h) => (
                  <tr key={h.attempt_id} onClick={() => navigate(`/admin/attempts/${h.attempt_id}`)}
                      className="border-b border-border last:border-0 hover:bg-page/60 transition-colors cursor-pointer">
                    <td className="px-4 py-3 font-medium">{h.exam_title}</td>
                    <td className="px-4 py-3 text-muted">{h.examiner_name}</td>
                    <td className="px-4 py-3 text-muted">{fmtDateTime(h.started_at)}</td>
                    <td className="px-4 py-3"><span className={badgeClass(h.status === "terminated" ? "danger" : h.status === "in_progress" ? "warning" : "success")}>{h.status.replace("_", " ")}</span></td>
                    <td className="px-4 py-3 text-right tabular-nums">{fmtPercent(h.score)}</td>
                    <td className="px-4 py-3">{h.result ? <span className={badgeClass(h.result === "Passed" ? "success" : "danger")}>{h.result}</span> : "—"}</td>
                    <td className="px-4 py-3 text-right tabular-nums">{h.violations}</td>
                    <td className="px-4 py-3">{h.risk ? <span className={badgeClass(h.risk)}>{h.risk}</span> : "—"}</td>
                    <td className="px-4 py-3">
                      <button type="button" onClick={(e) => { e.stopPropagation(); navigate(`/admin/attempts/${h.attempt_id}`); }}
                              className="text-xs font-semibold text-primary hover:underline">
                        View Report
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </main>
      {viewingIdentity && candidate && (
        <IdentityPhotoModal studentId={candidate.id} studentName={candidate.full_name} onClose={() => setViewingIdentity(false)} />
      )}
    </div>
  );
}
