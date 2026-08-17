import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import RedirectToLogin from "../components/RedirectToLogin.jsx";
import DashboardHeader from "../components/DashboardHeader.jsx";
import CaptureCard from "../components/CaptureCard.jsx";
import { PhotoBox } from "../components/IdentityPhotoModal.jsx";
import Icon from "../components/Icon.jsx";
import { PasswordField } from "../components/FormField.jsx";
import { btnPrimary, btnGhost, sectionEyebrow } from "../lib/ui.js";
import { Api, ApiError } from "../lib/api.js";
import { clearMustChangePassword, isLoggedIn, getRole } from "../lib/auth.js";

export default function Profile() {
  // See the identical comment in Exam.jsx: the guard used to run before any hooks below, but
  // Api.get can clear the session on a 401 (api.js), which flips isLoggedIn() to false and
  // would change the hook count on the next render -- a Rules-of-Hooks crash.
  const [me, setMe] = useState(null);
  const [loadError, setLoadError] = useState("");

  const refresh = useCallback(async () => {
    try {
      setMe(await Api.get("/users/me"));
      setLoadError("");
    } catch (err) {
      setLoadError(err instanceof ApiError ? err.message : "Couldn't load your profile.");
    }
  }, []);

  useEffect(() => {
    // Guards against the one edge case the hook-order fix above introduces.
    if (isLoggedIn()) refresh();
  }, [refresh]);

  const faceDone = Boolean(me?.face_registered);
  const idDone = Boolean(me?.id_verified);
  const locked = Boolean(me?.identity_locked);

  if (!isLoggedIn()) return <RedirectToLogin />;

  // Examiners and admins get this page too. Identity verification is student-only.
  const isStudent = getRole() === "student";
  const isExaminer = getRole() === "examiner";
  const isAdmin = getRole() === "admin";

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
              : isAdmin
                ? "Review your account details and manage the password you use to sign in."
                : "Review your account details. Your password is managed by your administrator."}
          </p>
        </div>

        {loadError && (
          <div role="alert"
               className="flex flex-wrap items-start gap-2.5 rounded-xl border border-danger/30 bg-danger/5 px-4 py-3 text-sm text-danger">
            <Icon name="alert" width={16} height={16} className="mt-0.5 shrink-0" />
            <span className="flex-1 min-w-[12rem]">{loadError}</span>
            <button type="button" onClick={() => { setLoadError(""); refresh(); }}
                    className="font-semibold underline underline-offset-2 hover:no-underline">
              Try again
            </button>
          </div>
        )}

        {isStudent && me?.reverification_required && (
          <div role="status" className="rounded-2xl border border-warning/40 bg-warning/5 p-5">
            <div className="flex items-start gap-3">
              <span className="inline-flex items-center justify-center w-9 h-9 rounded-xl bg-warning/15 text-warning shrink-0">
                <Icon name="shield-check" width={17} height={17} />
              </span>
              <div className="min-w-0">
                <p className="font-bold text-ink mb-1">Your institution has asked you to verify again</p>
                <p className="text-sm text-muted leading-relaxed mb-2">
                  Complete <strong>both</strong> steps below again — register your face, and capture
                  your ID card. Doing only one will not restore your access.
                </p>
                {me.reverification_reason && (
                  <p className="text-sm text-ink bg-page border border-border rounded-lg px-3 py-2">
                    <span className="font-semibold">Reason given:</span> {me.reverification_reason}
                  </p>
                )}
              </div>
            </div>
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

            {/* Shown only once something is actually on file. A pair of empty
                boxes before the student has captured anything would read as a
                broken feature rather than an empty state -- the capture cards
                above are already the empty state. */}
            {(faceDone || idDone) && (
              <VerifiedPhotos studentId={me?.student_id} faceDone={faceDone} idDone={idDone} />
            )}

            {/* Shown regardless of whether anything is captured yet: "nothing
                is held about you" is itself information a person is entitled
                to see, and hiding the panel until data exists would mean the
                only way to learn what is collected is to hand it over first. */}
            <BiometricData onErased={refresh} />
          </section>
        )}

        <section className="flex flex-col gap-5">
          <SectionHeading
            icon="lock"
            title="Sign-in & security"
            description="The password you use to log in to Merit.Ai."
          />
          {isAdmin ? (
            <ChangePasswordCard />
          ) : (
            // Says who to ask AND offers the self-service route, so this reads
            // as "handled elsewhere" rather than "you are stuck".
            <div className="rounded-2xl border border-border bg-surface shadow-card p-6">
              <p className="text-sm text-ink font-semibold mb-1.5">Your password is managed by your administrator</p>
              <p className="text-sm text-muted leading-relaxed">
                Passwords on Merit.Ai are issued and rotated centrally. If you&apos;ve forgotten yours, you can set a new
                one yourself using a code sent to <strong className="text-ink">{me?.email}</strong> — no need to wait
                for an administrator.
              </p>
              <Link to="/forgot-password" className={`${btnGhost.replace("px-5 py-3", "px-4 py-2.5")} text-sm mt-4 inline-flex`}>
                Reset my password by email
              </Link>
            </div>
          )}
        </section>
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

/**
 * The student's own verified face and ID card.
 */
/**
 * See what biometric data is held, and delete it.
 */
