import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import RedirectToLogin from "../components/RedirectToLogin.jsx";
import DashboardHeader from "../components/DashboardHeader.jsx";
import Icon from "../components/Icon.jsx";
import EmptyState from "../components/EmptyState.jsx";
import Breadcrumbs from "../components/Breadcrumbs.jsx";
import StatCard from "../components/StatCard.jsx";
import IdentityPhotoModal from "../components/IdentityPhotoModal.jsx";
import { generatePassword } from "../components/CredentialsHandoff.jsx";
import { Api, ApiError } from "../lib/api.js";
import { btnPrimary, btnGhost, fieldInput } from "../lib/ui.js";
import { badgeClass, fmtDateTime, fmtPercent } from "../lib/adminUi.js";
import { isLoggedIn, getRole } from "../lib/auth.js";

export default function AdminCandidateDetail() {
  const { studentId } = useParams();
  const navigate = useNavigate();
  const [candidate, setCandidate] = useState(null);
  const [loadError, setLoadError] = useState("");
  const [viewingIdentity, setViewingIdentity] = useState(false);
  // Account actions. Every one of these already existed as an endpoint and had
  // no button anywhere -- so an administrator asked to suspend an account, or
  // to erase a candidate's biometrics on request, had no way to do it from the
  // page about that candidate.
  const [busyAction, setBusyAction] = useState(null);
  const [actionResult, setActionResult] = useState(null); // { tone, message }
  const [confirmText, setConfirmText] = useState("");
  const [confirming, setConfirming] = useState(null); // "delete" | "biometrics" | null
  const [revealedPassword, setRevealedPassword] = useState("");

  useEffect(() => {
    setCandidate(null);
    Api.get(`/admin/candidates/${studentId}`)
      .then(setCandidate)
      .catch((err) => setLoadError(err instanceof ApiError ? err.message : "Couldn't load this candidate."));
  }, [studentId]);

  async function runAction(name, request, successMessage) {
    setBusyAction(name);
    setActionResult(null);
    setRevealedPassword("");
    try {
      const result = await request();
      setActionResult({ tone: "success", message: successMessage });
      // Reload rather than patching local state: several of these change more
      // than one field, and a page that disagrees with the server about whether
      // an account is active is worse than a spinner.
      setCandidate(await Api.get(`/admin/candidates/${studentId}`));
      return result;
    } catch (err) {
      setActionResult({
        tone: "error",
        message: err instanceof ApiError ? err.message : "That action didn't complete.",
      });
      return null;
    } finally {
      setBusyAction(null);
      setConfirming(null);
      setConfirmText("");
    }
  }

  if (!isLoggedIn() || getRole() !== "admin") return <RedirectToLogin />;

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
          <StatCard label="Total Exams" value={candidate ? candidate.total_exams : null} tone="primary"
                    hint="Attempts started, not exams assigned" />
          <StatCard label="Completed" value={candidate ? candidate.completed_exams : null} tone="success" />
          <StatCard label="In Progress" value={candidate ? candidate.in_progress_exams : null} tone="warning" />
          <StatCard label="Total Violations" value={candidate ? candidate.total_violations : null} tone="danger"
                    hint="All flags recorded, including dismissed" />
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

        {/* Account actions.
            Every endpoint here already existed and none had a button, so the
            page about a candidate could not act on that candidate. Destructive
            actions need the email typed back: an accidental click on "delete
            account" is not recoverable, and a native confirm() cannot say which
            account is about to go. */}
        {candidate && (
          <section className="mt-6 rounded-2xl border border-border bg-surface shadow-card p-5">
            <h2 className="font-bold text-ink mb-1">Account actions</h2>
            <p className="text-sm text-muted mb-4">
              Everything here is recorded in the activity trail against your account.
            </p>

            {actionResult && (
              <div role="alert"
                   className={`mb-4 rounded-xl px-4 py-3 text-sm ${
                     actionResult.tone === "success"
                       ? "border border-success/30 bg-success/5 text-ink"
                       : "border border-danger/30 bg-danger/5 text-danger"
                   }`}>
                {actionResult.message}
              </div>
            )}

            {revealedPassword && (
              <div className="mb-4 rounded-xl border border-warning/30 bg-warning/5 px-4 py-3">
                <p className="text-sm text-ink mb-1.5">
                  New password — <strong>shown once</strong>. Pass it to the candidate through a
                  channel you trust; it is stored only as a hash and cannot be shown again.
                </p>
                <code className="block rounded-lg bg-page px-3 py-2 font-mono text-sm text-ink break-all">
                  {revealedPassword}
                </code>
              </div>
            )}

            <div className="flex flex-wrap gap-2">
              {candidate.is_active ? (
                <button
                  type="button" disabled={busyAction !== null}
                  onClick={() => runAction("deactivate",
                    () => Api.post(`/users/${candidate.user_id}/deactivate`),
                    "Account suspended. They can no longer sign in.")}
                  className={`${btnGhost} px-4 py-2 text-sm disabled:opacity-60`}
                >
                  Suspend account
                </button>
              ) : (
                <button
                  type="button" disabled={busyAction !== null}
                  onClick={() => runAction("activate",
                    () => Api.post(`/users/${candidate.user_id}/activate`),
                    "Account reactivated.")}
                  className={`${btnPrimary.replace("px-5 py-3", "px-4 py-2")} text-sm disabled:opacity-60`}
                >
                  Reactivate account
                </button>
              )}

              <button
                type="button" disabled={busyAction !== null}
                onClick={async () => {
                  const generated = generatePassword();
                  const result = await runAction("reset",
                    () => Api.post(`/users/${candidate.user_id}/reset-password`, { new_password: generated }),
                    "Password reset.");
                  if (result) setRevealedPassword(result.password || generated);
                }}
                className={`${btnGhost} px-4 py-2 text-sm disabled:opacity-60`}
              >
                Reset password
              </button>

              <button
                type="button" disabled={busyAction !== null}
                onClick={() => { setConfirming("biometrics"); setConfirmText(""); setActionResult(null); }}
                className={`${btnGhost} px-4 py-2 text-sm disabled:opacity-60`}
              >
                Erase biometric data
              </button>

              <button
                type="button" disabled={busyAction !== null}
                onClick={() => { setConfirming("delete"); setConfirmText(""); setActionResult(null); }}
                className="px-4 py-2 text-sm font-semibold rounded-xl border border-danger/40 text-danger hover:bg-danger/5 transition-colors disabled:opacity-60"
              >
                Delete account
              </button>
            </div>

            {confirming && (
              <div className="mt-4 rounded-xl border border-danger/30 bg-danger/5 px-4 py-4">
                <p className="text-sm text-ink mb-1 font-semibold">
                  {confirming === "delete"
                    ? "Delete this account permanently?"
                    : "Erase this candidate's face and ID data?"}
                </p>
                <p className="text-sm text-muted mb-3 leading-relaxed">
                  {confirming === "delete"
                    ? "Their attempts and results go with it. The activity trail keeps a record that the account existed and was deleted, but nothing else survives."
                    : "Their registered face photo, face signature and ID card image are removed. They will have to verify again before their next proctored exam. Their attempts and results are untouched."}
                </p>
                <label className="block text-xs font-semibold text-muted mb-1.5">
                  Type <span className="text-ink">{candidate.email}</span> to confirm
                </label>
                <input
                  value={confirmText}
                  onChange={(e) => setConfirmText(e.target.value)}
                  className={`${fieldInput} !mb-3`}
                  placeholder={candidate.email}
                  autoComplete="off"
                />
                <div className="flex flex-wrap gap-2">
                  <button
                    type="button"
                    disabled={confirmText.trim().toLowerCase() !== candidate.email.toLowerCase() || busyAction !== null}
                    onClick={() => {
                      if (confirming === "delete") {
                        runAction("delete",
                          async () => {
                            await Api.del(`/users/${candidate.user_id}`);
                            navigate("/admin/candidates", { replace: true });
                          },
                          "Account deleted.");
                      } else {
                        runAction("biometrics",
                          () => Api.del(`/users/${candidate.user_id}/biometrics`),
                          "Biometric data erased.");
                      }
                    }}
                    className="px-4 py-2 text-sm font-semibold rounded-xl bg-danger text-white disabled:opacity-40 disabled:cursor-not-allowed"
                  >
                    {busyAction ? "Working…" : confirming === "delete" ? "Delete permanently" : "Erase"}
                  </button>
                  <button type="button" onClick={() => { setConfirming(null); setConfirmText(""); }}
                          className={`${btnGhost} px-4 py-2 text-sm`}>
                    Cancel
                  </button>
                </div>
              </div>
            )}
          </section>
        )}
      </main>
      {viewingIdentity && candidate && (
        <IdentityPhotoModal studentId={candidate.id} studentName={candidate.full_name} onClose={() => setViewingIdentity(false)} />
      )}
    </div>
  );
}
