import { Link } from "react-router-dom";
import Navbar from "../components/Navbar.jsx";
import Footer from "../components/Footer.jsx";
import Icon, { IconBadge } from "../components/Icon.jsx";
import { btnPrimary, btnGhost, sectionEyebrow } from "../lib/ui.js";

/**
 * Pricing page.
 */

const TIERS = [
  {
    name: "Candidate",
    price: "Free",
    priceNote: "forever, no card required",
    desc: "For anyone taking a proctored exam on Merit.Ai.",
    icon: "user",
    features: [
      "Create an account in under a minute",
      "One-time face & ID verification",
      "Full proctored exam interface",
      "Autosave with reconnect recovery",
      "Instant results & PDF report",
    ],
    cta: { label: "Get Started Free", to: "/register" },
    highlight: false,
  },
  {
    name: "Institution",
    price: "Custom",
    priceNote: "based on cohort size",
    desc: "For schools, bootcamps, and companies running their own exams.",
    icon: "briefcase",
    features: [
      "Everything in Candidate",
      "Verified examiner account & organization roster",
      "Drag-and-drop exam builder + bulk MCQ import",
      "Sandboxed Python & JavaScript coding questions",
      "Live session monitoring & violation dashboard",
      "Analytics: score distribution, violation breakdown",
    ],
    cta: { label: "Request Access", to: "/request-access" },
    highlight: true,
  },
  {
    name: "Enterprise",
    price: "Custom",
    priceNote: "for large or multi-department deployments",
    desc: "For institutions that need dedicated infrastructure and support.",
    icon: "target",
    features: [
      "Everything in Institution",
      "Dedicated AI worker deployment for higher throughput",
      "Multiple examiners, one shared organization roster",
      "Priority onboarding & configuration support",
      "Direct line for security & compliance questions",
    ],
    cta: { label: "Contact Sales", to: "/contact" },
    highlight: false,
  },
];

const FAQ_TEASERS = [
  {
    q: "Is it really free for candidates?",
    a: "Yes — there's no payment step anywhere in candidate registration or exam-taking. Institutions cover the cost of running exams, not the people sitting them.",
  },
  {
    q: "How does institution pricing work?",
    a: "Every examiner account is set up by our team after a short access request — there's no self-serve checkout yet, so pricing is quoted based on your cohort size and needs.",
  },
  {
    q: "Can I try it before committing my institution?",
    a: "Yes. Register a free candidate account to experience the actual exam interface and proctoring flow firsthand before requesting an examiner account.",
  },
];

function cardBase(highlight) {
  return (
    "relative flex flex-col rounded-2xl border p-7 sm:p-8 transition-all duration-200 animate-slide-up " +
    (highlight
      ? "border-primary/40 bg-surface shadow-[0_20px_50px_-20px_rgba(37,99,235,0.35)] lg:-translate-y-3"
      : "border-border bg-surface hover:border-primary/30 hover:-translate-y-1")
  );
}

