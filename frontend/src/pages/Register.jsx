import { useEffect, useState } from "react";
import { Link, Navigate, useNavigate } from "react-router-dom";
import AuthLayout from "../components/AuthLayout.jsx";
import { TextField, PasswordField } from "../components/FormField.jsx";
import Icon from "../components/Icon.jsx";
import { btnPrimary } from "../lib/ui.js";
import { Api, ApiError } from "../lib/api.js";
import { setSession, isLoggedIn } from "../lib/auth.js";
import { TERMS_VERSION } from "./Terms.jsx";

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export default function Register() {
  const navigate = useNavigate();
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

  // Email verification. Signup is two phases now: request a code, then create
  // the account with it. The account does NOT exist until the code checks out
  // (POST /auth/register/student/verified verifies BEFORE creating), so no
  // unverified rows can accumulate.
  //
  // `codeSent` doubles as the phase flag rather than a separate step counter --
  // there are only two states and one of them is "we have sent a code".
  const [codeSent, setCodeSent] = useState(false);
  const [code, setCode] = useState("");
  const [otpUnavailable, setOtpUnavailable] = useState(false);

  // Everything about the code comes from the server's own response rather than
  // being guessed here. The screen used to hard-code a six-digit placeholder, a
  // ten-minute expiry and a 60-second resend cooldown, none of which tracked the
  // settings they were describing -- so raising OTP_LENGTH would have left the
  // form telling candidates to enter six digits for an eight-digit code.
  const [codeShape, setCodeShape] = useState({ length: 6, expiresIn: 10, resendAfter: 60 });
  const [cooldown, setCooldown] = useState(0);

  // The rules the SERVER enforces. Previously this screen listed an uppercase
  // letter, a number and a special character while the backend checked only
  // length -- instructions describing a policy nothing implemented.
  const [policy, setPolicy] = useState(null);

  useEffect(() => {
    if (cooldown <= 0) return undefined;
    const timer = setTimeout(() => setCooldown((s) => s - 1), 1000);
    return () => clearTimeout(timer);
  }, [cooldown]);

  useEffect(() => {
    let cancelled = false;
    Api.get("/auth/password-policy")
      .then((res) => { if (!cancelled) setPolicy(res); })
      .catch(() => { /* the server still enforces it; this only affects the hint */ });
    return () => { cancelled = true; };
  }, []);

  // Signup is now a single step, so an already-signed-in visitor is simply
  // sent onward -- there is no longer a second wizard stage this could yank
  // someone out of mid-flow.
  //
  // Placed after every hook above (not as an early return before them) for
  // the same Rules-of-Hooks reason documented in Profile.jsx and Exam.jsx.
  if (isLoggedIn()) return <Navigate to="/dashboard" replace />;

  function computeErrors(values) {
    const next = {};
    // Name is validated a little more strictly than a generic text field
    // because it is matched against the student's ID card during
    // verification, and becomes immutable once that match succeeds.
    if (!values.firstName.trim()) next.firstName = "First name is required.";
    if (!values.lastName.trim()) next.lastName = "Last name is required.";
    if (!values.email.trim()) next.email = "Email is required.";
    else if (!EMAIL_RE.test(values.email.trim())) next.email = "Enter a valid email address.";
    else if (values.email.trim().length > 150) next.email = "That email address is too long.";
    // Mirrors the server's floor rather than a number picked here. The server
    // does the real checking (blocklist, name/email derivation) and its message
    // is shown verbatim if it refuses -- this is only the immediate hint.
    const minLength = policy?.min_length ?? 10;
    if (values.password.length < minLength) next.password = `Use at least ${minLength} characters.`;
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

    // A LOCAL copy, not the state variable.
    //
    // The bug this replaces: the 503 branch below called setOtpUnavailable(true)
    // and the very next statement read `otpUnavailable` to decide which endpoint
    // to call. React state updates are asynchronous, so that read still saw
    // `false` -- the fallback called the verified endpoint again, failed again,
    // and the candidate had to submit a second time for the fallback to take
    // effect. Whether the fallback works cannot depend on a re-render that has
    // not happened yet.
    let useUnverifiedSignup = otpUnavailable;

    setLoading(true);
    try {
      // Phase 1: ask for a code, if we haven't already.
      if (!codeSent && !useUnverifiedSignup) {
        try {
          const res = await Api.post("/auth/otp/signup/request", { email: form.email.trim() });
          setCodeShape({
            length: res.code_length ?? 6,
            expiresIn: res.expires_in_minutes ?? 10,
            resendAfter: res.resend_after_seconds ?? 60,
          });
          setCooldown(res.resend_after_seconds ?? 60);
          setCodeSent(true);
          return;
        } catch (err) {
          // 503 means this deployment has no mail configured. Falling back to
          // unverified signup rather than blocking the person entirely: the
          // server is the authority on whether email works, and a candidate
          // should not be locked out of an exam platform by its SMTP settings.
          //
          // If the server REQUIRES verification it refuses that fallback with a
          // 403, which surfaces as a normal error -- the client asking nicely
          // does not decide whether verification is optional.
          if (err instanceof ApiError && err.status === 503) {
            useUnverifiedSignup = true;
            setOtpUnavailable(true);
          } else {
            throw err;
          }
        }
      }

      if (!useUnverifiedSignup && code.trim().length !== codeShape.length) {
        setErrors((prev) => ({ ...prev, code: `Enter the ${codeShape.length}-digit code we emailed you.` }));
        setTouched((t) => ({ ...t, code: true }));
        return;
      }

      // Phase 2: create the account. The verified endpoint checks the code
      // first, so a wrong code never leaves a half-made account behind.
      const payload = {
        first_name: form.firstName.trim(),
        last_name: form.lastName.trim(),
        email: form.email.trim(),
        password: form.password,
        roll_number: form.studentId.trim() || null,
        // Sent, not merely checked in the browser. Both checkboxes were
        // enforced only in React, so a direct API call registered without
        // agreeing to anything, and nothing recorded the agreement of the
        // people who did tick them.
        accepted_terms: form.agreeTerms,
        accepted_proctoring: form.agreeProctoring,
        terms_version: TERMS_VERSION,
      };
      const data = useUnverifiedSignup
        ? await Api.registerStudent(payload)
        : await Api.post("/auth/register/student/verified", { ...payload, code: code.trim() });
      setSession(data);
      // Straight to the dashboard. Identity verification used to be a second
      // signup step, which put a camera prompt in front of someone who had not
      // yet seen the product and could not yet know whether they needed an
      // account at all. It now lives on Profile, where the candidate chooses
      // the moment -- and attempt_service still refuses to start a proctored
      // exam until it is done, so nothing about the identity guarantee is
      // weakened by moving where it is collected.
      navigate("/dashboard", { replace: true });
    } catch (err) {
      setFormError(describeError(err));
    } finally {
      setLoading(false);
    }
  }

  async function resendCode() {
    setFormError("");
    try {
      const res = await Api.post("/auth/otp/signup/request", { email: form.email.trim() });
      setCodeShape({
        length: res.code_length ?? codeShape.length,
        expiresIn: res.expires_in_minutes ?? codeShape.expiresIn,
        resendAfter: res.resend_after_seconds ?? codeShape.resendAfter,
      });
      setCooldown(res.resend_after_seconds ?? codeShape.resendAfter);
      setCode("");
    } catch (err) {
      setFormError(describeError(err));
    }
  }

  /** Back to the email step, discarding everything tied to the old address. */
  function changeEmail() {
    // A code is bound to ONE address. Leaving the email editable while a code
    // was outstanding meant a candidate could change it and then submit the old
    // address's code against the new one -- a confusing rejection for something
    // that looked like it should work. The field is disabled while a code is
    // live, and this is the deliberate way back.
    setCodeSent(false);
    setCode("");
    setCooldown(0);
    setFormError("");
    setErrors((prev) => ({ ...prev, code: undefined }));
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
          <span title="Your name is matched against the ID card you verify from your Profile page before your first exam. Once that match succeeds, your name and email are locked and an administrator would need to change them." className="cursor-help text-muted/70 shrink-0 mt-0.5">
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
          maxLength={150}
          // Locked while a code is outstanding: the code belongs to this
          // address, and editing the field would leave the candidate submitting
          // the old address's code against a new one. "Change email" below
          // resets the whole verification state deliberately.
          disabled={codeSent}
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
          // The rules as the SERVER states them, fetched from
          // /auth/password-policy. This used to be a hard-coded list promising
          // uppercase, a number and a special character, while the backend
          // enforced eight characters and nothing else -- so the form asked for
          // one thing and accepted another.
          hint={policy?.rules?.join(" · ")}
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
              <Link to="/terms" target="_blank" className="font-semibold text-primary hover:underline">
                Terms of Service
              </Link>{" "}
              and the{" "}
              <Link to="/privacy" target="_blank" className="font-semibold text-primary hover:underline">
                Privacy Policy
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

        {codeSent && (
          <div className="rounded-xl border border-primary/25 bg-primary/5 px-4 py-4 mb-5">
            <div className="flex items-start gap-2.5 mb-3">
              <span className="text-primary mt-0.5 shrink-0"><Icon name="mail" width={16} height={16} /></span>
              <p className="text-sm text-ink leading-relaxed">
                We've sent a {codeShape.length}-digit code to <strong>{form.email.trim()}</strong>.
                It expires in {codeShape.expiresIn} minutes.
              </p>
            </div>
            <TextField
              id="signup-code"
              label="Verification code"
              icon="shield"
              value={code}
              // Strip non-digits as they are typed and cap at the server's own
              // length. The field previously accepted any characters and any
              // length, so a pasted code with a stray space, or one digit too
              // many, produced a Pydantic validation message rather than
              // anything the candidate could act on. Slicing rather than
              // rejecting also makes pasting the whole code work.
              onChange={(e) => {
                setCode(e.target.value.replace(/\D/g, "").slice(0, codeShape.length));
                if (errors.code) setErrors((prev) => ({ ...prev, code: undefined }));
              }}
              inputMode="numeric"
              pattern="[0-9]*"
              maxLength={codeShape.length}
              autoComplete="one-time-code"
              autoFocus
              placeholder={"0".repeat(codeShape.length)}
              error={touched.code ? errors.code : undefined}
              className="mb-0"
            />
            <div className="flex items-center justify-between gap-3 mt-3">
              <button type="button" onClick={resendCode} disabled={cooldown > 0}
                      className="text-xs font-semibold text-primary disabled:text-muted disabled:cursor-not-allowed">
                {cooldown > 0 ? `Resend in ${cooldown}s` : "Resend code"}
              </button>
              <button type="button" onClick={changeEmail}
                      className="text-xs font-semibold text-muted hover:text-ink">
                Change email
              </button>
            </div>
          </div>
        )}

        <button
          type="submit"
          disabled={loading}
          className={`${btnPrimary.replace("px-5 py-3", "px-5 py-4")} w-full group ${loading ? "opacity-70 pointer-events-none" : ""}`}
        >
          {loading && <Icon name="spinner" width={16} height={16} className="animate-spin" />}
          {loading
            ? (codeSent || otpUnavailable ? "Creating account…" : "Sending code…")
            : (
              <>
                {codeSent ? "Verify & create account" : "Continue"}
                <Icon name="arrow" width={16} height={16} className="transition-transform duration-200 group-hover:translate-x-1" />
              </>
            )}
        </button>
      </form>
    </AuthLayout>
  );
}

