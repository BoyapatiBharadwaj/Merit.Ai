/**
 * The brand tagline, in one place.
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
