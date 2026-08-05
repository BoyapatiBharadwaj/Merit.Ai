import { Navigate, useLocation } from "react-router-dom";

/**
 * Sends an unauthenticated visitor to the login screen, remembering where they
 * were trying to go.
 *
 * Every guarded page used to render `<Navigate to="/login" replace />` directly.
 * That works, but it throws away the destination -- so a candidate who opened a
 * link to their exam, got bounced, and signed in landed on the dashboard and had
 * to find the exam again. On a timed assessment that is not a cosmetic problem.
 *
 * The path travels in router state rather than in the URL. A ?next= parameter is
 * attacker-controllable, which is the standard shape of an open redirect;
 * router state cannot be set by a link from another site. Login re-validates it
 * anyway (see the `intended` comment there) on the principle that the consumer
 * of a redirect target should never trust its provenance.
 *
 * This is a component, not a hook or a helper, so that callers can `return
 * <RedirectToLogin />` from inside a conditional without breaking the Rules of
 * Hooks -- useLocation runs in this component's own render, not the caller's.
 */
export default function RedirectToLogin({ to = "/login" }) {
  const location = useLocation();
  const from = `${location.pathname}${location.search}`;
  // Do not bounce back to an auth screen; that would loop.
  const safe = from.startsWith("/login") || from.startsWith("/register") ? undefined : from;
  return <Navigate to={to} state={safe ? { from: safe } : undefined} replace />;
}
