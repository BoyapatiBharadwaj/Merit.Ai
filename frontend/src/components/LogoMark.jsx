/**
 * The brand mark, on its own -- a checkmark (Evaluate) with a small dot
 * floating above it (Monitor). The single source of truth for the icon:
 * Logo.jsx (nav/footer wordmark) and AuthLayout.jsx (the auth split-panel,
 * which needs a larger badge on a translucent tile rather than the solid
 * gradient tile) both render this instead of each keeping their own copy of
 * the SVG, so the mark can never quietly drift between the two places it
 * appears.
 *
 * `rounded-[28%]` rather than a fixed pixel radius so the corner stays
 * visually identical at every size this renders at (nav: 32px, auth panel:
 * 36px, footer/mobile: 28px) instead of three separately-tuned px values.
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
