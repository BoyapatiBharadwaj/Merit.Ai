import { useEffect, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import Logo from "./Logo.jsx";
import ThemeToggle from "./ThemeToggle.jsx";
import { btnGhost, btnPrimary } from "../lib/ui.js";

// Real routes, not anchor scrolls -- each of these is now its own page, so a
// visitor can land directly on /pricing or /contact (from a search engine, a
// bookmark, a shared link) and get the whole page, not a blank Home with no
// matching section to scroll to.
const NAV_LINKS = [
  { to: "/features", label: "Features" },
  { to: "/pricing", label: "Pricing" },
  { to: "/about", label: "Who We Are" },
  { to: "/faq", label: "FAQ" },
  { to: "/contact", label: "Contact" },
];

export default function Navbar() {
  const [scrolled, setScrolled] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const location = useLocation();

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 8);
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  // Close the mobile menu on every route change -- otherwise navigating from
  // the mobile sheet leaves it open behind the new page.
  useEffect(() => {
    setMenuOpen(false);
  }, [location.pathname]);

  const isActive = (to) => location.pathname === to;

  return (
    <header
      className={`sticky top-0 z-50 border-b transition-colors duration-200 ${
        scrolled ? "bg-surface/95 backdrop-blur-md border-border shadow-[0_1px_0_rgba(0,0,0,0.02)]" : "bg-transparent border-transparent"
      }`}
    >
      <nav aria-label="Primary" className="max-w-7xl mx-auto px-5 sm:px-6 lg:px-8 h-[68px] flex items-center justify-between gap-6">
        <Link to="/" className="inline-flex shrink-0">
          <Logo />
        </Link>

        {/* lg, not md.
            At md (768px) this row rendered a logo, five links, a theme switch
            and two buttons with no overflow, wrap or condensing strategy -- on
            a portrait tablet they simply collided. Moving the breakpoint up
            means the burger menu covers 768-1023px, which is where the content
            no longer fits. */}
        <div className="hidden lg:flex items-center gap-1">
          {NAV_LINKS.map((link) => (
            <Link
              key={link.to}
              to={link.to}
              aria-current={isActive(link.to) ? "page" : undefined}
              className={`group relative px-3.5 py-2 text-sm font-medium transition-colors ${
                isActive(link.to) ? "text-ink" : "text-muted hover:text-ink"
              }`}
            >
              {link.label}
              <span
                className={`absolute left-3.5 right-3.5 -bottom-0 h-px bg-primary transition-transform duration-200 origin-left ${
                  isActive(link.to) ? "scale-x-100" : "scale-x-0 group-hover:scale-x-100"
                }`}
              />
            </Link>
          ))}
        </div>

        <div className="hidden lg:flex items-center gap-3 shrink-0">
          <ThemeToggle />
          <span className="w-px h-6 bg-border" aria-hidden="true" />
          <Link to="/login" className={btnGhost.replace("px-5 py-3", "px-4 py-2")}>
            Log in
          </Link>
          <Link to="/register" className={btnPrimary.replace("px-5 py-3", "px-4 py-2")}>
            Candidate Sign Up
          </Link>
        </div>

        <div className="flex lg:hidden items-center gap-2 shrink-0">
          <ThemeToggle />
          <button
            type="button"
            aria-label={menuOpen ? "Close menu" : "Open menu"}
            aria-expanded={menuOpen}
            aria-controls="primary-mobile-menu"
            onClick={() => setMenuOpen((v) => !v)}
            // 44x44, not 36x36. Below ~44px a touch target is measurably harder to
            // hit, and this is the control a candidate on a phone needs first.
            className="inline-flex items-center justify-center w-11 h-11 rounded-lg border border-border text-ink hover:bg-page transition-colors"
          >
            <svg aria-hidden="true" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
              {menuOpen ? <path d="M18 6 6 18M6 6l12 12" /> : <path d="M3 6h18M3 12h18M3 18h18" />}
            </svg>
          </button>
        </div>
      </nav>

      {menuOpen && (
        <div id="primary-mobile-menu" className="lg:hidden border-t border-border bg-surface px-5 py-5 flex flex-col gap-1 animate-slide-up">
          {NAV_LINKS.map((link) => (
            <Link
              key={link.to}
              to={link.to}
              aria-current={isActive(link.to) ? "page" : undefined}
              className={`text-sm font-medium py-2.5 border-b border-border last:border-0 ${
                isActive(link.to) ? "text-primary" : "text-ink"
              }`}
            >
              {link.label}
            </Link>
          ))}
          <div className="flex gap-3 pt-4">
            <Link to="/login" className={`flex-1 ${btnGhost.replace("px-5 py-3", "px-4 py-2.5")}`}>
              Log in
            </Link>
            <Link to="/register" className={`flex-1 ${btnPrimary.replace("px-5 py-3", "px-4 py-2.5")}`}>
              Get Started
            </Link>
          </div>
        </div>
      )}
    </header>
  );
}