export default function Pricing() {
  return (
    <div className="min-h-screen flex flex-col bg-page text-ink overflow-x-hidden">
      <Navbar />
      <main className="flex-1">
        <section className="relative overflow-hidden">
          <div
            className="absolute inset-x-0 top-0 h-[420px] -z-10 opacity-60"
            style={{
              background:
                "radial-gradient(600px circle at 15% 10%, rgba(37,99,235,0.12), transparent 60%), radial-gradient(500px circle at 85% 0%, rgba(22,163,74,0.10), transparent 55%)",
            }}
            aria-hidden="true"
          />
          <div className="max-w-3xl mx-auto px-5 sm:px-6 lg:px-8 pt-16 pb-6 lg:pt-20 text-center">
            <span className={`${sectionEyebrow} mb-6`}>
              <Icon name="zap" width={13} height={13} />
              Pricing
            </span>
            <h1 className="text-4xl sm:text-5xl font-extrabold tracking-tight mb-5 text-balance">
              Simple for candidates. Fair for institutions.
            </h1>
            <p className="text-lg text-muted leading-relaxed max-w-2xl mx-auto">
              Candidates never pay to take an exam. Institutions get a quote based on cohort size — set up by our
              team, not a self-serve checkout.
            </p>
          </div>
        </section>

        <section className="max-w-6xl mx-auto px-5 sm:px-6 lg:px-8 pb-20 lg:pb-24 pt-6">
          <ul className="list-none grid lg:grid-cols-3 gap-6 lg:gap-5 items-start">
            {TIERS.map((tier, i) => (
              <li key={tier.name} className={cardBase(tier.highlight)} style={{ animationDelay: `${i * 0.06}s` }}>
                {tier.highlight && (
                  <span className="absolute -top-3 left-1/2 -translate-x-1/2 inline-flex items-center gap-1.5 rounded-full brand-gradient text-white text-xs font-semibold px-3.5 py-1.5 shadow-[0_6px_16px_-6px_rgba(37,99,235,0.7)]">
                    <Icon name="zap" width={12} height={12} />
                    Most common
                  </span>
                )}

                <IconBadge name={tier.icon} tone={tier.highlight ? "primary" : "surface"} />
                <h2 className="text-lg font-bold text-ink mt-5 mb-1">{tier.name}</h2>
                <p className="text-sm text-muted mb-5 min-h-[2.5rem]">{tier.desc}</p>

                <div className="mb-6 pb-6 border-b border-border">
                  <span className="text-3xl font-extrabold text-ink tracking-tight">{tier.price}</span>
                  <p className="text-xs text-muted mt-1">{tier.priceNote}</p>
                </div>

                <ul className="list-none flex flex-col gap-2.5 mb-7">
                  {tier.features.map((f) => (
                    <li key={f} className="flex items-start gap-2.5 text-sm text-ink">
                      <span className="mt-0.5 text-success shrink-0">
                        <Icon name="check" width={15} height={15} />
                      </span>
                      {f}
                    </li>
                  ))}
                </ul>

                <div className="mt-auto">
                  <Link
                    to={tier.cta.to}
                    className={`w-full ${(tier.highlight ? btnPrimary : btnGhost).replace("py-3", "py-2.5")}`}
                  >
                    {tier.cta.label}
                    <Icon name="arrow" width={15} height={15} />
                  </Link>
                </div>
              </li>
            ))}
          </ul>
        </section>

        {/* Quick pricing FAQ, with a link out to the full FAQ page */}
        <section className="bg-surface border-y border-border">
          <div className="max-w-4xl mx-auto px-5 sm:px-6 lg:px-8 py-16 lg:py-20">
            <h2 className="text-2xl font-extrabold tracking-tight mb-8 text-center">Pricing questions</h2>
            <div className="flex flex-col gap-5 mb-8">
              {FAQ_TEASERS.map((item) => (
                <div key={item.q} className="rounded-2xl border border-border bg-page p-5 sm:p-6">
                  <h3 className="font-bold text-ink mb-1.5">{item.q}</h3>
                  <p className="text-sm text-muted leading-relaxed">{item.a}</p>
                </div>
              ))}
            </div>
            <p className="text-center text-sm text-muted">
              More questions?{" "}
              <Link to="/faq" className="font-semibold text-primary hover:underline">
                Read the full FAQ
              </Link>{" "}
              or{" "}
              <Link to="/contact" className="font-semibold text-primary hover:underline">
                contact us
              </Link>
              .
            </p>
          </div>
        </section>

        <section className="max-w-7xl mx-auto px-5 sm:px-6 lg:px-8 py-20 lg:py-24 text-center">
          <h2 className="text-2xl sm:text-3xl font-extrabold tracking-tight mb-4 text-balance">
            Ready to talk about your cohort?
          </h2>
          <p className="text-muted mb-8 max-w-xl mx-auto">
            Tell us about your institution and we&apos;ll set up a verified examiner account with a quote to match.
          </p>
          <div className="flex flex-wrap items-center justify-center gap-4">
            <Link to="/request-access" className={btnPrimary}>
              Request Access
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
