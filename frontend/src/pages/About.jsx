import { Link } from "react-router-dom";
import Navbar from "../components/Navbar.jsx";
import Footer from "../components/Footer.jsx";
import Icon, { IconBadge } from "../components/Icon.jsx";
import { btnPrimary, btnGhost, sectionEyebrow } from "../lib/ui.js";

/**
 * "Who We Are" — company/mission page.
 *
 * Kept to claims that are true of the product regardless of who's reading
 * this: what we believe assessment integrity requires, and how that shows
 * up in the platform (server-side enforcement, honest security copy, no
 * biometric data reused for anything but verification). No invented
 * founding story, headcount, or funding details — none of that exists to
 * describe truthfully, so it isn't manufactured for flavor.
 */

const VALUES = [
  {
    icon: "shield-check",
    title: "Integrity over optics",
    desc: "A proctoring signal only ships once it actually holds up — sustained, corroborated, and unlikely to flag someone for glancing at their keyboard. We'd rather catch less than accuse wrongly.",
  },
  {
    icon: "eye",
    title: "Honest about limits",
    desc: "No browser can stop a phone camera pointed at a monitor, and we say so plainly instead of implying otherwise. Trust is built by being precise about what's enforced versus what's only detected.",
  },
  {
    icon: "lock",
    title: "Privacy by design",
    desc: "Face data is stored as a numeric signature, not a searchable photo library. Proctoring stops the moment an attempt is submitted — nothing runs outside an active, proctored exam.",
  },
  {
    icon: "users",
    title: "Built for real classrooms",
    desc: "Every feature exists because an institution, examiner, or candidate needed it — roster-based access, retake resets after a crash, partial credit on coding questions.",
  },
];

const PRINCIPLES = [
  {
    n: "01",
    title: "Server decides, browser reports",
    desc: "Every scoring, access, and lockdown decision that matters is made and enforced on the server. The browser observes and reports — it is never trusted to grade itself or count its own strikes.",
  },
  {
    n: "02",
    title: "Failure defaults to no access",
    desc: "An exam nobody explicitly granted you is invisible, not merely hidden. An unverified identity blocks a proctored exam rather than warning and continuing.",
  },
  {
    n: "03",
    title: "Evidence, not accusations",
    desc: "Every violation is logged with a timestamp, a severity, and — where relevant — a screenshot, so a human reviewer sees exactly what happened rather than trusting a single automated verdict.",
  },
];

