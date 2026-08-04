import { useCallback, useEffect, useState } from "react";
import { Navigate } from "react-router-dom";
import DashboardHeader from "../components/DashboardHeader.jsx";
import CaptureCard from "../components/CaptureCard.jsx";
import Icon from "../components/Icon.jsx";
import { PasswordField } from "../components/FormField.jsx";
import { btnPrimary, btnGhost, sectionEyebrow } from "../lib/ui.js";
import { Api, ApiError } from "../lib/api.js";
import { isLoggedIn, getRole } from "../lib/auth.js";

export default function Profile() {
  // See the identical comment in Exam.jsx: the guard used to run before any
  // hooks below, but Api.get can clear the session on a 401 (api.js), which
  // flips isLoggedIn() to false and would change the hook count on the next
  // render -- a Rules-of-Hooks crash. Hooks now run unconditionally and the
  // guard is evaluated once, right before the JSX return.
  const [me, setMe] = useState(null);
  const [loadError, setLoadError] = useState("");

  const refresh = useCallback(async () => {
    try {
      setMe(await Api.get("/users/me"));
    } catch (err) {
      setLoadError(err instanceof ApiError ? err.message : "Couldn't load your profile.");
    }
  }, []);

  useEffect(() => {
    // Guards against the one edge case the hook-order fix above introduces:
    // a logged-out user landing directly on this route would otherwise still
    // fire this fetch (with no token) before the render-time redirect below
    // sends them to /login.
    if (isLoggedIn()) refresh();
  }, [refresh]);

  const faceDone = Boolean(me?.face_registered);
  const idDone = Boolean(me?.id_verified);
  const locked = Boolean(me?.identity_locked);

  if (!isLoggedIn()) return <Navigate to="/login" replace />;

  // Examiners and admins get this page too. Identity verification is
  // student-only. Password management is for students and admins only --
  // examiner credentials are issued and rotated by an admin (see
  // Admin.jsx's reset-password action), so an examiner has nothing to do in
  // a "change password" section and it's hidden here rather than shown and
  // rejected by the backend.
  const isStudent = getRole() === "student";
  const isExaminer = getRole() === "examiner";

  return (
    <div className="min-h-screen flex flex-col bg-page text-ink">
      <DashboardHeader title="Profile" />

      <main className="flex-1 max-w-3xl mx-auto w-full px-5 sm:px-6 py-10 flex flex-col gap-10">
        <div className="animate-fade-in">
          <span className={`${sectionEyebrow} mb-3`}>
            <Icon name="user" width={14} height={14} />
            Your Account
          </span>
          <h1 className="text-2xl font-extrabold tracking-tight mb-2">
            {isStudent
              ? locked ? "Your identity is verified" : "Verify it's really you"
              : "Account & security"}
          </h1>
          <p className="text-sm text-muted leading-relaxed">
            {isStudent
              ? locked
                ? "Both checks are complete, so you can now sit proctored exams. Your name and email are locked to the ID you verified."
                : "Both steps below must be completed before your first proctored exam. Make sure you're in a well-lit space with only your face in frame."
              : isExaminer
                ? "Review your account details. Your password is managed by your administrator."
                : "Review your account details and manage the password you use to sign in."}
          </p>
        </div>

        {loadError && (
          <div className="flex items-start gap-2.5 rounded-xl border border-danger/30 bg-danger/5 px-4 py-3 text-sm text-danger">
            <Icon name="alert" width={16} height={16} className="mt-0.5 shrink-0" />
            <span>{loadError}</span>
          </div>
        )}

        {me && <AccountCard me={me} locked={locked} isStudent={isStudent} />}

        {isStudent && (
          <section className="flex flex-col gap-8">
            <SectionHeading
              icon="shield-check"
              title="Identity verification"
              description="Required once, before your first proctored exam."
              trailing={me && <VerificationPill faceDone={faceDone} idDone={idDone} />}
            />

            <StepCard number={1} done={faceDone}>
              <CaptureCard
                title="Face Registration"
                description="This photo is used to verify your identity during exams."
                mirrored
                captureLabel="Capture Photo"
                submitLabel="Register Face"
                onSubmit={async (image) => {
                  const res = await Api.post("/proctoring/face/register", { image_base64: image });
                  await refresh();
                  return res;
                }}
                formatResult={(res) => ({ tone: "success", message: res.message })}
              />
            </StepCard>

            <StepCard number={2} done={idDone}>
              <CaptureCard
                title="ID Card Verification"
                description="Capture your student/government ID card. We'll read the name and compare it to your registered name."
                captureLabel="Capture ID Card"
                submitLabel="Verify ID"
                onSubmit={async (image) => {
                  const res = await Api.post("/proctoring/id-card/verify", { image_base64: image });
                  await refresh();
                  return res;
                }}
                formatResult={(res) => ({ tone: res.name_matched ? "success" : "error", message: res.message })}
              />
            </StepCard>
          </section>
        )}

        {!isExaminer && (
          <section className="flex flex-col gap-5">
            <SectionHeading
              icon="lock"
              title="Sign-in & security"
              description="The password you use to log in to Merit.Ai."
            />
            <ChangePasswordCard />
          </section>
        )}
      </main>
    </div>
  );
}

