/**
 * The brand mark, on its own -- a checkmark (Evaluate)
 * with a small dot floating above it (Monitor).
 */
export default function LogoMark({ box = 32, icon = 15, tone = "brand", className = "" }) {
  const toneClass =
    tone === "brand"
      ? "brand-gradient shadow-[0_2px_6px_-1px_rgba(37,99,235,0.5)]"
      : "bg-white/15 border border-white/20 backdrop-blur-sm";

  return (
    <span
      className={`inline-flex items-center justify-center shrink-0 rounded-[28%] ${toneClass} ${className}`}
      style={{ width: box, height: box }}
    >
      <svg aria-hidden="true" width={icon} height={icon} viewBox="0 0 24 24" fill="none" stroke="#fff" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
        <path d="M5 12.5 10 17.5 19 6.5" />
        <circle cx="5.5" cy="5.5" r="1.6" fill="#fff" stroke="none" />
      </svg>
    </span>
  );
}