export default function About() {
  return (
    <div className="min-h-screen flex flex-col bg-page text-ink overflow-x-hidden">
      <Navbar />
      <main className="flex-1">
        {/* Hero */}
        <section className="relative overflow-hidden">
          <div
            className="absolute inset-x-0 top-0 h-[420px] -z-10 opacity-60"
            style={{
              background:
                "radial-gradient(600px circle at 15% 10%, rgba(37,99,235,0.12), transparent 60%), radial-gradient(500px circle at 85% 0%, rgba(22,163,74,0.10), transparent 55%)",
            }}
            aria-hidden="true"
          />
          <div className="max-w-3xl mx-auto px-5 sm:px-6 lg:px-8 pt-16 pb-14 lg:pt-20 text-center">
            <span className={`${sectionEyebrow} mb-6`}>
              <Icon name="heart" width={13} height={13} />
              Who We Are
            </span>
            <h1 className="text-4xl sm:text-5xl font-extrabold tracking-tight mb-5 text-balance">
              We think an exam result should mean something.
            </h1>
            <p className="text-lg text-muted leading-relaxed max-w-2xl mx-auto">
              Merit.Ai exists because remote assessment kept asking institutions to choose between trusting their
              candidates blindly or watching them like suspects. We built a platform that verifies identity, enforces
              a fair set of rules consistently, and tells everyone involved — candidate, examiner, and admin — exactly
              what it saw and why.
            </p>
          </div>
        </section>

        {/* Mission */}
        <section className="max-w-5xl mx-auto px-5 sm:px-6 lg:px-8 pb-20 lg:pb-24">
          <div className="rounded-3xl border border-border bg-surface p-8 sm:p-12 grid md:grid-cols-[1fr_1.4fr] gap-8 items-center">
            <div className="inline-flex items-center justify-center w-16 h-16 rounded-2xl bg-primary/10 text-primary mx-auto md:mx-0">
              <Icon name="compass" width={30} height={30} />
            </div>
            <div>
              <h2 className="text-2xl font-extrabold tracking-tight mb-3">Our mission</h2>
              <p className="text-muted leading-relaxed">
                Give every institution — not just the ones who can afford enterprise proctoring contracts — a way to
                run exams that candidates can trust and results that mean what they say. That means building the
                enforcement where it can't be tampered with, being transparent about what's genuinely possible in a
                browser, and never treating a candidate's biometric data as anything other than a verification tool.
              </p>
            </div>
          </div>
        </section>

        {/* Values */}
        <section className="bg-surface border-y border-border">
          <div className="max-w-7xl mx-auto px-5 sm:px-6 lg:px-8 py-20 lg:py-24">
            <div className="max-w-2xl mb-14">
              <span className={sectionEyebrow}>
                <Icon name="heart" width={13} height={13} />
                What we value
              </span>
              <h2 className="text-3xl sm:text-4xl font-extrabold tracking-tight mt-5 mb-4 text-balance">
                The principles behind every feature
              </h2>
            </div>
            <ul className="list-none grid sm:grid-cols-2 lg:grid-cols-4 gap-5">
              {VALUES.map((v, i) => (
                <li
                  key={v.title}
                  className="rounded-2xl border border-border bg-page p-6 transition-all duration-200 hover:border-primary/30 hover:-translate-y-1 animate-slide-up"
                  style={{ animationDelay: `${i * 0.06}s` }}
                >
                  <IconBadge name={v.icon} tone="surface" />
                  <h3 className="font-bold text-ink mt-4 mb-2">{v.title}</h3>
                  <p className="text-sm text-muted leading-relaxed">{v.desc}</p>
                </li>
              ))}
            </ul>
          </div>
        </section>

        {/* Design principles */}
        <section className="max-w-6xl mx-auto px-5 sm:px-6 lg:px-8 py-20 lg:py-24">
          <div className="max-w-2xl mb-14">
            <span className={sectionEyebrow}>
              <Icon name="target" width={13} height={13} />
              How we build
            </span>
            <h2 className="text-3xl sm:text-4xl font-extrabold tracking-tight mt-5 mb-4 text-balance">
              Three rules that shape every decision
            </h2>
          </div>
          <ol className="list-none flex flex-col gap-5">
            {PRINCIPLES.map((p) => (
              <li key={p.n} className="flex gap-5 sm:gap-8 rounded-2xl border border-border bg-surface p-6 sm:p-8">
                <span className="text-3xl sm:text-4xl font-extrabold text-primary/20 tabular-nums shrink-0">{p.n}</span>
                <div>
                  <h3 className="font-bold text-ink mb-2">{p.title}</h3>
                  <p className="text-sm text-muted leading-relaxed">{p.desc}</p>
                </div>
              </li>
            ))}
          </ol>
        </section>

        {/* CTA */}
        <section className="max-w-7xl mx-auto px-5 sm:px-6 lg:px-8 pb-20 lg:pb-24 text-center">
          <h2 className="text-2xl sm:text-3xl font-extrabold tracking-tight mb-4 text-balance">
            Want to know more about how it works?
          </h2>
          <p className="text-muted mb-8 max-w-xl mx-auto">
            See the full feature and security breakdown, or reach out directly with questions.
          </p>
          <div className="flex flex-wrap items-center justify-center gap-4">
            <Link to="/features" className={btnPrimary}>
              Explore Features
              <Icon name="arrow" width={16} height={16} />
            </Link>
            <Link to="/contact" className={btnGhost}>
              Contact Us
            </Link>
          </div>
        </section>
      </main>
      <Footer />
    </div>
  );
}
