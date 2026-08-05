import { Link, useNavigate } from "react-router-dom";
import Logo from "./Logo.jsx";
import ThemeToggle from "./ThemeToggle.jsx";
import { btnGhost } from "../lib/ui.js";
import { getName, getRole, clearSession, mustChangePassword, roleLabel } from "../lib/auth.js";

/** Top bar shared by every authenticated page (dashboards, and eventually
 * the exam interface's non-fullscreen chrome). Keeping this in one place
 * means the "logged in as X" affordance never drifts between roles. */
export default function DashboardHeader({ title }) {
  const navigate = useNavigate();
  const name = getName();
  const role = getRole();

  function handleLogout() {
    clearSession();
    navigate("/", { replace: true });
  }

  return (
    <>
    {/*
      Shown on every authenticated page, because "someone else knows your
      password" is not a fact that belongs only on the screen you happened to
      land on. Not a forced redirect: students cannot change their own password
      on this platform by design, so routing every flagged session to a change
      form would strand the one role with no form to fill in. Each role is
      pointed at the route that actually works for it.
    */}
    {mustChangePassword() && (
      <div role="status"
           className="flex flex-wrap items-center justify-center gap-x-3 gap-y-1 bg-warning/10 border-b border-warning/30 px-4 py-2.5 text-sm text-warning">
        <span className="font-semibold">
          Your password was set by an administrator, so you are not the only person who knows it.
        </span>
        <Link to={role === "student" ? "/forgot-password" : "/profile"}
              className="font-semibold underline underline-offset-2 hover:no-underline">
          {role === "student" ? "Set one only you know" : "Change it now"}
        </Link>
      </div>
    )}
    <header className="border-b border-border bg-surface">
      <div className="max-w-7xl mx-auto px-5 sm:px-6 lg:px-8 h-[68px] flex items-center justify-between gap-4">
        <div className="flex items-center gap-4 min-w-0">
          {/* This header only renders on already-authenticated pages
              (dashboards, Profile), so the logo should keep the user inside
              the app instead of bouncing them out to the public marketing
              site the way the logged-out Navbar's logo does. */}
          <Link to="/dashboard" className="inline-flex shrink-0">
            <Logo />
          </Link>
          {title && (
            <>
              <span className="hidden sm:block w-px h-6 bg-border shrink-0" aria-hidden="true" />
              <h1 className="hidden sm:block text-sm font-semibold text-muted truncate">{title}</h1>
            </>
          )}
        </div>
        <div className="flex items-center gap-3 sm:gap-4 shrink-0">
          {/* Every DashboardHeader-using page except the dashboard itself
              (Profile today, more sub-pages later) otherwise has no way
              back except the browser's back button. */}
          <Link to="/dashboard" className="hidden sm:inline text-sm font-medium text-muted hover:text-ink transition-colors">
            Dashboard
          </Link>
          {/* Every role, not just students. This was student-only, which --
              together with the redirect that used to sit at the top of
              Profile.jsx -- left examiners and admins with no route anywhere
              in the app to change their own password. */}
          <Link to="/profile" className="hidden sm:inline text-sm font-medium text-muted hover:text-ink transition-colors">
            Profile
          </Link>
          <ThemeToggle />
          <span className="hidden sm:block w-px h-6 bg-border" aria-hidden="true" />
          <div className="hidden sm:flex items-center gap-2 text-sm">
            <span className="font-semibold text-ink">{name}</span>
            <span className="text-xs font-semibold text-primary bg-primary/10 rounded-full px-2.5 py-1">{roleLabel(role)}</span>
          </div>
          <button onClick={handleLogout} className={btnGhost.replace("px-5 py-3", "px-4 py-2")}>
            Log Out
          </button>
        </div>
      </div>
    </header>
    </>
  );
}