/** Shared section header, so "Identity verification" and "Sign-in & security"
 * read as two peer sections of one page rather than a run of loose cards. */
function SectionHeading({ icon, title, description, trailing }) {
  return (
    <div className="flex items-start justify-between gap-4 border-b border-border pb-3">
      <div className="flex items-start gap-3 min-w-0">
        <span className="mt-0.5 inline-flex items-center justify-center w-8 h-8 rounded-lg bg-primary/10 text-primary shrink-0">
          <Icon name={icon} width={16} height={16} />
        </span>
        <div className="min-w-0">
          <h2 className="text-base font-bold text-ink">{title}</h2>
          <p className="text-xs text-muted mt-0.5">{description}</p>
        </div>
      </div>
      {trailing}
    </div>
  );
}

/** Compact "1 of 2 done" pill. Replaces the old full-width checklist banner,
 * which repeated information the numbered step badges already carry. */
function VerificationPill({ faceDone, idDone }) {
  const done = Number(faceDone) + Number(idDone);
  const complete = done === 2;
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-semibold shrink-0 ${
        complete ? "bg-success/10 text-success" : "bg-warning/10 text-warning"
      }`}
    >
      <Icon name={complete ? "check" : "clock"} width={12} height={12} />
      {complete ? "Complete" : `${done} of 2 done`}
    </span>
  );
}

/** Read-only account summary. Once identity is locked these fields are not
 * editable anywhere in the UI, matching the server-side rule in
 * auth_service.update_profile -- so the card explains *why* rather than
 * showing disabled inputs with no context. */
function AccountCard({ me, locked, isStudent }) {
  const initials = `${(me.first_name || "")[0] || ""}${(me.last_name || "")[0] || ""}`.toUpperCase();
  return (
    <div className="rounded-2xl border border-border bg-surface shadow-card overflow-hidden">
      <div className="flex items-center gap-4 border-b border-border bg-page/40 px-6 py-5">
        <span className="inline-flex items-center justify-center w-12 h-12 rounded-xl bg-primary/10 text-primary font-extrabold text-base shrink-0">
          {initials || <Icon name="user" width={20} height={20} />}
        </span>
        <div className="min-w-0 flex-1">
          <p className="text-base font-bold text-ink truncate">{me.full_name || "—"}</p>
          <p className="text-xs text-muted truncate">{me.email}</p>
        </div>
        <span className="inline-flex items-center rounded-full bg-page px-2.5 py-1 text-[11px] font-semibold capitalize text-muted border border-border shrink-0">
          {me.role}
        </span>
      </div>

      <div className="px-6 py-5">
        <dl className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <Field label="First name" value={me.first_name} />
          <Field label="Last name" value={me.last_name} />
          {isStudent && <Field label="Roll number" value={me.roll_number || "—"} />}
        </dl>

        <p className="mt-4 flex items-start gap-2 text-xs text-muted leading-relaxed">
          {isStudent && locked && (
            <Icon name="lock" width={13} height={13} className="mt-0.5 shrink-0 text-success" />
          )}
          <span>
            {!isStudent
              ? "Contact an administrator to change your name or email."
              : locked
                ? "Your name and email were matched against your ID card and can no longer be changed. Contact an administrator if a correction is needed."
                : "Your name must match the ID card you'll use for verification. Once verified, these details are locked."}
          </span>
        </p>
      </div>
    </div>
  );
}

function Field({ label, value }) {
  return (
    <div>
      <dt className="text-xs font-semibold text-muted mb-1">{label}</dt>
      <dd className="text-sm font-medium text-ink break-words">{value || "—"}</dd>
    </div>
  );
}

/** Wraps a CaptureCard with a numbered step badge so the two-step flow reads
 * as an ordered checklist rather than two unrelated cards. */
function StepCard({ number, done, children }) {
  return (
    <div className="relative">
      <span
        className={`absolute -top-3 -left-3 z-10 inline-flex items-center justify-center w-7 h-7 rounded-full text-xs font-bold shadow-card ${
          done ? "bg-success text-white" : "bg-ink text-page"
        }`}
      >
        {done ? <Icon name="check" width={13} height={13} /> : number}
      </span>
      {children}
    </div>
  );
}

/** Password is the only credential a locked student can still change, so it
 * gets a first-class card here rather than being buried in a settings page. */
function ChangePasswordCard() {
  const [form, setForm] = useState({ current: "", next: "", confirm: "" });
  const [result, setResult] = useState(null);
  const [saving, setSaving] = useState(false);

  function update(field) {
    return (e) => {
      setForm((f) => ({ ...f, [field]: e.target.value }));
      setResult(null);
    };
  }

  async function handleSubmit(e) {
    e.preventDefault();
    if (form.next.length < 8) {
      setResult({ tone: "error", message: "New password must be at least 8 characters." });
      return;
    }
    if (form.next !== form.confirm) {
      setResult({ tone: "error", message: "New passwords don't match." });
      return;
    }
    setSaving(true);
    try {
      const res = await Api.post("/users/me/password", {
        current_password: form.current,
        new_password: form.next,
      });
      setResult({ tone: "success", message: res.message || "Password updated." });
      setForm({ current: "", next: "", confirm: "" });
    } catch (err) {
      setResult({ tone: "error", message: err instanceof ApiError ? err.message : "Couldn't update your password." });
    } finally {
      setSaving(false);
    }
  }

  // Collapsed by default. Changing a password is a rare, deliberate act; three
  // always-open password inputs sitting under the ID-verification steps made
  // the page look like it was demanding four things at once.
  const [open, setOpen] = useState(false);

  const mismatch = form.confirm.length > 0 && form.next !== form.confirm;
  const tooShort = form.next.length > 0 && form.next.length < 8;
  const ready = form.current && form.next.length >= 8 && form.next === form.confirm;

  if (!open) {
    return (
      <div className="rounded-2xl border border-border bg-surface shadow-card px-6 py-5 flex flex-wrap items-center justify-between gap-4">
        <div className="flex items-center gap-3 min-w-0">
          <span className="inline-flex items-center justify-center w-9 h-9 rounded-lg bg-page border border-border text-muted shrink-0">
            <Icon name="lock" width={16} height={16} />
          </span>
          <div className="min-w-0">
            <p className="text-sm font-semibold text-ink">Password</p>
            <p className="text-xs text-muted">
              {result?.tone === "success" ? result.message : "Last changed when your account was set up."}
            </p>
          </div>
        </div>
        <button
          type="button"
          onClick={() => { setOpen(true); setResult(null); }}
          className={`${btnGhost.replace("px-5 py-3", "px-4 py-2")} text-sm shrink-0`}
        >
          Change password
        </button>
      </div>
    );
  }

  return (
    <div className="rounded-2xl border border-border bg-surface shadow-card overflow-hidden animate-slide-up">
      <div className="flex items-center justify-between gap-3 border-b border-border px-6 py-4">
        <div className="flex items-center gap-3">
          <span className="inline-flex items-center justify-center w-9 h-9 rounded-lg bg-primary/10 text-primary shrink-0">
            <Icon name="lock" width={16} height={16} />
          </span>
          <div>
            <p className="text-sm font-bold text-ink">Change password</p>
            <p className="text-xs text-muted">Enter your current password to confirm it's you.</p>
          </div>
        </div>
        <button
          type="button"
          onClick={() => { setOpen(false); setForm({ current: "", next: "", confirm: "" }); setResult(null); }}
          className="text-muted hover:text-ink transition-colors shrink-0"
          aria-label="Cancel"
        >
          <Icon name="x" width={16} height={16} />
        </button>
      </div>

      <form onSubmit={handleSubmit} className="px-6 py-5">
        {result && result.tone === "error" && (
          <div className="mb-4 flex items-start gap-2.5 rounded-xl border border-danger/30 bg-danger/5 px-4 py-3 text-sm text-danger">
            <Icon name="alert" width={16} height={16} className="mt-0.5 shrink-0" />
            <span>{result.message}</span>
          </div>
        )}

        <PasswordField
          id="currentPassword"
          label="Current password"
          autoComplete="current-password"
          required
          value={form.current}
          onChange={update("current")}
        />

        <div className="mt-1 mb-1 border-t border-dashed border-border pt-4">
          <PasswordField
            id="newPassword"
            label="New password"
            autoComplete="new-password"
            required
            minLength={8}
            showStrength
            value={form.next}
            onChange={update("next")}
            error={tooShort ? "Use at least 8 characters." : ""}
          />
          <PasswordField
            id="confirmNewPassword"
            label="Confirm new password"
            autoComplete="new-password"
            required
            value={form.confirm}
            onChange={update("confirm")}
            valid={Boolean(form.confirm) && !mismatch}
            error={mismatch ? "Passwords don't match." : ""}
          />
        </div>

        <div className="flex flex-wrap items-center gap-3 pt-2">
          <button
            type="submit"
            disabled={saving || !ready}
            className={`${btnPrimary.replace("px-5 py-3", "px-4 py-2.5")} text-sm disabled:opacity-50 disabled:pointer-events-none`}
          >
            {saving && <Icon name="spinner" width={15} height={15} className="animate-spin" />}
            {saving ? "Updating…" : "Update password"}
          </button>
          <button
            type="button"
            onClick={() => { setOpen(false); setForm({ current: "", next: "", confirm: "" }); setResult(null); }}
            className="text-sm font-semibold text-muted hover:text-ink transition-colors"
          >
            Cancel
          </button>
        </div>
      </form>
    </div>
  );
}
