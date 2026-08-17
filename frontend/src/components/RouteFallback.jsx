import LogoMark from "./LogoMark.jsx";

/**
 * Shown while a code-split route chunk downloads.
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
