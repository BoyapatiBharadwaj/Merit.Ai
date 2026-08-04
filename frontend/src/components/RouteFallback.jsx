import LogoMark from "./LogoMark.jsx";

/**
 * Shown while a code-split route chunk downloads.
 *
 * Deliberately quiet: a branded mark and a subtle pulse rather than a spinner
 * with "Loading...". On a fast connection this is visible for a few frames and
 * anything louder reads as a flash of broken layout; on a slow one it needs to
 * look intentional rather than stuck.
 *
 * No timeout or error state here -- a chunk that genuinely fails to load throws,
 * and ErrorBoundary above catches it with a message and a retry. Duplicating
 * that logic here would mean two places deciding what "failed" means.
 */
export default function RouteFallback() {
  return (
    <div className="min-h-screen bg-page flex items-center justify-center" role="status" aria-live="polite">
      <div className="flex flex-col items-center gap-3 animate-pulse">
        <LogoMark box={40} icon={19} />
        <span className="sr-only">Loading…</span>
      </div>
    </div>
  );
}
