import { Link } from "react-router-dom";
import Logo from "./Logo.jsx";
import LogoMark from "./LogoMark.jsx";
import Tagline from "./Tagline.jsx";
import ThemeToggle from "./ThemeToggle.jsx";
import Icon from "./Icon.jsx";
import { sectionEyebrow } from "../lib/ui.js";

/**
 * Headline copy is page-specific (variant="login" | "register"); the three benefit bullets
 * underneath stay the same set on both, since they're true regardless of which door someone's
 * coming through and keeping one list means they can't quietly drift apart.
 */
const PANEL_COPY = {
  login: { headline: "Welcome back to secure assessments." },
  register: { headline: "Create your account. Verify your identity. Start your exam." },
};

const PANEL_POINTS = ["Verified candidate identities", "Real-time integrity monitoring", "Fast, transparent results"];

/**
 * Live-status-styled product preview -- an "Exam readiness" widget rather than a couple of flat
 * "OK" lines, so the panel demonstrates the product instead of only claiming things about it.
 */
const READINESS_ROWS = [
  { icon: "camera", label: "Camera connected", value: "Ready" },
  { icon: "shield-check", label: "Face detected", value: "Verified" },
  { icon: "maximize", label: "Fullscreen access", value: "Enabled" },
];

export default function AuthLayout({ eyebrow, title, subtitle, children, footer, variant = "login" }) {
  const copy = PANEL_COPY[variant] || PANEL_COPY.login;

  return (
    // 44/56 rather than an even 50/50 -- the promotional panel was winning a
    // visual tie it didn't need to win; the extra width goes to the form side,
    // which is where every field, error, and button actually lives.
    <div className="min-h-screen grid lg:grid-cols-[44%_56%] bg-page">
      {/* Brand panel — desktop only (lg+); the compact banner further down
          carries the same identity below that breakpoint, since this whole
          panel disappears there. Mirrors the hero's gradient + dot-grid
          treatment so the auth flow still feels like the same product. */}
      <div className="hidden lg:flex relative flex-col justify-between brand-gradient text-white p-12 overflow-hidden">
        {/* Opacity cut from .12 to .09 (~25% reduction) -- at the old strength
            the dot grid competed with the headline text sitting on top of it,
            especially through the lighter strokes of narrow glyphs. */}
        <div
          className="absolute inset-0 opacity-[0.09]"
          style={{ backgroundImage: "radial-gradient(circle, #fff 1px, transparent 1px)", backgroundSize: "22px 22px" }}
          aria-hidden="true"
        />
        {/* A soft dark band behind the headline's vertical position specifically
            (independent of the dot grid and the two corner glows below), so
            the text keeps its contrast wherever those overlap it. */}
        <div className="absolute inset-x-0 top-[36%] h-60 bg-black/10 blur-2xl" aria-hidden="true" />
        <div className="absolute -top-16 -right-16 w-72 h-72 bg-white/10 rounded-full blur-3xl" aria-hidden="true" />
        <div className="absolute -bottom-20 -left-10 w-72 h-72 bg-white/10 rounded-full blur-3xl" aria-hidden="true" />
        <div
          className="absolute top-12 right-14 hidden xl:flex items-center justify-center w-11 h-11 rounded-2xl bg-white/10 border border-white/15 backdrop-blur-sm animate-float"
          aria-hidden="true"
        >
          <Icon name="shield-check" width={18} height={18} className="text-white/70" />
        </div>

        <Link to="/" className="relative inline-flex items-center gap-2.5 animate-fade-in">
          <LogoMark box={38} icon={18} tone="panel" />
          <span className="inline-flex leading-none text-[21px] font-extrabold tracking-tight">
            Merit<span className="text-white/70">.Ai</span>
          </span>
        </Link>

        <div className="relative animate-slide-up" style={{ animationDelay: "80ms" }}>
          {/* /80 rather than the default on-dark /60 -- against the brighter
              parts of the gradient (the corner glows especially) the dimmer
              tagline dropped under a comfortable contrast floor. */}
          <Tagline variant="on-dark" className="mb-3 !text-white/80" />
          <h2 className="text-3xl font-extrabold tracking-tight leading-tight mb-6 text-balance">{copy.headline}</h2>
          <ul className="space-y-3.5 mb-7">
            {PANEL_POINTS.map((point, i) => (
              <li
                key={point}
                className="flex items-center gap-3 text-sm text-white/90 animate-slide-in-right"
                style={{ animationDelay: `${160 + i * 70}ms` }}
              >
                <span className="inline-flex items-center justify-center w-5 h-5 rounded-full bg-white/15 shrink-0">
                  <Icon name="check" width={11} height={11} />
                </span>
                {point}
              </li>
            ))}
          </ul>

          <div className="relative rounded-2xl border border-white/15 bg-white/10 backdrop-blur-sm p-4 animate-fade-in" style={{ animationDelay: "460ms" }}>
            <div className="flex items-center justify-between mb-3">
              <span className="text-xs font-semibold text-white/90">Exam readiness</span>
              {/* One subtle pulse on the "live" label itself, not per-row --
                  animating all three rows would cross from "reassuring" into
                  "distracting" fast, and this is the version of that idea the
                  spec's "avoid excessive animation" note was steering toward. */}
              <span className="inline-flex items-center gap-1.5 text-[10px] font-bold uppercase tracking-wide text-emerald-300">
                <span className="relative flex w-1.5 h-1.5" aria-hidden="true">
                  <span className="animate-ping absolute inline-flex w-full h-full rounded-full bg-emerald-300 opacity-60" />
                  <span className="relative inline-flex w-1.5 h-1.5 rounded-full bg-emerald-300" />
                </span>
                Live preview
              </span>
            </div>
            <div className="space-y-2">
              {READINESS_ROWS.map((row) => (
                <div key={row.label} className="flex items-center justify-between text-xs text-white/80">
                  <span className="inline-flex items-center gap-2">
                    <Icon name={row.icon} width={13} height={13} className="text-white/60" />
                    {row.label}
                  </span>
                  <span className="inline-flex items-center gap-1 font-semibold text-emerald-300">
                    <Icon name="check" width={11} height={11} />
                    {row.value}
                  </span>
                </div>
              ))}
            </div>
          </div>
        </div>

        <p className="relative text-xs text-white/60">© {new Date().getFullYear()} Merit.Ai</p>
      </div>

      {/* Form column */}
      <div className="flex flex-col min-h-screen">
        {/* px-6/12/14 and pt-7/9 -- both sides of the page now share this same
            header rhythm (this row above the form on the right, the LogoMark
            row inside the panel on the left), so the two no longer read as
            two different scales bolted together. */}
        <div className="flex items-center justify-between px-6 sm:px-12 lg:px-14 pt-7 sm:pt-9 h-[68px] shrink-0">
          <Link to="/" className="inline-flex">
            <Logo size="lg" />
          </Link>
          <ThemeToggle />
        </div>

        {/* Compact brand banner, lg:hidden only -- the full panel above is
            gone entirely below that breakpoint, and without this a phone or
            tablet visitor landed straight on a plain white header with no
            brand identity until they scrolled past the form. */}
        <div className="lg:hidden brand-gradient text-white px-6 py-2.5 text-center shrink-0">
          <Tagline variant="on-dark" className="!text-white/85 text-center" />
        </div>

        {/* items-center replaced with a fixed top offset -- dead-centering
            this column put a wall of empty space above the form on any
            screen taller than the content, most visibly on the login page's
            shorter form. Anchoring from the top instead means the form
            starts in roughly the same place regardless of viewport height,
            rather than drifting lower on a taller monitor. */}
        <div className="flex-1 flex flex-col items-center px-6 sm:px-12 lg:px-14 pt-10 sm:pt-14 lg:pt-16 pb-10">
          <div className="w-full max-w-[460px]">
            {eyebrow && <span className={`${sectionEyebrow} mb-5 animate-slide-up`}>{eyebrow}</span>}
            {/* 2rem/2.5rem (32px/40px) -- up from the previous 28px/30px,
                which read closer to a section subhead than the first thing
                on a dedicated page. */}
            <h1
              className="text-[2rem] sm:text-[2.5rem] font-extrabold tracking-tight text-ink mb-2.5 animate-slide-up"
              style={{ animationDelay: "60ms" }}
            >
              {title}
            </h1>
            {subtitle && (
              <p className="text-base text-muted leading-relaxed mb-8 animate-slide-up" style={{ animationDelay: "110ms" }}>
                {subtitle}
              </p>
            )}
            <div className="animate-slide-up" style={{ animationDelay: "160ms" }}>
              {children}
            </div>
          </div>
        </div>

        {footer && (
          <div className="px-6 sm:px-12 lg:px-14 pb-10 text-center text-sm text-muted shrink-0 animate-fade-in">{footer}</div>
        )}
      </div>
    </div>
  );
}
