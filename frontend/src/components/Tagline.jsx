/**
 * The brand tagline, in one place. Previously this was three hand-copied
 * blocks of near-identical markup (Home.jsx hero, Footer.jsx, AuthLayout.jsx)
 * that had already started to drift in wording and letter-spacing. Now every
 * call site renders the same three words with the same separator, and only
 * the presentation (pill kicker vs. plain line, light vs. dark background)
 * varies by variant.
 *
 * - "eyebrow": the hero kicker -- a pill with a status dot, replacing the
 *   generic "AI Proctoring & Exam Lockdown" category label that used to sit
 *   above the H1. The tagline IS the positioning statement; a second line
 *   restating it a few pixels below was redundant, so this replaces that
 *   line rather than sitting alongside it.
 * - "text": a plain colored line for light surfaces (footer).
 * - "on-dark": the same plain line, dimmed white for dark/brand surfaces
 *   (the auth split-panel).
 */
const WORDS = ["Conduct", "Monitor", "Evaluate"];

function TaglineWords() {
  return (
    <>
      {WORDS.map((word, i) => (
        <span key={word}>
          {i > 0 && (
            <span aria-hidden="true" className="mx-2 opacity-40">
              ·
            </span>
          )}
          {word}
        </span>
      ))}
    </>
  );
}

export default function Tagline({ variant = "text", className = "" }) {
  if (variant === "eyebrow") {
    return (
      <span
        className={`inline-flex items-center gap-2 rounded-full border border-border bg-surface px-3.5 py-1.5 text-xs font-bold uppercase tracking-[0.15em] text-primary ${className}`}
      >
        <span className="w-1.5 h-1.5 rounded-full bg-primary shrink-0" aria-hidden="true" />
        <TaglineWords />
      </span>
    );
  }

  const toneClass = variant === "on-dark" ? "text-white/60" : "text-primary";
  return (
    <p className={`text-xs font-bold uppercase tracking-[0.2em] ${toneClass} ${className}`}>
      <TaglineWords />
    </p>
  );
}
