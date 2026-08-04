import LogoMark from "./LogoMark.jsx";

export default function Logo({ className = "", size = "md" }) {
  // "lg" is the auth pages' brand header (Login/Register, both sides of
  // AuthLayout) -- a bigger mark and wordmark than the app chrome uses
  // elsewhere, so the sign-in flow reads as a deliberate front door rather
  // than a shrunk-down copy of the dashboard navbar.
  const box = size === "lg" ? 38 : size === "sm" ? 28 : 32;
  const icon = size === "lg" ? 18 : size === "sm" ? 14 : 16;
  const textSize = size === "lg" ? "text-[21px]" : size === "sm" ? "text-base" : "text-lg";

  return (
    <span className={`inline-flex items-center gap-2.5 select-none ${className}`}>
      <LogoMark box={box} icon={icon} />
      {/* The wordmark itself is real text, so it (not the mark above) is what
          gives this element its accessible name when it's the content of a link. */}
      <span className={`inline-flex leading-none font-extrabold ${textSize} tracking-tight text-ink`}>
        Merit<span className="text-primary">.Ai</span>
      </span>
    </span>
  );
}
