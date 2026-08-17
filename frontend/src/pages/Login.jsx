import { useEffect, useRef, useState } from "react";
import { Link, Navigate, useLocation, useNavigate } from "react-router-dom";
import AuthLayout from "../components/AuthLayout.jsx";
import { TextField, PasswordField } from "../components/FormField.jsx";
import Icon from "../components/Icon.jsx";
import { btnPrimary } from "../lib/ui.js";
import { Api, ApiError } from "../lib/api.js";
import { setSession, isLoggedIn } from "../lib/auth.js";

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export default function Login() {
  const navigate = useNavigate();
  const location = useLocation();

  /**
   * Where to go after signing in.
   */
  const intended = typeof location.state?.from === "string"
    && location.state.from.startsWith("/")
    && !location.state.from.startsWith("//")
    ? location.state.from
    : "/dashboard";
  const [form, setForm] = useState({ email: "", password: "" });
  const [errors, setErrors] = useState({});
  // Only a field the person has actually left (or tried to submit through) shows its error.
  const [touched, setTouched] = useState({});
  const [formError, setFormError] = useState("");
  // Focus moves to the error when one appears. Without it a screen-reader user submits, hears
  // nothing, and is left at the bottom of a form with no indication that anything happened.
  const errorRef = useRef(null);
  const [loading, setLoading] = useState(false);
  // Defaults to OFF. It defaulted to true, with the reasoning that a person who never notices
  // the checkbox keeps today's behaviour rather than being logged out sooner.
  const [remember, setRemember] = useState(false);

  function computeErrors(values) {
    const next = {};
    if (!values.email.trim()) next.email = "Email is required.";
    else if (!EMAIL_RE.test(values.email.trim())) next.email = "Enter a valid email address.";
    if (!values.password) next.password = "Password is required.";
    return next;
  }

  function update(field) {
    return (e) => {
      const value = e.target.value;
      const nextForm = { ...form, [field]: value };
      setForm(nextForm);
      // Once a field has been touched, clear/update its error live as the person types, instead
      // of making them re-blur or re-submit to see that a fix landed.
      if (touched[field]) setErrors(computeErrors(nextForm));
    };
  }

  function handleBlur(field) {
    return () => {
      setTouched((t) => ({ ...t, [field]: true }));
      setErrors(computeErrors(form));
    };
  }

  // Maps what the backend can actually distinguish today onto specific
  // copy, rather than inventing outcomes (an "account locked" state, a
  // separate "examiner login") that don't exist server-side.
  function describeError(err) {
    if (err instanceof ApiError) {
      if (err.status === 0) return "Network error — check your connection and try again.";
      return err.message;
    }
    return "Something went wrong. Please try again.";
  }

  useEffect(() => {
    if (formError) errorRef.current?.focus();
  }, [formError]);

  if (isLoggedIn()) return <Navigate to={intended} replace />;

  async function handleSubmit(e) {
    e.preventDefault();
    setFormError("");
    setTouched({ email: true, password: true });
    const next = computeErrors(form);
    setErrors(next);
    if (Object.keys(next).length > 0) return;

    setLoading(true);
    try {
      const data = await Api.login(form.email.trim(), form.password);
      setSession(data, remember);
      navigate(intended, { replace: true });
    } catch (err) {
      setFormError(describeError(err));
    } finally {
      setLoading(false);
    }
  }

  return (
    <AuthLayout
      variant="login"
      eyebrow="Welcome back"
      title="Log in to Merit.Ai"
      subtitle="Enter your credentials to access your dashboard."
    >
      {formError && (
        <div
          ref={errorRef}
          tabIndex={-1}
          role="alert"
          aria-live="assertive"
          className="mb-5 flex items-start gap-2.5 rounded-xl border border-danger/30 bg-danger/5 px-4 py-3 text-sm text-danger animate-fade-in outline-none"
        >
          <Icon name="alert" width={16} height={16} className="mt-0.5 shrink-0" />
          <span>{formError}</span>
        </div>
      )}

      <form onSubmit={handleSubmit} noValidate>
        <TextField
          id="email"
          label="Email"
          icon="mail"
          type="email"
          autoComplete="email"
          autoFocus
          placeholder="you@example.com"
          value={form.email}
          onChange={update("email")}
          onBlur={handleBlur("email")}
          error={touched.email ? errors.email : undefined}
          valid={touched.email && !errors.email && form.email.trim() !== ""}
        />

        <PasswordField
          id="password"
          label="Password"
          // Shares the label's row instead of trailing the whole form, where it read as an
          // afterthought rather than the second thing a person needs right when they're about
          // to type a password they've forgotten.
          labelExtra={
            <Link to="/forgot-password" className="text-xs font-semibold text-primary hover:underline">
              Forgot password?
            </Link>
          }
          autoComplete="current-password"
          value={form.password}
          onChange={update("password")}
          onBlur={handleBlur("password")}
          error={touched.password ? errors.password : undefined}
          className="!mb-3"
        />

        {/* Optional, and off by nobody's default expectation -- this is an
            exam platform that may well be opened on a lab or library machine
            between candidates, so staying signed in has to be something a
            person opts into, not something that just happens. */}
        <label className="flex items-start gap-2.5 mb-6 text-sm text-ink cursor-pointer select-none">
          <input
            type="checkbox"
            checked={remember}
            onChange={(e) => setRemember(e.target.checked)}
            className="mt-0.5 w-4 h-4 rounded border-border text-primary focus:ring-primary/30 focus:ring-offset-0 shrink-0"
          />
          <span>
            Keep me signed in
            <span className="block text-xs text-muted">
              Leave this off on a shared or lab computer.
            </span>
          </span>
        </label>

        {/* py-4 rather than the shared btnPrimary's default py-3 -- a
            per-instance override (the same pattern used throughout the
            dashboards) so the auth pages' primary action hits the
            52-56px target height without resizing every button sitewide
            that shares this constant. */}
        <button
          type="submit"
          disabled={loading}
          className={`${btnPrimary.replace("px-5 py-3", "px-5 py-4")} w-full group ${loading ? "opacity-70 pointer-events-none" : ""}`}
        >
          {loading && <Icon name="spinner" width={16} height={16} className="animate-spin" />}
          {loading ? "Authenticating…" : (
            <>
              Log In
              <Icon name="arrow" width={16} height={16} className="transition-transform duration-200 group-hover:translate-x-1" />
            </>
          )}
        </button>

        {/* Close to the button it follows on from, not stranded at the very
            bottom of the screen -- "did I sign up already?" is a question
            that comes up right after failing (or before attempting) to log
            in, not after scrolling away from the form entirely. */}
        <p className="mt-4 text-center text-sm text-muted">
          New to Merit.Ai?{" "}
          <Link to="/register" className="font-semibold text-primary hover:underline">
            Create a student account
          </Link>
        </p>

        {/* One small, factual line -- not a row of padlock badges. It sits
            directly under the button and the signup line, in the form's own
            flow, rather than in AuthLayout's page-level footer slot, which
            would strand it at the bottom of the viewport instead of the
            bottom of the card. */}
        <p className="mt-5 flex items-center justify-center gap-1.5 text-xs text-muted">
          <Icon name="lock" width={11} height={11} />
          {/* Was "Secure login • Your credentials are encrypted", which claimed
              something the code does not do. The password is HASHED at rest,
              which is a different (and stronger) property than encryption, and
              whether it is encrypted in transit depends entirely on the
              deployment having TLS configured -- the bundled proxy serves plain
              HTTP unless someone sets it up. Claiming "secure" on a page served
              over HTTP would be the least trustworthy sentence on the site. */}
          Your password is hashed and never stored in plain text
        </p>
      </form>
    </AuthLayout>
  );
}
