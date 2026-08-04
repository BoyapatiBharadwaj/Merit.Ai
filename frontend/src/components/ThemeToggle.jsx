import { useEffect, useState } from "react";
import { getInitialTheme, applyTheme } from "../lib/theme.js";

// Decorative only -- the toggle button below already carries the accessible
// name/state via aria-label + aria-checked, so these icons stay out of the
// accessibility tree rather than being announced redundantly.
const SunIcon = (props) => (
  <svg aria-hidden="true" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" {...props}>
    <circle cx="12" cy="12" r="4.2" />
    <path d="M12 2.5v2.2M12 19.3v2.2M4.5 4.5l1.6 1.6M17.9 17.9l1.6 1.6M2.5 12h2.2M19.3 12h2.2M4.5 19.5l1.6-1.6M17.9 6.1l1.6-1.6" />
  </svg>
);

const MoonIcon = (props) => (
  <svg aria-hidden="true" width="12" height="12" viewBox="0 0 24 24" fill="currentColor" {...props}>
    <path d="M20.6 15.1A9 9 0 1 1 9 3.4a7.2 7.2 0 0 0 11.6 11.7Z" />
  </svg>
);

export default function ThemeToggle({ className = "" }) {
  const [theme, setTheme] = useState(getInitialTheme);
  const isDark = theme === "dark";

  useEffect(() => {
    applyTheme(theme);
  }, [theme]);

  return (
    <button
      type="button"
      role="switch"
      aria-checked={isDark}
      aria-label="Toggle dark mode"
      onClick={() => setTheme((t) => (t === "dark" ? "light" : "dark"))}
      // h-9 (36px) rather than the old h-8 (32px) -- still short of a literal
      // 40px square (this is a pill switch, not an icon button, so matching
      // that exactly would distort its shape), but a meaningfully larger
      // touch target than before, and the hit area extends the full
      // w-16 x h-9 track rather than just the visible thumb.
      className={`relative inline-flex h-9 w-16 shrink-0 items-center rounded-full border border-border bg-page transition-colors duration-200 ${className}`}
    >
      <span className="pointer-events-none absolute inset-0 flex items-center justify-between px-2.5">
        <SunIcon className={`transition-opacity duration-200 ${isDark ? "opacity-30" : "opacity-70"} text-warning`} />
        <MoonIcon className={`transition-opacity duration-200 ${isDark ? "opacity-80" : "opacity-30"} text-primary`} />
      </span>
      <span
        className={`inline-flex h-7 w-7 items-center justify-center rounded-full bg-surface shadow-card ring-1 ring-black/5 transition-transform duration-200 ease-out ${
          isDark ? "translate-x-[31px]" : "translate-x-1"
        }`}
      >
        {isDark ? <MoonIcon className="text-primary" /> : <SunIcon className="text-warning" />}
      </span>
    </button>
  );
}
