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
  // Account actions. Every one of these already existed
  // as an endpoint and had no button anywhere.
  const [busyAction, setBusyAction] = useState(null);
  const [actionResult, setActionResult] = useState(null); // { tone, message }
  const [confirmText, setConfirmText] = useState("");
  const [confirming, setConfirming] = useState(null); // "delete" | "biometrics" | "reverify" | null
  const [reverifyReason, setReverifyReason] = useState("");
  const [editing, setEditing] = useState(false);
  const [editForm, setEditForm] = useState(null);
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
                onClick={() => {
                  setEditForm({
                    first_name: candidate.first_name || "",
                    last_name: candidate.last_name || "",
                    email: candidate.email || "",
                    roll_number: candidate.roll_number || "",
                  });
                  setEditing(true);
                  setActionResult(null);
                }}
                className={`${btnGhost} px-4 py-2 text-sm disabled:opacity-60`}
              >
                Edit details
              </button>

              {/* Separate from "Erase biometric data" on purpose, and worded so
                  the difference is visible: this one KEEPS the photos. The two
                  actions pull in opposite directions on the same data, and an
                  administrator picking the wrong one either destroys the
                  evidence they were about to review, or fails to remove data
                  somebody asked to have removed. */}
              <button
                type="button"
                disabled={busyAction !== null || candidate.reverification_required
                          || !(candidate.face_registered || candidate.id_verified)}
                onClick={() => { setConfirming("reverify"); setReverifyReason(""); setActionResult(null); }}
                title={candidate.reverification_required
                  ? "Already asked — waiting for the candidate"
                  : !(candidate.face_registered || candidate.id_verified)
                    ? "Nothing verified yet, so there is nothing to re-verify"
                    : undefined}
                className={`${btnGhost} px-4 py-2 text-sm disabled:opacity-60`}
              >
                Require re-verification
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

            {/* Re-verification is not destructive, so it gets a normal panel
                rather than the red typed-email ceremony. Asking for a reason
                is the only friction, and it earns its place: the reason is
                shown to the candidate verbatim, and a demand to re-prove your
                identity with nothing attached reads as either an accusation or
                a malfunction. */}
            {confirming === "reverify" && (
              <div className="mt-4 rounded-xl border border-border bg-page px-4 py-4">
                <p className="text-sm font-semibold text-ink mb-1">
                  Ask this candidate to verify their identity again?
                </p>
                <p className="text-sm text-muted mb-3 leading-relaxed">
                  Their existing face photo and ID card stay on file — this does not delete
                  anything. They will be blocked from proctored exams until they have re-registered
                  their face <em>and</em> re-submitted their ID card.
                </p>
                <label htmlFor="reverify-reason" className="block text-xs font-semibold text-muted mb-1.5">
                  Reason (shown to the candidate)
                </label>
                <input
                  id="reverify-reason"
                  value={reverifyReason}
                  onChange={(e) => setReverifyReason(e.target.value)}
                  maxLength={500}
                  className={`${fieldInput} !mb-3`}
                  placeholder="e.g. The registered photo did not match at the last sitting."
                  autoComplete="off"
                />
                <div className="flex flex-wrap gap-2">
                  <button
                    type="button" disabled={busyAction !== null || !reverifyReason.trim()}
                    onClick={() => runAction("reverify",
                      () => Api.post(`/admin/candidates/${studentId}/require-reverification`,
                                     { reason: reverifyReason.trim(), notify: true }),
                      "Re-verification requested. The candidate has been emailed.")}
                    className={`${btnPrimary.replace("px-5 py-3", "px-4 py-2")} text-sm disabled:opacity-40`}
                  >
                    {busyAction ? "Working…" : "Request re-verification"}
                  </button>
                  <button type="button" onClick={() => { setConfirming(null); setReverifyReason(""); }}
                          className={`${btnGhost} px-4 py-2 text-sm`}>
                    Cancel
                  </button>
                </div>
              </div>
            )}

            {editing && editForm && (
              <form
                className="mt-4 rounded-xl border border-border bg-page px-4 py-4"
                onSubmit={(e) => {
                  e.preventDefault();
                  runAction("edit",
                    () => Api.patch(`/admin/candidates/${studentId}`, {
                      first_name: editForm.first_name.trim(),
                      last_name: editForm.last_name.trim(),
                      email: editForm.email.trim(),
                      roll_number: editForm.roll_number.trim() || null,
                    }),
                    "Details updated.").then((result) => {
                      if (result) {
                        setEditing(false);
                        if (result.reverification_required) {
                          setActionResult({
                            tone: "success",
                            message: "Details updated. Because their name or email was verified "
                              + "against their ID card, their identity has been unlocked and they "
                              + "must verify again before their next proctored exam.",
                          });
                        }
                      }
                    });
                }}
              >
                <p className="text-sm font-semibold text-ink mb-3">Edit candidate details</p>

                {candidate.identity_locked && (
                  <p className="mb-3 rounded-lg border border-warning/30 bg-warning/5 px-3 py-2 text-xs leading-relaxed text-warning">
                    This candidate&apos;s name was matched against their ID card. Changing the name
                    or email will unlock their identity and require them to verify again — otherwise
                    a &ldquo;verified&rdquo; badge would be attached to details nobody has checked.
                    Changing only the roll number has no such effect.
                  </p>
                )}

                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                  {[
                    ["first_name", "First name"],
                    ["last_name", "Last name"],
                    ["email", "Email"],
                    ["roll_number", "Roll number"],
                  ].map(([key, label]) => (
                    <div key={key}>
                      <label htmlFor={`edit-${key}`} className="block text-xs font-semibold text-muted mb-1.5">
                        {label}
                      </label>
                      <input
                        id={`edit-${key}`}
                        value={editForm[key]}
                        onChange={(e) => setEditForm({ ...editForm, [key]: e.target.value })}
                        className={fieldInput}
                        type={key === "email" ? "email" : "text"}
                        autoComplete="off"
                      />
                    </div>
                  ))}
                </div>

                <div className="flex flex-wrap gap-2 mt-3">
                  <button type="submit" disabled={busyAction !== null}
                          className={`${btnPrimary.replace("px-5 py-3", "px-4 py-2")} text-sm disabled:opacity-60`}>
                    {busyAction === "edit" ? "Saving…" : "Save changes"}
                  </button>
                  <button type="button" onClick={() => { setEditing(false); setEditForm(null); }}
                          className={`${btnGhost} px-4 py-2 text-sm`}>
                    Cancel
                  </button>
                </div>
              </form>
            )}

            {(confirming === "delete" || confirming === "biometrics") && (
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
