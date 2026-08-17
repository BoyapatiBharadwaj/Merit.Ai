import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import AuthLayout from "../components/AuthLayout.jsx";
import { TextField, PasswordField } from "../components/FormField.jsx";
import Icon from "../components/Icon.jsx";
import { btnPrimary, btnGhost } from "../lib/ui.js";
import { Api, ApiError } from "../lib/api.js";

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

/**
 * Self-service password reset, by emailed one-time code.
 */
export default function ForgotPassword() {
  const navigate = useNavigate();
  // request -> notfound | verify -> done "notfound" is its
  // own step, not just an inline error on "request".
  const [step, setStep] = useState("request");
  const [email, setEmail] = useState("");
  const [code, setCode] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [expiresIn, setExpiresIn] = useState(10);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [loading, setLoading] = useState(false);
  // Seconds until "Resend code" is available again,
  // mirroring the server's OTP_RESEND_COOLDOWN_SECONDS.
  const [cooldown, setCooldown] = useState(0);

  // The rules the SERVER enforces, fetched the same way Register.jsx does.
  const [policy, setPolicy] = useState(null);

  useEffect(() => {
    let cancelled = false;
    Api.get("/auth/password-policy")
      .then((res) => { if (!cancelled) setPolicy(res); })
      .catch(() => { /* the server still enforces it; this only affects the hint */ });
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    if (cooldown <= 0) return undefined;
    const timer = setTimeout(() => setCooldown((s) => s - 1), 1000);
    return () => clearTimeout(timer);
  }, [cooldown]);

  function describe(err) {
    if (err instanceof ApiError) {
      if (err.status === 0) return "Network error — check your connection and try again.";
      return err.message;
    }
    return "Something went wrong. Please try again.";
  }

  async function requestCode(e) {
    e?.preventDefault();
    setError("");
    if (!EMAIL_RE.test(email.trim())) {
      setError("Enter a valid email address.");
      return;
    }
    setLoading(true);
    try {
      // Check existence FIRST.
      const { exists } = await Api.post("/auth/password-reset/check-account", { email: email.trim() });
      if (!exists) {
        setStep("notfound");
        return;
      }

      const res = await Api.post("/auth/password-reset/request", { email: email.trim() });
      setExpiresIn(res.expires_in_minutes ?? 10);
      setCooldown(60);
      setNotice(res.message || "A reset code is on its way.");
      setStep("verify");
    } catch (err) {
      setError(describe(err));
    } finally {
      setLoading(false);
    }
  }

  const minLength = policy?.min_length ?? 10;

  async function submitReset(e) {
    e.preventDefault();
    setError("");
    if (!code.trim()) {
      setError("Enter the code from your email.");
      return;
    }
    if (password.length < minLength) {
      setError(`Your new password must be at least ${minLength} characters.`);
      return;
    }
    if (password !== confirm) {
      setError("Those passwords don't match.");
      return;
    }
    setLoading(true);
    try {
      await Api.post("/auth/password-reset/confirm", {
        email: email.trim(),
        code: code.trim(),
        new_password: password,
      });
      // Setting a new password stamps password_changed_at, which invalidates every access token
      // issued before this moment (see api/deps.py::_reject_if_password_changed).
      setStep("done");
    } catch (err) {
      setError(describe(err));
    } finally {
      setLoading(false);
    }
  }

  const alert = error ? (
    <div className="flex items-start gap-2.5 rounded-xl border border-danger/25 bg-danger/5 px-4 py-3 mb-4" role="alert">
      <span className="text-danger mt-0.5 shrink-0"><Icon name="alert" width={16} height={16} /></span>
      <p className="text-sm text-danger leading-relaxed">{error}</p>
    </div>
  ) : null;

  if (step === "done") {
    return (
      <AuthLayout
        variant="login"
        eyebrow="Account recovery"
        title="Password updated"
        subtitle="You can sign in with your new password now."
      >
        <div className="rounded-2xl border border-border bg-surface shadow-card p-6">
          <span className="inline-flex items-center justify-center w-11 h-11 rounded-xl bg-success/10 text-success mb-4">
            <Icon name="check" width={20} height={20} />
          </span>
          <h2 className="text-base font-bold text-ink mb-2">All set</h2>
          <p className="text-sm text-muted leading-relaxed">
            Your password has been changed. For your own security, sign out of any other device that was still
            signed in with the old one.
          </p>
        </div>

        <button type="button" onClick={() => navigate("/login")} className={`${btnPrimary} w-full justify-center mt-5`}>
          Go to Log In
        </button>
      </AuthLayout>
    );
  }

  if (step === "notfound") {
    return (
      <AuthLayout
        variant="login"
        eyebrow="Account recovery"
        title="Account not found"
        subtitle="There's no account for that email address on this server."
      >
        <div className="rounded-2xl border border-border bg-surface shadow-card p-6">
          <span className="inline-flex items-center justify-center w-11 h-11 rounded-xl bg-warning/10 text-warning mb-4">
            <Icon name="alert" width={20} height={20} />
          </span>
          <h2 className="text-base font-bold text-ink mb-2">No account for {email.trim()}</h2>
          <p className="text-sm text-muted leading-relaxed">
            Double-check the address, or create a new account if you haven&apos;t registered yet.
          </p>
        </div>

        <Link to="/register" className={`${btnPrimary} w-full justify-center mt-5`}>
          Create an account
        </Link>
        <button
          type="button"
          onClick={() => { setStep("request"); setError(""); }}
          className={`${btnGhost.replace("px-5 py-3", "px-5 py-4")} w-full justify-center mt-3`}
        >
          Try a different email
        </button>
      </AuthLayout>
    );
  }

  if (step === "verify") {
    return (
      <AuthLayout
        variant="login"
        eyebrow="Account recovery"
        title="Enter your code"
        subtitle={`We've sent a code to ${email.trim()}. It expires in ${expiresIn} minutes.`}
      >
        {notice && (
          <div className="flex items-start gap-2.5 rounded-xl border border-primary/20 bg-primary/5 px-4 py-3 mb-4">
            <span className="text-primary mt-0.5 shrink-0"><Icon name="mail" width={16} height={16} /></span>
            <p className="text-sm text-ink leading-relaxed">{notice}</p>
          </div>
        )}
        {alert}

        <form
          onSubmit={submitReset}
          noValidate
          className="rounded-2xl border border-border bg-surface shadow-card p-6"
        >
          <TextField
            id="reset-code"
            label="Verification code"
            icon="shield"
            value={code}
            onChange={(e) => setCode(e.target.value)}
            inputMode="numeric"
            // Lets iOS/Android surface the code straight from the SMS/mail notification instead
            // of making the person switch apps and retype it from memory.
            autoComplete="one-time-code"
            autoFocus
            placeholder="123456"
          />
          <PasswordField
            id="reset-password"
            label="New password"
            showStrength
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="new-password"
            // The server's actual rules, not a hard-coded "8 characters" --
            // see the `policy` fetch above.
            hint={policy?.rules?.join(" · ")}
          />
          <PasswordField
            id="reset-confirm"
            label="Confirm new password"
            value={confirm}
            onChange={(e) => setConfirm(e.target.value)}
            autoComplete="new-password"
          />
          <button type="submit" disabled={loading} className={`${btnPrimary} w-full justify-center`}>
            {loading ? "Updating…" : "Update password"}
          </button>
        </form>

        <div className="flex items-center justify-between gap-3 mt-4">
          <button
            type="button"
            onClick={requestCode}
            disabled={cooldown > 0 || loading}
            className="text-sm font-semibold text-primary disabled:text-muted disabled:cursor-not-allowed"
          >
            {cooldown > 0 ? `Resend code in ${cooldown}s` : "Resend code"}
          </button>
          <button
            type="button"
            onClick={() => { setStep("request"); setError(""); setNotice(""); setCode(""); }}
            className="text-sm font-semibold text-muted hover:text-ink"
          >
            Use a different email
          </button>
        </div>
      </AuthLayout>
    );
  }

  return (
    <AuthLayout
      variant="login"
      eyebrow="Account recovery"
      title="Forgot your password?"
      subtitle="Enter your email and we'll send you a code to set a new one."
    >
      {alert}

      <form
        onSubmit={requestCode}
        noValidate
        className="rounded-2xl border border-border bg-surface shadow-card p-6"
      >
        <TextField
          id="reset-email"
          label="Email address"
          icon="mail"
          type="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          autoComplete="email"
          placeholder="you@institution.edu"
        />
        <button type="submit" disabled={loading} className={`${btnPrimary} w-full justify-center`}>
          {loading ? "Sending…" : "Send reset code"}
        </button>
      </form>

      <p className="text-xs text-muted leading-relaxed mt-4 px-1">
        Examiner accounts are managed by your institution&apos;s administrator. If you&apos;re an examiner and the code
        doesn&apos;t arrive, contact them directly.
      </p>

      <Link to="/login" className={`${btnGhost.replace("px-5 py-3", "px-5 py-4")} w-full justify-center mt-5`}>
        <Icon name="chevron-left" width={15} height={15} />
        Back to Log In
      </Link>
    </AuthLayout>
  );
}
