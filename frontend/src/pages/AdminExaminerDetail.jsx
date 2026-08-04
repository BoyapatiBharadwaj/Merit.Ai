import { useEffect, useState } from "react";
import { Navigate, useNavigate, useParams } from "react-router-dom";
import DashboardHeader from "../components/DashboardHeader.jsx";
import Icon from "../components/Icon.jsx";
import EmptyState from "../components/EmptyState.jsx";
import Breadcrumbs from "../components/Breadcrumbs.jsx";
import StatCard from "../components/StatCard.jsx";
import Tabs from "../components/Tabs.jsx";
import CredentialsHandoff, { generatePassword } from "../components/CredentialsHandoff.jsx";
import { Api, ApiError } from "../lib/api.js";
import { btnPrimary } from "../lib/ui.js";
import { badgeClass, fmtDate, fmtPercent } from "../lib/adminUi.js";
import { isLoggedIn, getRole } from "../lib/auth.js";

const TABS = [
  { id: "all", label: "All Exams" },
  { id: "active", label: "Active" },
  { id: "upcoming", label: "Upcoming" },
  { id: "completed", label: "Completed" },
];

export default function AdminExaminerDetail() {
  const { examinerId } = useParams();
  const navigate = useNavigate();

  const [detail, setDetail] = useState(null);
  const [exams, setExams] = useState(null);
  const [tab, setTab] = useState("all");
  const [loadError, setLoadError] = useState("");

  const [revealed, setRevealed] = useState(null);
  const [confirmingReset, setConfirmingReset] = useState(false);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState("");

  async function loadDetail() {
    try {
      setDetail(await Api.get(`/admin/examiners/${examinerId}`));
    } catch (err) {
      setLoadError(err instanceof ApiError ? err.message : "Couldn't load this examiner.");
    }
  }

  useEffect(() => {
    setDetail(null);
    loadDetail();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [examinerId]);

  useEffect(() => {
    setExams(null);
    Api.get(`/admin/examiners/${examinerId}/exams?status=${tab}`)
      .then(setExams)
      .catch((err) => setLoadError(err instanceof ApiError ? err.message : "Couldn't load exams."));
  }, [examinerId, tab]);

  if (!isLoggedIn() || getRole() !== "admin") return <Navigate to="/login" replace />;

  async function handleToggleActive() {
    setBusy(true);
    setActionError("");
    try {
      await Api.post(`/users/${detail.user_id}/${detail.is_active ? "deactivate" : "activate"}`);
      await loadDetail();
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Couldn't update this account.");
    } finally {
      setBusy(false);
    }
  }

  async function handleResetPassword() {
    setBusy(true);
    setActionError("");
    const password = generatePassword();
    try {
      await Api.post(`/users/${detail.user_id}/reset-password`, { new_password: password });
      setRevealed(password);
      setConfirmingReset(false);
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Couldn't reset that password.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="min-h-screen bg-page text-ink">
      <DashboardHeader title="Examiner Details" />
      <main className="max-w-6xl mx-auto w-full p-4 sm:p-6">
        <Breadcrumbs
          trail={[
            { label: "Dashboard", to: "/dashboard" },
            { label: "Examiners", to: "/admin/examiners" },
            { label: detail ? detail.full_name : "…" },
          ]}
        />

        {loadError && (
          <div className="mb-4 flex items-start gap-2.5 rounded-xl border border-danger/30 bg-danger/5 px-4 py-3 text-sm text-danger">
            <Icon name="alert" width={16} height={16} className="mt-0.5 shrink-0" />
            <span>{loadError}</span>
          </div>
        )}

        {!detail ? (
          <div className="skeleton skeleton-card h-32 mb-6" />
        ) : (
          <div className="rounded-2xl border border-border bg-surface shadow-card p-5 mb-6">
            <div className="flex flex-wrap items-start justify-between gap-4">
              <div>
                <h1 className="text-xl font-extrabold tracking-tight">{detail.full_name}</h1>
                <p className="text-sm text-muted mt-0.5">{detail.email}</p>
                <p className="text-sm text-muted">{detail.organization_name || "No organization set"}</p>
                <div className="mt-2 flex items-center gap-2">
                  <span className="text-xs text-muted">Account status:</span>
                  <span className={badgeClass(detail.is_active ? "success" : "muted")}>{detail.is_active ? "Active" : "Disabled"}</span>
                </div>
              </div>
              <div className="flex flex-col items-end gap-2">
                {actionError && <p className="text-xs text-danger max-w-xs text-right">{actionError}</p>}
                <div className="flex gap-2">
                  <button type="button" disabled={busy} onClick={handleToggleActive}
                          className="px-3 py-1.5 rounded-lg border border-border text-xs font-semibold text-ink hover:border-primary hover:text-primary transition-colors disabled:opacity-50">
                    {detail.is_active ? "Disable account" : "Enable account"}
                  </button>
                  {confirmingReset ? (
                    <>
                      <button type="button" disabled={busy} onClick={handleResetPassword}
                              className="px-3 py-1.5 rounded-lg border border-danger/40 text-xs font-semibold text-danger hover:bg-danger/10 transition-colors disabled:opacity-50">
                        Confirm reset
                      </button>
                      <button type="button" onClick={() => setConfirmingReset(false)} className="px-3 py-1.5 text-xs font-semibold text-muted hover:text-ink">
                        Cancel
                      </button>
                    </>
                  ) : (
                    <button type="button" onClick={() => setConfirmingReset(true)}
                            className="px-3 py-1.5 rounded-lg border border-border text-xs font-semibold text-ink hover:border-primary hover:text-primary transition-colors">
                      Reset password
                    </button>
                  )}
                </div>
              </div>
            </div>
            {revealed && (
              <div className="mt-4">
                <CredentialsHandoff email={detail.email} password={revealed} onDismiss={() => setRevealed(null)} />
              </div>
            )}
          </div>
        )}

        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-4 mb-6">
          <StatCard label="Total Exams" value={detail ? detail.total_exams : null} tone="primary" />
          <StatCard label="Active Exams" value={detail ? detail.active_exams : null} tone="success" />
          <StatCard label="Upcoming Exams" value={detail ? detail.upcoming_exams : null} tone="primary" />
          <StatCard label="Completed Exams" value={detail ? detail.completed_exams : null} tone="muted" />
          <StatCard label="Total Candidates" value={detail ? detail.total_candidates : null} tone="primary" />
          <StatCard label="Total Violations" value={detail ? detail.total_violations : null} tone="danger" />
        </div>

        <Tabs tabs={TABS} active={tab} onChange={setTab} />

        {!exams ? (
          <div className="skeleton skeleton-card h-64" />
        ) : exams.length === 0 ? (
          <EmptyState icon="doc" title="No exams in this bucket" description="Exams matching this tab will appear here." />
        ) : (
          <div className="rounded-2xl border border-border bg-surface shadow-card overflow-x-auto">
            <table className="min-w-full text-sm">
              <thead>
                <tr className="text-left text-xs text-muted uppercase tracking-wide border-b border-border">
                  <th className="px-4 py-3 font-semibold">Exam Name</th>
                  <th className="px-4 py-3 font-semibold">Type</th>
                  <th className="px-4 py-3 font-semibold">Scheduled Date</th>
                  <th className="px-4 py-3 font-semibold text-right">Enrolled</th>
                  <th className="px-4 py-3 font-semibold text-right">Completed</th>
                  <th className="px-4 py-3 font-semibold text-right">Violations</th>
                  <th className="px-4 py-3 font-semibold text-right">Average Score</th>
                  <th className="px-4 py-3 font-semibold">Status</th>
                  <th className="px-4 py-3 font-semibold">Action</th>
                </tr>
              </thead>
              <tbody>
                {exams.map((exam) => (
                  <tr key={exam.id} onClick={() => navigate(`/admin/exams/${exam.id}`)}
                      className="border-b border-border last:border-0 hover:bg-page/60 transition-colors cursor-pointer">
                    <td className="px-4 py-3 font-medium">{exam.title}</td>
                    <td className="px-4 py-3 text-muted">{exam.type}</td>
                    <td className="px-4 py-3 text-muted">{fmtDate(exam.scheduled_date)}</td>
                    <td className="px-4 py-3 text-right tabular-nums">{exam.enrolled}</td>
                    <td className="px-4 py-3 text-right tabular-nums">{exam.completed}</td>
                    <td className="px-4 py-3 text-right tabular-nums">{exam.violations}</td>
                    <td className="px-4 py-3 text-right tabular-nums">{fmtPercent(exam.average_score)}</td>
                    <td className="px-4 py-3"><span className={badgeClass(exam.status === "active" ? "success" : exam.status === "upcoming" ? "primary" : "muted")}>{exam.status}</span></td>
                    <td className="px-4 py-3">
                      <button type="button" onClick={(e) => { e.stopPropagation(); navigate(`/admin/exams/${exam.id}`); }}
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
