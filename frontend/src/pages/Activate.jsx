import { useEffect, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import AuthLayout from "../components/AuthLayout.jsx";
import { PasswordField } from "../components/FormField.jsx";
import Icon from "../components/Icon.jsx";
import { btnPrimary } from "../lib/ui.js";
import { Api, ApiError } from "../lib/api.js";
import { setSession } from "../lib/auth.js";

/**
 * Choose the first password on an account somebody else created.
 *
 * This screen exists because the alternative it replaces was worse than it
 * looked. An approved examiner used to be emailed a password an administrator
 * had typed. That password then lived in a mailbox in plain text indefinitely,
 * was known to at least two people, and made "only this examiner could have
 * done that" untrue of everything the account subsequently did -- none of which
 * a "please change it immediately" line in the email actually fixes.
 *
 * The link that lands here carries a single-use token, stored server-side only
 * as a hash, that expires. The password ends up on the account without ever
 * having been transmitted or known to anybody but its owner.
 *
 * The link is checked BEFORE the form is shown. Letting somebody read the page,
 * think of a password, type it twice and only then be told the link expired
 * three days ago is a small cruelty that costs one extra request to avoid.
 */
export default function Activate() {
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const token = params.get("token") || "";
  const email = (params.get("email") || "").trim().toLowerCase();

  // "checking" -> "ready" | "invalid" | "unreachable"
  //
  // "invalid" and "unreachable" are kept apart deliberately. A dead link and a
  // server that cannot be reached call for opposite actions -- ask for a new
  // invitation, versus try again in a minute -- and collapsing them into one
  // "something went wrong" sends half the people down the wrong path.
  const [state, setState] = useState("checking");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [policy, setPolicy] = useState(null);
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const errorRef = useRef(null);

  useEffect(() => {
    if (error) errorRef.current?.focus();
  }, [error]);

  useEffect(() => {
    let cancelled = false;
    Api.get("/auth/password-policy")
      .then((res) => { if (!cancelled) setPolicy(res); })
      .catch(() => { /* the server still enforces it; this only affects the hint */ });
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    if (!token || !email) { setState("invalid"); return undefined; }

    let cancelled = false;
    Api.post("/auth/activate/check", { email, token })
      .then((res) => { if (!cancelled) setState(res?.valid ? "ready" : "invalid"); })
      .catch(() => { if (!cancelled) setState("unreachable"); });
    return () => { cancelled = true; };
  }, [token, email]);

  const minLength = policy?.min_length ?? 10;

  function validate() {
    if (password.length < minLength) return `Use at least ${minLength} characters.`;
    if (confirm !== password) return "Passwords don't match.";
    return "";
  }

  async function handleSubmit(e) {
    e.preventDefault();
    const problem = validate();
    setError(problem);
    if (problem) return;

    setSubmitting(true);
    try {
      const data = await Api.post("/auth/activate", { email, token, password });
      // Signed in immediately rather than bounced to the login form. They have
      // proved control of the mailbox and just chosen the password; asking them
      // to type it again establishes nothing.
      //
      // remember=false: this is very often a shared or institutional machine,
      // and the person has not been offered the choice on this screen. Opting
      // somebody into a persistent session they were never asked about is the
      // same mistake the login page's "Keep me signed in" default used to make.
      setSession(data, false);
      navigate(data.role === "admin" ? "/dashboard" : "/examiner", { replace: true });
    } catch (err) {
      setError(err instanceof ApiError
        ? err.message
        : "Couldn't activate the account. Please try again.");
      setSubmitting(false);
    }
  }

  if (state === "checking") {
    return (
      <AuthLayout variant="login" eyebrow="Almost there" title="Checking your link"
                  subtitle="One moment.">
        <div className="h-32 rounded-xl bg-border/30 animate-pulse" aria-hidden="true" />
        <p className="sr-only" role="status">Checking your activation link.</p>
      </AuthLayout>
    );
  }

  if (state === "unreachable") {
    return (
      <AuthLayout variant="login" eyebrow="Can't reach the server"
                  title="We couldn't check your link"
                  subtitle="Your link is probably fine — we just couldn't reach the server.">
        <div role="alert" className="rounded-xl border border-border bg-page px-4 py-3 text-sm text-ink">
          Check your connection and try again in a moment. Your link has not been used.
        </div>
        <button type="button" onClick={() => window.location.reload()}
                className={`${btnPrimary.replace("px-5 py-3", "px-5 py-4")} w-full mt-5`}>
          Try again
        </button>
      </AuthLayout>
    );
  }

  if (state === "invalid") {
    return (
      <AuthLayout variant="login" eyebrow="Link expired"
                  title="This activation link isn't valid"
                  subtitle="Links work once, and expire after a few days.">
        <div role="alert" className="flex items-start gap-2.5 rounded-xl border border-danger/30 bg-danger/5 px-4 py-3 text-sm text-danger">
          <Icon name="alert" width={16} height={16} className="mt-0.5 shrink-0" />
          <span>
            It may have already been used, or a newer invitation may have replaced it.
          </span>
        </div>
        {/* Password reset, not "ask your admin to resend". If the account
            exists, this reaches the same mailbox and achieves the same thing
            without anybody waiting on a human. And the reset endpoint is
            deliberately silent about whether an account exists, so pointing
            here leaks nothing either. */}
        <Link to="/forgot-password"
              className={`${btnPrimary.replace("px-5 py-3", "px-5 py-4")} w-full mt-5`}>
          Set a password by email instead
        </Link>
        <p className="mt-4 text-center text-sm text-muted">
          Already activated? <Link to="/login" className="font-semibold text-primary hover:underline">Log in</Link>
        </p>
      </AuthLayout>
    );
  }

  return (
    <AuthLayout
      variant="login"
      eyebrow="Welcome to Merit.Ai"
      title="Choose your password"
      subtitle={email ? `Setting up ${email}` : "Set the password for your new account."}
    >
      {error && (
        <div
          ref={errorRef}
          tabIndex={-1}
          role="alert"
          aria-live="assertive"
          className="mb-5 flex items-start gap-2.5 rounded-xl border border-danger/30 bg-danger/5 px-4 py-3 text-sm text-danger outline-none"
        >
          <Icon name="alert" width={16} height={16} className="mt-0.5 shrink-0" />
          <span>{error}</span>
        </div>
      )}

      <form onSubmit={handleSubmit} noValidate>
        <PasswordField
          id="password"
          label="New password"
          autoComplete="new-password"
          autoFocus
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          hint={policy?.rules?.length ? policy.rules.join(" ") : `At least ${minLength} characters.`}
        />
        <PasswordField
          id="confirm"
          label="Confirm password"
          autoComplete="new-password"
          value={confirm}
          onChange={(e) => setConfirm(e.target.value)}
        />

        <button
          type="submit"
          disabled={submitting}
          className={`${btnPrimary.replace("px-5 py-3", "px-5 py-4")} w-full mt-2 group ${submitting ? "opacity-70 pointer-events-none" : ""}`}
        >
          {submitting && <Icon name="spinner" width={16} height={16} className="animate-spin" />}
          {submitting ? "Activating…" : "Activate my account"}
        </button>

        <p className="mt-5 flex items-center justify-center gap-1.5 text-xs text-muted">
          <Icon name="lock" width={11} height={11} />
          Nobody else — including your administrator — will know this password
        </p>
      </form>
    </AuthLayout>
  );
}
