import { useState } from "react";
import { Link, Navigate, useNavigate } from "react-router-dom";
import AuthLayout from "../components/AuthLayout.jsx";
import { TextField, PasswordField } from "../components/FormField.jsx";
import CaptureCard from "../components/CaptureCard.jsx";
import Icon from "../components/Icon.jsx";
import { btnPrimary, btnGhost } from "../lib/ui.js";
import { Api, ApiError } from "../lib/api.js";
import { setSession, isLoggedIn } from "../lib/auth.js";

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

const STEPS = [
  { n: 1, label: "Account details" },
  { n: 2, label: "Identity verification" },
];

/** The 1 ── 2 progress rail above both steps. A plain row of text would say
 * the same thing, but this is the one place in signup that visually promises
 * "there are two short steps, not one long form" -- worth a few lines of its
 * own markup to make good on. */
function StepIndicator({ current }) {
  return (
    <div className="flex items-center gap-3 mb-7" aria-label={`Step ${current} of ${STEPS.length}`}>
      {STEPS.map((step, i) => {
        const done = step.n < current;
        const active = step.n === current;
        return (
          <div key={step.n} className="flex items-center gap-3 flex-1 last:flex-initial">
            <div className="flex items-center gap-2 shrink-0">
              <span
                className={`inline-flex items-center justify-center w-6 h-6 rounded-full text-xs font-bold shrink-0 transition-colors ${
                  done ? "bg-success text-white" : active ? "bg-primary text-white" : "border border-border text-muted"
                }`}
              >
                {done ? <Icon name="check" width={11} height={11} /> : step.n}
              </span>
              <span className={`text-xs font-semibold whitespace-nowrap ${active ? "text-ink" : done ? "text-muted" : "text-muted/60"}`}>
                {step.label}
              </span>
            </div>
            {i < STEPS.length - 1 && (
              <span aria-hidden="true" className={`h-px flex-1 ${done ? "bg-success" : "bg-border"}`} />
            )}
          </div>
        );
      })}
    </div>
  );
}

