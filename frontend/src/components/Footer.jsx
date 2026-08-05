import { Link } from "react-router-dom";
import Logo from "./Logo.jsx";
import Tagline from "./Tagline.jsx";

const COLUMNS = [
  {
    title: "Product",
    links: [
      { label: "Features", to: "/features" },
      { label: "Pricing", to: "/pricing" },
      { label: "FAQ", to: "/faq" },
    ],
  },
  {
    title: "Company",
    links: [
      { label: "Who We Are", to: "/about" },
      { label: "Contact", to: "/contact" },
      { label: "Privacy", to: "/privacy" },
    ],
  },
  {
    // Was three separate "Student / Examiner / Admin Login" entries that all
    // pointed at the same /login route -- a single sign-in serves every role,
    // so three links implied portals that don't exist.
    title: "Candidates",
    links: [
      { label: "Create Candidate Account", to: "/register" },
      { label: "Candidate Login", to: "/login" },
      { label: "Test Your Device", to: "/system-check" },
      { label: "Log in", to: "/login" },
    ],
  },
  {
    title: "Institutions",
    links: [
      { label: "Request Examiner Access", to: "/request-access" },
      { label: "Examiner Log in", to: "/login" },
    ],
  },
];

export default function Footer() {
  return (
    <footer aria-label="Site footer" className="border-t border-border bg-surface">
      <div className="max-w-7xl mx-auto px-5 sm:px-6 lg:px-8 py-16 grid gap-10 sm:grid-cols-2 lg:grid-cols-[1.3fr_1fr_1fr_1fr_1fr]">
        <div>
          <Logo />
          <Tagline className="mt-4" />
          <p className="mt-3 text-sm leading-relaxed text-muted max-w-xs">
            AI-proctored online exams with real-time monitoring, sandboxed coding assessments, and analytics — built
            for institutions, examiners, and candidates.
          </p>
        </div>

        {COLUMNS.map((col) => (
          <div key={col.title}>
            {/* h2: the footer is its own landmark region, so its column
                labels start a fresh heading outline rather than continuing
                the main content's h1/h2/h3 hierarchy. */}
            <h2 className="text-xs font-bold uppercase tracking-wider text-muted mb-4">{col.title}</h2>
            <ul className="flex flex-col gap-3">
              {col.links.map((link) => (
                <li key={link.label}>
                  {link.to ? (
                    <Link to={link.to} className="text-sm text-ink hover:text-primary transition-colors">
                      {link.label}
                    </Link>
                  ) : (
                    <a href={link.href} className="text-sm text-ink hover:text-primary transition-colors">
                      {link.label}
                    </a>
                  )}
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>

      <div className="border-t border-border">
        <div className="max-w-7xl mx-auto px-5 sm:px-6 lg:px-8 py-5 flex flex-col sm:flex-row items-center justify-between gap-3 text-xs text-muted">
          <span>© {new Date().getFullYear()} Merit.Ai. All rights reserved.</span>
          <span>Built for secure, transparent, AI-assisted assessment.</span>
        </div>
      </div>
    </footer>
  );
}
