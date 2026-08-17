import { Navigate, useLocation } from "react-router-dom";

/**
 * Sends an unauthenticated visitor to the login
 * screen, remembering where they were trying to go.
 */
export default function RedirectToLogin({ to = "/login" }) {
  const location = useLocation();
  const from = `${location.pathname}${location.search}`;
  // Do not bounce back to an auth screen; that would loop.
  const safe = from.startsWith("/login") || from.startsWith("/register") ? undefined : from;
  return <Navigate to={to} state={safe ? { from: safe } : undefined} replace />;
}