export default function Register() {
  const navigate = useNavigate();
  const [step, setStep] = useState(1);
  const [form, setForm] = useState({
    firstName: "", lastName: "", email: "", studentId: "", password: "", confirmPassword: "",
    agreeTerms: false, agreeProctoring: false,
  });
  const [errors, setErrors] = useState({});
  // Only a field the person has actually left (or tried to submit through)
  // shows its error -- see the identical comment in Login.jsx for why.
  const [touched, setTouched] = useState({});
  const [formError, setFormError] = useState("");
  const [loading, setLoading] = useState(false);
  const [faceDone, setFaceDone] = useState(false);
  const [idDone, setIdDone] = useState(false);

  // step === 1 guards this rather than isLoggedIn() alone: step 1's own
  // submit handler calls setSession() and then moves to step 2 *without*
  // navigating away, so an unconditional redirect-when-logged-in would fire
  // the instant that happens and yank someone out of step 2 before they ever
  // saw it. A reload during step 2 does still land on /dashboard -- step
  // resets to 1 on remount, isLoggedIn() is now true, and this guard sends
  // them onward -- which is an acceptable fallback (identity verification
  // was always resumable from Profile) rather than a bug worth the
  // complexity of persisting wizard position across a reload.
  //
  // Placed after every hook above (not as an early return before them) for
  // the same Rules-of-Hooks reason documented in Profile.jsx and Exam.jsx.
  if (step === 1 && isLoggedIn()) return <Navigate to="/dashboard" replace />;

  function computeErrors(values) {
    const next = {};
    // Name is validated a little more strictly than a generic text field
    // because it is matched against the student's ID card during
    // verification, and becomes immutable once that match succeeds.
    if (!values.firstName.trim()) next.firstName = "First name is required.";
    if (!values.lastName.trim()) next.lastName = "Last name is required.";
    if (!values.email.trim()) next.email = "Email is required.";
    else if (!EMAIL_RE.test(values.email.trim())) next.email = "Enter a valid email address.";
    if (values.password.length < 8) next.password = "Use at least 8 characters.";
    if (values.confirmPassword !== values.password) next.confirmPassword = "Passwords don't match.";
    return next;
  }

  function update(field) {
    return (e) => {
      const value = e.target.value;
      const nextForm = { ...form, [field]: value };
      setForm(nextForm);
      // Typing in "password" can also fix/break "confirm password"'s error,
      // so re-check both together whenever either has been touched already.
      if (touched[field] || (field === "password" && touched.confirmPassword)) {
        setErrors(computeErrors(nextForm));
      }
    };
  }

  function handleBlur(field) {
    return () => {
      setTouched((t) => ({ ...t, [field]: true }));
      setErrors(computeErrors(form));
    };
  }

  function describeError(err) {
    if (err instanceof ApiError) {
      if (err.status === 0) return "Network error — check your connection and try again.";
      return err.message;
    }
    return "Something went wrong. Please try again.";
  }

  async function handleSubmit(e) {
    e.preventDefault();
    setFormError("");
    setTouched({ firstName: true, lastName: true, email: true, password: true, confirmPassword: true });
    const next = computeErrors(form);
    setErrors(next);
    if (Object.keys(next).length > 0) return;
    if (!form.agreeTerms || !form.agreeProctoring) {
      setFormError("Please agree to both statements below to create your account.");
      return;
    }

    setLoading(true);
    try {
      const data = await Api.registerStudent({
        first_name: form.firstName.trim(),
        last_name: form.lastName.trim(),
        email: form.email.trim(),
        password: form.password,
        roll_number: form.studentId.trim() || null,
      });
      setSession(data);
      // Straight into step 2 rather than /dashboard -- the account exists
      // and the person is signed in, but identity verification is the
      // second half of "signing up" on a proctored platform, not a separate
      // errand for later. They can still skip it below and finish from
      // Profile whenever they're ready.
      setStep(2);
    } catch (err) {
      setFormError(describeError(err));
    } finally {
      setLoading(false);
    }
  }

  if (step === 2) {
    return (
      <AuthLayout
        variant="register"
        eyebrow="One more step"
        title="Verify your identity"
        subtitle="Two quick captures -- required before your first proctored exam, but you can finish them later from Profile if you'd rather do this now and that later."
      >
        <StepIndicator current={2} />

        <div className="flex flex-col gap-5">
          <StepBadgeCard number={1} done={faceDone}>
            <CaptureCard
              title="Face Registration"
              description="This photo is used to verify your identity during exams."
              mirrored
              captureLabel="Capture Photo"
              submitLabel="Register Face"
              onSubmit={async (image) => {
                const res = await Api.post("/proctoring/face/register", { image_base64: image });
                setFaceDone(true);
                return res;
              }}
              formatResult={(res) => ({ tone: "success", message: res.message })}
            />
          </StepBadgeCard>

          <StepBadgeCard number={2} done={idDone}>
            <CaptureCard
              title="ID Card Verification"
              description="Capture your student/government ID card. We'll read the name and compare it to your registered name."
              captureLabel="Capture ID Card"
              submitLabel="Verify ID"
              onSubmit={async (image) => {
                const res = await Api.post("/proctoring/id-card/verify", { image_base64: image });
                if (res.name_matched) setIdDone(true);
                return res;
              }}
              formatResult={(res) => ({ tone: res.name_matched ? "success" : "error", message: res.message })}
            />
          </StepBadgeCard>
        </div>

        <div className="flex flex-col sm:flex-row gap-3 mt-7">
          <button
            type="button"
            onClick={() => navigate("/dashboard")}
            className={`${btnGhost.replace("px-5 py-3", "px-5 py-4")} flex-1 justify-center`}
          >
            Skip for now
          </button>
          <button
            type="button"
            onClick={() => navigate("/dashboard")}
            className={`${btnPrimary.replace("px-5 py-3", "px-5 py-4")} flex-1 justify-center`}
          >
            {faceDone && idDone ? "Continue to Dashboard" : "Finish later, go to Dashboard"}
            <Icon name="arrow" width={15} height={15} />
          </button>
        </div>
        {!(faceDone && idDone) && (
          <p className="mt-3 text-center text-xs text-muted">
            You can complete whichever step you skipped anytime from your Profile page.
          </p>
        )}
      </AuthLayout>
    );
  }

  return (
    <AuthLayout
      variant="register"
      eyebrow="Get started"
      title="Create your student account"
      subtitle="Free to join — start taking proctored exams in minutes."
      footer={
        <>
          Already have an account?{" "}
          <Link to="/login" className="font-semibold text-primary hover:underline">
            Log in
          </Link>
        </>
      }
    >
      <StepIndicator current={1} />

      {formError && (
        <div className="mb-5 flex items-start gap-2.5 rounded-xl border border-danger/30 bg-danger/5 px-4 py-3 text-sm text-danger animate-fade-in">
          <Icon name="alert" width={16} height={16} className="mt-0.5 shrink-0" />
          <span>{formError}</span>
        </div>
      )}

      <form onSubmit={handleSubmit} noValidate>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-4">
          <TextField
            id="firstName"
            label="First name"
            icon="user"
            autoComplete="given-name"
            autoFocus
            placeholder="Jane"
            value={form.firstName}
            onChange={update("firstName")}
            onBlur={handleBlur("firstName")}
            error={touched.firstName ? errors.firstName : undefined}
            valid={touched.firstName && !errors.firstName && form.firstName.trim() !== ""}
          />
          <TextField
            id="lastName"
            label="Last name"
            icon="user"
            autoComplete="family-name"
            placeholder="Doe"
            value={form.lastName}
            onChange={update("lastName")}
            onBlur={handleBlur("lastName")}
            error={touched.lastName ? errors.lastName : undefined}
            valid={touched.lastName && !errors.lastName && form.lastName.trim() !== ""}
          />
        </div>
        {/* Shortened from a two-sentence paragraph -- the full rationale
            (why it's strict, what "locked" means) now lives in the info
            icon's tooltip for anyone who wants it, instead of being forced
            on everyone as a permanent block of text under the name fields. */}
        <p className="-mt-1 mb-4 text-xs text-muted leading-relaxed inline-flex items-start gap-1">
          Enter your name exactly as shown on your ID. It can't be edited after verification.
          <span title="Your name is matched against the ID card you'll verify in the next step. Once that match succeeds, your name and email are locked and an administrator would need to change them." className="cursor-help text-muted/70 shrink-0 mt-0.5">
            <Icon name="alert" width={12} height={12} />
          </span>
        </p>
        <TextField
          id="email"
          label="Email"
          icon="mail"
          type="email"
          autoComplete="email"
          placeholder="you@example.com"
          value={form.email}
          onChange={update("email")}
          onBlur={handleBlur("email")}
          error={touched.email ? errors.email : undefined}
          valid={touched.email && !errors.email && form.email.trim() !== ""}
        />
        <TextField
          id="studentId"
          label={
            <>
              Student or candidate ID <span className="font-normal text-muted">· Optional</span>
            </>
          }
          icon="hash"
          autoComplete="off"
          placeholder="e.g. 21CS042"
          value={form.studentId}
          onChange={update("studentId")}
        />
        <PasswordField
          id="password"
          label="Password"
          autoComplete="new-password"
          value={form.password}
          onChange={update("password")}
          onBlur={handleBlur("password")}
          error={touched.password ? errors.password : undefined}
          showStrength
        />
        <PasswordField
          id="confirmPassword"
          label="Confirm password"
          autoComplete="new-password"
          value={form.confirmPassword}
          onChange={update("confirmPassword")}
          onBlur={handleBlur("confirmPassword")}
          error={touched.confirmPassword ? errors.confirmPassword : undefined}
        />

        {/* Required consent -- the second checkbox specifically, since this
            platform collects face, ID, and webcam data during exams, which
            is meaningfully different from an ordinary signup and deserves
            its own explicit line rather than being folded into "I agree to
            the Terms." See Privacy.jsx for exactly what's collected. */}
        <div className="flex flex-col gap-2.5 mb-6 mt-2">
          <label className="flex items-start gap-2.5 text-sm text-ink cursor-pointer select-none">
            <input
              type="checkbox"
              checked={form.agreeTerms}
              onChange={(e) => setForm((f) => ({ ...f, agreeTerms: e.target.checked }))}
              className="mt-0.5 w-4 h-4 rounded border-border text-primary focus:ring-primary/30 focus:ring-offset-0 shrink-0"
            />
            <span>
              I agree to the{" "}
              <Link to="/privacy" target="_blank" className="font-semibold text-primary hover:underline">
                Terms of Service and Privacy Policy
              </Link>
              .
            </span>
          </label>
          <label className="flex items-start gap-2.5 text-sm text-ink cursor-pointer select-none">
            <input
              type="checkbox"
              checked={form.agreeProctoring}
              onChange={(e) => setForm((f) => ({ ...f, agreeProctoring: e.target.checked }))}
              className="mt-0.5 w-4 h-4 rounded border-border text-primary focus:ring-primary/30 focus:ring-offset-0 shrink-0"
            />
            <span>
              I understand that identity and proctoring data may be collected during exams.{" "}
              <Link to="/privacy" target="_blank" className="text-primary hover:underline">
                See what's collected
              </Link>
              .
            </span>
          </label>
        </div>

        <button
          type="submit"
          disabled={loading}
          className={`${btnPrimary.replace("px-5 py-3", "px-5 py-4")} w-full group ${loading ? "opacity-70 pointer-events-none" : ""}`}
        >
          {loading && <Icon name="spinner" width={16} height={16} className="animate-spin" />}
          {loading ? "Creating account…" : (
            <>
              Continue
              <Icon name="arrow" width={16} height={16} className="transition-transform duration-200 group-hover:translate-x-1" />
            </>
          )}
        </button>
      </form>
    </AuthLayout>
  );
}

/** Wraps a CaptureCard with a numbered step badge, same treatment as
 * Profile.jsx's identical StepCard -- kept as its own small copy here rather
 * than importing Profile's (unexported) version, since sharing it would mean
 * either exporting a component out of a page module or lifting it into
 * components/ for exactly one other caller. */
function StepBadgeCard({ number, done, children }) {
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