function BiometricData({ onErased }) {
  const [status, setStatus] = useState(null);
  const [state, setState] = useState("loading");
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");

  const load = useCallback(() => {
    let cancelled = false;
    setState("loading");
    Api.get("/users/me/biometrics")
      .then((data) => { if (!cancelled) { setStatus(data); setState("ready"); } })
      .catch((err) => {
        if (cancelled) return;
        setError(err instanceof ApiError ? err.message : "Couldn't load your biometric data.");
        setState("error");
      });
    return () => { cancelled = true; };
  }, []);

  useEffect(() => load(), [load]);

  async function erase() {
    setBusy(true);
    setError("");
    try {
      const res = await Api.del("/users/me/biometrics");
      setNotice(res?.message || "Your face and ID-card data have been deleted.");
      setConfirming(false);
      load();
      await onErased?.();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't delete your data. Please try again.");
    } finally {
      setBusy(false);
    }
  }

  const held = [];
  if (status?.face?.captured) held.push("your registered face photo and its face-match data");
  if (status?.id_card?.captured) held.push("the photo of your ID card");

  return (
    <div className="rounded-2xl border border-border bg-surface shadow-card p-6">
      <h3 className="text-base font-bold text-ink mb-1">Your biometric data</h3>
      <p className="text-sm text-muted leading-relaxed mb-5">
        What Merit.Ai holds to verify you during proctored exams, and your right to have it removed.
      </p>

      {state === "loading" && <div className="h-20 rounded-xl bg-border/30 animate-pulse" aria-hidden="true" />}

      {state === "error" && (
        <div role="alert" className="rounded-xl border border-border bg-page px-4 py-3 text-sm text-ink">
          <p className="mb-2">{error}</p>
          <button type="button" onClick={load} className={`${btnGhost} px-3 py-1.5 text-sm`}>Try again</button>
        </div>
      )}

      {state === "ready" && (
        <>
          {held.length === 0 ? (
            <p className="rounded-xl border border-border bg-page px-4 py-3 text-sm text-ink">
              {status?.erased_at
                ? "Your biometric data has been deleted. You'll need to register your face again before your next proctored exam."
                : "Nothing on file yet. Data is only collected when you register your face or verify your ID above."}
            </p>
          ) : (
            <>
              <ul className="mb-4 space-y-1.5 text-sm text-ink">
                {held.map((item) => (
                  <li key={item} className="flex items-start gap-2">
                    <Icon name="shield-check" width={14} height={14} className="mt-1 shrink-0 text-muted" />
                    <span>{item}</span>
                  </li>
                ))}
              </ul>
              {/* Surfaced because a stale consent version is exactly the thing a
                  person would want to know before deciding whether to keep the
                  data on file: it means what they agreed to is no longer what
                  currently applies. */}
              {(status?.face?.stale || status?.id_card?.stale) && (
                <p className="mb-4 rounded-xl border border-warning/30 bg-warning/5 px-4 py-3 text-sm text-warning">
                  This was collected under an older version of the proctoring terms. Your institution
                  may ask you to re-consent before your next exam.
                </p>
              )}
            </>
          )}

          {notice && (
            <p role="status" className="mt-4 rounded-xl border border-success/30 bg-success/5 px-4 py-3 text-sm text-success">
              {notice}
            </p>
          )}
          {error && state === "ready" && (
            <p role="alert" className="mt-4 text-sm text-danger">{error}</p>
          )}

          {held.length > 0 && !confirming && (
            <button type="button" onClick={() => setConfirming(true)}
                    className={`${btnGhost} mt-5 px-4 py-2 text-sm !text-danger !border-danger/30 hover:!bg-danger/5`}>
              Delete my biometric data
            </button>
          )}

          {confirming && (
            <div className="mt-5 rounded-xl border border-danger/30 bg-danger/5 p-4">
              <p className="text-sm font-semibold text-ink mb-1">Delete your face and ID data?</p>
              <p className="text-sm text-muted leading-relaxed mb-4">
                Your exam results, attempts and proctoring history are <strong>not</strong> deleted —
                those are assessment records, not biometric data. You will need to register your face
                again before your next proctored exam.
              </p>
              <div className="flex flex-wrap gap-2">
                <button type="button" onClick={erase} disabled={busy}
                        className={`${btnPrimary.replace("px-5 py-3", "px-4 py-2")} text-sm !bg-danger disabled:opacity-60`}>
                  {busy ? "Deleting…" : "Yes, delete it"}
                </button>
                <button type="button" onClick={() => setConfirming(false)} disabled={busy}
                        className={`${btnGhost} px-4 py-2 text-sm`}>
                  Keep my data
                </button>
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}


function VerifiedPhotos({ studentId, faceDone, idDone }) {
  if (!studentId) return null;
  return (
    <div className="rounded-2xl border border-border bg-surface shadow-card p-6">
      <div className="flex items-start justify-between gap-3 mb-1">
        <h3 className="text-base font-bold text-ink">Your verified photos</h3>
        <span className="inline-flex items-center gap-1.5 text-xs font-semibold text-success shrink-0">
          <Icon name="shield-check" width={14} height={14} />
          On file
        </span>
      </div>
      <p className="text-sm text-muted leading-relaxed mb-5">
        This is what Merit.Ai matches you against during a proctored exam. Only you, your examiner and an
        administrator can see these.
      </p>
      <div className="flex flex-col sm:flex-row gap-4">
        {faceDone && (
          <PhotoBox label="Registered face" path={`/proctoring/face/photo/${studentId}`} active />
        )}
        {idDone && (
          <PhotoBox label="ID card" path={`/proctoring/id-card/photo/${studentId}`} active />
        )}
      </div>
    </div>
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
      // The password is now one only they know, so the
      // "an administrator set this" banner must stop.
      clearMustChangePassword();
    } catch (err) {
      setResult({ tone: "error", message: err instanceof ApiError ? err.message : "Couldn't update your password." });
    } finally {
      setSaving(false);
    }
  }

  // Collapsed by default. Changing a password is a rare, deliberate act.
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
            <p className="text-xs text-muted">Enter your current password to confirm it&apos;s you.</p>
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
