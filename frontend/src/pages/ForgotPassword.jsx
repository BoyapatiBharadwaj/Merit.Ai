import { Link } from "react-router-dom";
import AuthLayout from "../components/AuthLayout.jsx";
import Icon from "../components/Icon.jsx";
import { btnGhost } from "../lib/ui.js";

/**
 * "Forgot password?" -- honest about what this platform can actually do
 * today rather than promising an email it cannot send.
 *
 * There is no automated password-reset flow anywhere in this backend: no
 * outbound email capability exists at all (checked directly against the
 * codebase, not assumed), and the only endpoint that can change a password
 * without knowing the current one is POST /users/{id}/reset-password, which
 * is restricted to admins -- not even an examiner can reset a student's own
 * password. Building a page that collects an email and says "check your
 * inbox" would be the exact misleading pattern already flagged elsewhere in
 * this app's access-request flow, just repeated somewhere new. This page
 * instead names the real, current process (an administrator resets it and
 * shares the new one, the same one-time-reveal flow already used when an
 * examiner account is created) so nobody is left waiting for an email that
 * is never going to arrive.
 */
export default function ForgotPassword() {
  return (
    <AuthLayout
      variant="login"
      eyebrow="Account recovery"
      title="Forgot your password?"
      subtitle="Merit.Ai doesn't send automated reset emails yet -- here's how to get back in."
    >
      <div className="rounded-2xl border border-border bg-surface shadow-card p-6">
        <span className="inline-flex items-center justify-center w-11 h-11 rounded-xl bg-primary/10 text-primary mb-4">
          <Icon name="shield" width={20} height={20} />
        </span>
        <h2 className="text-base font-bold text-ink mb-2">Ask your administrator to reset it</h2>
        <p className="text-sm text-muted leading-relaxed mb-4">
          Only an administrator can reset a password on Merit.Ai today, for students and examiners alike. Reach out to
          the administrator at your institution with the email address on your account -- they'll set a temporary
          password and share it with you directly.
        </p>
        <p className="text-sm text-muted leading-relaxed">
          Once you're back in, you can set a password only you know from your <strong className="text-ink">Profile</strong> page
          (students and administrators -- examiner passwords are managed by an administrator).
        </p>
      </div>

      <Link to="/login" className={`${btnGhost.replace("px-5 py-3", "px-5 py-4")} w-full justify-center mt-5`}>
        <Icon name="chevron-left" width={15} height={15} />
        Back to Log In
      </Link>
    </AuthLayout>
  );
}
