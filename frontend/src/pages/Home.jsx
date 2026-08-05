import { Link } from "react-router-dom";
import Navbar from "../components/Navbar.jsx";
import Footer from "../components/Footer.jsx";
import Tagline from "../components/Tagline.jsx";
import { btnPrimary, btnGhost, btnPrimaryOnDark, btnGhostOnDark, sectionEyebrow } from "../lib/ui.js";
import Icon, { IconBadge } from "../components/Icon.jsx";

/**
 * Marketing landing page — "/".
 *
 * Deliberately short. This used to carry the full feature grid and the full
 * six-layer security breakdown inline, which made it read as one long
 * undifferentiated page. Both now live on their own dedicated page
 * (Features.jsx, linked from the nav and from the teaser section below) so a
 * first-time visitor gets a fast, confident pitch here and a deep evaluator
 * has one clear page to go read everything.
 *
 * A note on copy, because it matters more here than anywhere else in the app:
 * every claim below is written to match what the code actually does. The
 * lockdown layer genuinely enforces fullscreen, focus, and clipboard rules and
 * genuinely auto-submits on the third breach -- so it is described in those
 * terms. Screenshots and screen recording are NOT preventable from a browser
 * tab, so nothing here claims otherwise -- the full honesty panel about that
 * lives on the Features page.
 */

/**
 * Every figure here is checkable against the code rather than rounded up for
 * effect: 19 is the EventType enum minus `lockdown_terminated` (an outcome,
 * not a detected behaviour), 3 is LOCKDOWN_STRIKE_LIMIT, 2 is the face + ID
 * gate in identity_service, and 0 is literal -- the platform is browser-only.
 */
/**
 * Facts about what the software does, not claims about how many people use it.
 *
 * Deliberately no "10,000 exams proctored" or "99.9% uptime": this deployment
 * has no such figures, and a number a visitor cannot check is worth less than
 * no number on a page whose entire argument is that the platform is honest
 * about what it can and cannot see.
 *
 * Each of these is checkable in the source, and each was checked. "19 violation
 * types" was wrong -- EventType has twenty members -- which is exactly the drift
 * a hardcoded marketing figure invites: the code grew one and the homepage did
 * not notice. LOCKDOWN_STRIKE_LIMIT is 3 and is configurable, so the label says
 * "by default" rather than stating it as a law.
 */
const STATS = [
  { value: "20", label: "Violation Types Tracked" },
  { value: "3", label: "Strikes Before Auto-Submit, by default" },
  { value: "2", label: "Identity Checks Before Entry" },
  { value: "0", label: "Downloads Required" },
];

/**
 * Four compact highlights, not the full feature grid -- this is a teaser
 * with a "see everything" door, not the whole catalog. Picked as the four
 * things that most directly answer "why would I trust this over a plain
 * timed form": enforcement, identity, live AI signals, and safe code grading.
 */
const HIGHLIGHTS = [
  {
    title: "Server-Enforced Monitoring",
    desc: "Fullscreen required, tab-switches counted, three strikes auto-submits — decided server-side, never by the browser.",
    icon: "maximize",
  },
  {
    title: "Identity Verified",
    desc: "Face registration plus an ID-card name match, once, before a candidate's first proctored exam.",
    icon: "shield-check",
  },
  {
    title: "Multi-Signal Proctoring",
    desc: "Face match, anti-spoofing, gaze and head-pose, phone/book/person detection — continuous, not a single snapshot.",
    icon: "eye",
  },
  {
    title: "Sandboxed Coding",
    desc: "Python and JavaScript run in isolated, network-disabled containers with partial credit per test case.",
    icon: "code",
  },
];

const STEPS = [
  {
    n: "1",
    title: "Design the Exam",
    desc: "Examiners build sections, MCQs, and coding questions — with drag-and-drop ordering, bulk import, and scheduling.",
  },
  {
    n: "2",
    title: "Candidates Verify",
    desc: "Before their first proctored exam, candidates register their face and pass an ID-card name match. Neither can be skipped.",
  },
  {
    n: "3",
    title: "Lockdown Engages",
    desc: "The exam opens in enforced fullscreen with clipboard and shortcut restrictions active, while AI signals run continuously.",
  },
  {
    n: "4",
    title: "Results, When You Choose",
    // Was "Results, Immediately", which stopped being true when result release
    // became the examiner's decision. The first candidate to finish used to
    // hold the complete answer key while everyone else was still writing.
    desc: "Scores compute automatically — including partial credit on coding tests. Release them the moment an attempt ends, or hold every result until the paper closes.",
  },
];

/**
 * Two audiences, matching the two routes a visitor can actually take from
 * here: candidates self-register, examiners submit a request an admin
 * reviews. The admin card was removed deliberately -- admin accounts are
 * provisioned internally, never signed up for, so a public "Log in as Admin"
 * card offered a door with nothing behind it.
 */
const AUDIENCES = [
  {
    title: "For Candidates",
    icon: "user",
    desc: "A focused exam interface with a visible timer, question navigator, and honest proctoring status at all times.",
    bullets: [
      "One-time face & ID verification",
      "Autosave on every answer",
      "Resume instantly after a disconnect",
      "Instant, detailed results",
    ],
    cta: { label: "Take an Exam", to: "/register" },
    note: "Free — create your account in under a minute.",
  },
  {
    title: "For Institutions & Examiners",
    icon: "briefcase",
    desc: "Build and schedule exams, manage question banks, and monitor active sessions live — all from one dashboard.",
    bullets: [
      "Drag-and-drop question builder",
      "Bulk MCQ import",
      "Real-time attempt monitoring",
      "Violation & performance reports",
    ],
    // Was "Create an Exam" pointing at /request-access, which submits an
    // application form. A visitor clicking it expected a builder and got a
    // waiting list.
    cta: { label: "Request Institution Access", to: "/request-access" },
    note: "See Pricing for how institution accounts work.",
  },
];

/**
 * Who actually runs exams on this, and what each one needs.
 *
 * The page described features and roles but never a situation. "Multi-signal
 * proctoring" tells a university registrar nothing about whether this fits a
 * 400-seat first-year paper; a sentence about a 400-seat first-year paper does.
 * Each of these is deliberately tied to a capability that exists rather than
 * one that would be nice to advertise.
 */
const USE_CASES = [
  {
    icon: "briefcase",
    title: "University semester exams",
    desc: "A large cohort sitting the same paper at the same time. Roster the class by email before anyone has signed up, hold every result until the last candidate submits, then release the lot at once.",
  },
  {
    icon: "code",
    title: "Technical hiring screens",
    desc: "Python and JavaScript run in isolated, network-disabled containers with partial credit per test case — so a candidate who solves three of five cases scores three of five, not zero.",
  },
  {
    icon: "shield-check",
    title: "Certification and licensing",
    desc: "Where a result carries legal weight, every flag is timestamped, evidence-backed and reviewable, and an examiner's confirm-or-dismiss decision is recorded against their name.",
  },
  {
    icon: "user",
    title: "Remote and distance learning",
    desc: "Candidates test their camera, microphone and connection days ahead on a page that needs no account, so exam morning is not the first time anything is checked.",
  },
];

/**
 * What the platform will not claim.
 *
 * This section exists because everything above it is a sales pitch, and an exam
 * platform asking for a webcam in someone's bedroom owes them the other half.
 * Every line here is a real limit in the code, not modesty: the lockdown is
 * best-effort because a browser cannot police an operating system, the AI
 * signals are indicators because they are, and a second device is genuinely
 * undetectable. An institution that reads this and walks away was going to be
 * disappointed later anyway.
 */
const TRUST = [
  {
    claim: "Violations are decided by the server",
    detail: "Strike counting, auto-submit and the timer all run server-side. A candidate editing the page cannot award themselves more time or unlogged tab switches.",
  },
  {
    claim: "The lockdown is best-effort, and we say so",
    detail: "A web page cannot police an operating system. Screen recorders, virtual machines and a second phone under the desk are outside what any browser-based proctoring can see.",
  },
  {
    claim: "AI signals are indicators, not verdicts",
    detail: "Gaze, head pose and object detection produce flags for a human to review. Nothing here fails a candidate on its own, and every flag carries the evidence it was raised from.",
  },
  {
    claim: "Biometric data is yours to delete",
    detail: "Face and ID-card data can be erased from your own profile at any time, without asking anyone. Your results and history are not touched — they are assessment records, not biometrics.",
  },
];

/* ==========================================================================
   Shared section furniture
   ========================================================================== */

function SectionHeader({ eyebrow, icon, title, children, className = "" }) {
  return (
    <div className={`max-w-2xl ${className}`}>
      <span className={sectionEyebrow}>
        {icon && <Icon name={icon} width={13} height={13} />}
        {eyebrow}
      </span>
      <h2 className="text-3xl sm:text-4xl font-extrabold tracking-tight mt-5 mb-4 text-balance">{title}</h2>
      <p className="text-lg text-muted leading-relaxed">{children}</p>
    </div>
  );
}

/**
 * Card styling, split by whether the card actually does anything.
 *
 * `cardBase` used to include a lift-and-shadow hover on every card on the page,
 * including the ones that are pure text. Hover feedback is a promise: it says
 * "this responds to you", and every card that rose under the cursor and then
 * did nothing when clicked spent a little of the page's credibility. On a site
 * whose argument is that it tells you the truth about what it does, that is
 * worse than merely untidy.
 *
 * Static cards keep the border transition, so they still feel alive on a
 * pointer without claiming to be a target.
 *
 * Every card on this page is now static. The audience cards come closest to an
 * exception -- they contain a real call to action -- but the clickable thing
 * there is the button inside, not the card, and giving the card the hover of a
 * link would misdirect the cursor by a hundred pixels. No `cardInteractive`
 * constant is defined for the same reason: an unused style waiting for a use is
 * how the original problem got in.
 */
const cardBase =
  "rounded-2xl border border-border transition-colors duration-200 " +
  "hover:border-border/80 animate-slide-up";

/* ==========================================================================
   Hero mockup
   ========================================================================== */

/**
 * Hero product mockup — a stylised recreation of the real exam header and
 * proctoring panel, including the warning counter that appears after a
 * lockdown breach. Deliberately shows a "Warning 1 of 3" state rather than an
 * all-green one: the strike system is the product's most distinctive feature,
 * so the hero should actually depict it.
 */
function BrowserMockup() {
  // "Fullscreen locked" claimed something a browser cannot do. A web page can
// REQUEST fullscreen and detect leaving it; it cannot prevent the operating
// system minimising the window, switching applications, recording the screen,
// or a second device sitting next to the candidate. lockdown.js documents those
// limits accurately; the marketing copy did not, and overstating enforcement to
// an institution buying an exam platform is the kind of claim that gets found
// out during an incident.
const signals = ["Identity verified", "Fullscreen active", "Gaze on screen", "Focus monitored"];

  return (
    <div className="relative w-full max-w-md mx-auto lg:mx-0" aria-hidden="true">
      <div className="absolute -top-8 -right-8 w-40 h-40 bg-primary/20 rounded-full blur-3xl" />
      <div className="absolute -bottom-10 -left-10 w-44 h-44 bg-success/20 rounded-full blur-3xl" />

      <div className="relative rounded-2xl border border-border bg-surface shadow-[0_30px_70px_-30px_rgba(15,23,42,0.35)] overflow-hidden animate-float">
        <div className="flex items-center gap-1.5 px-4 py-3 border-b border-border bg-page">
          <span className="w-2.5 h-2.5 rounded-full bg-danger/70" />
          <span className="w-2.5 h-2.5 rounded-full bg-warning/70" />
          <span className="w-2.5 h-2.5 rounded-full bg-success/70" />
          <span className="ml-3 flex-1 h-5 rounded-md bg-surface border border-border" />
        </div>

        <div className="flex items-center justify-between px-5 py-3 border-b border-border">
          <div className="flex items-center gap-2 min-w-0">
            <span className="w-2 h-2 rounded-full bg-success animate-pulse-soft shrink-0" />
            <span className="text-xs font-semibold text-ink truncate">Data Structures — Final</span>
          </div>
          <span className="text-xs font-mono font-semibold text-ink tabular-nums shrink-0">32:14</span>
        </div>

        <div className="p-5">
          <div className="relative rounded-xl bg-page border border-border h-32 flex items-center justify-center mb-3 overflow-hidden">
            <svg width="38" height="38" viewBox="0 0 24 24" fill="none" stroke="rgb(var(--primary))" strokeWidth="1.5">
              <circle cx="12" cy="9" r="3.4" />
              <path d="M5 20c0-4 3-6.5 7-6.5s7 2.5 7 6.5" />
            </svg>
            <span className="absolute top-2 left-2 inline-flex items-center gap-1 rounded-md bg-surface/90 border border-border px-1.5 py-0.5 text-[10px] font-semibold text-success">
              <span className="w-1.5 h-1.5 rounded-full bg-success" />
              LIVE
            </span>
          </div>

          <div className="flex items-center gap-2 rounded-lg border border-warning/40 bg-warning/10 px-3 py-2 mb-4">
            <Icon name="alert" width={13} height={13} className="text-warning shrink-0" />
            <span className="text-[11px] font-semibold text-warning">Warning 1 of 3 — tab switch logged</span>
          </div>

          <div className="space-y-2.5">
            {signals.map((label) => (
              <div key={label} className="flex items-center justify-between text-xs">
                <span className="text-muted">{label}</span>
                <span className="inline-flex items-center gap-1.5 font-semibold text-success">
                  <Icon name="check" width={12} height={12} />
                  OK
                </span>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

export default function Home() {
  return (
    <div className="min-h-screen flex flex-col bg-page text-ink overflow-x-hidden">
      <a
        href="#main-content"
        className="sr-only focus-visible:not-sr-only focus-visible:fixed focus-visible:top-3 focus-visible:left-3 focus-visible:z-[100] focus-visible:rounded-lg focus-visible:bg-primary focus-visible:px-4 focus-visible:py-2.5 focus-visible:text-sm focus-visible:font-semibold focus-visible:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white focus-visible:ring-offset-2 focus-visible:ring-offset-primary"
      >
        Skip to main content
      </a>

      <Navbar />

      <main id="main-content" className="flex-1">
        <HeroSection />
        <HighlightsSection />
        <WorkflowSection />
        <UseCasesSection />
        <TrustSection />
        <AudiencesSection />
        <FinalCta />
      </main>

      <Footer />
    </div>
  );
}

/* ==========================================================================
   Sections
   ========================================================================== */

function HeroSection() {
  return (
    <section className="relative">
      <div
        className="absolute inset-x-0 top-0 h-[560px] -z-10 opacity-60"
        style={{
          background:
            "radial-gradient(600px circle at 15% 20%, rgba(37,99,235,0.12), transparent 60%), radial-gradient(500px circle at 85% 10%, rgba(22,163,74,0.10), transparent 55%)",
        }}
        aria-hidden="true"
      />

      <div className="max-w-7xl mx-auto px-5 sm:px-6 lg:px-8 pt-16 pb-20 lg:pt-24 lg:pb-28 grid lg:grid-cols-2 gap-14 lg:gap-10 items-center">
        <div className="animate-fade-in">
          <Tagline variant="eyebrow" className="mb-6" />

          <h1 className="text-4xl sm:text-5xl lg:text-[3.4rem] font-extrabold tracking-tight leading-[1.08] mb-5 text-balance">
            Exams you can
            <br className="hidden sm:block" />{" "}
            <span className="bg-gradient-to-r from-primary to-success bg-clip-text text-transparent">actually trust.</span>
          </h1>

          <p className="text-lg text-muted leading-relaxed max-w-xl mb-8">
            Merit.Ai requires fullscreen, verifies every candidate's identity against their ID, and watches the
            session with continuous AI proctoring — detecting focus loss, screen-share interruptions and unusual
            activity, pausing the exam on a breach, and recording server-authoritative strikes in a complete,
            timestamped log.
          </p>

          <div className="flex flex-wrap items-center gap-4 mb-8">
            {/* Two audiences, two destinations. One "Get Started Free" button
                pointing at /register sent institutions to student signup --
                they would create a candidate account, find no way to build an
                exam, and conclude the product did not do what the page above
                said it did. */}
            <Link to="/request-access" className={btnPrimary}>
              Request Institution Access
              <Icon name="arrow" width={16} height={16} />
            </Link>
            <Link to="/register" className={btnGhost}>
              Create Candidate Account
            </Link>
            {/* Candidates could only discover their browser or camera was a
                problem at the moment they tried to start a real exam. */}
            <Link to="/system-check" className="text-sm font-semibold text-primary hover:underline">
              Test your device
            </Link>
          </div>

          <p className="flex flex-wrap items-center gap-x-4 gap-y-1.5 text-xs text-muted">
            {["Runs in the browser", "No candidate downloads", "Candidate accounts are free"].map((claim) => (
              <span key={claim} className="inline-flex items-center gap-1.5">
                <Icon name="check" width={13} height={13} className="text-success" />
                {claim}
              </span>
            ))}
          </p>
        </div>

        <div className="animate-slide-up" style={{ animationDelay: "0.1s" }}>
          <BrowserMockup />
        </div>
      </div>

      <div className="border-y border-border bg-surface">
        <div className="max-w-7xl mx-auto px-5 sm:px-6 lg:px-8 py-8 grid grid-cols-2 lg:grid-cols-4 gap-y-8 lg:gap-y-0 lg:divide-x lg:divide-border">
          {STATS.map((stat) => (
            <div key={stat.label} className="text-center px-3 lg:first:pl-0 lg:last:pr-0">
              <div className="text-3xl sm:text-4xl font-extrabold text-ink tabular-nums tracking-tight">{stat.value}</div>
              <div className="text-xs sm:text-sm text-muted mt-1.5">{stat.label}</div>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

/**
 * Condensed teaser -- four highlights, then a clear door to the full
 * Features page rather than the entire feature/security breakdown inline.
 */
function HighlightsSection() {
  return (
    <section className="max-w-7xl mx-auto px-5 sm:px-6 lg:px-8 py-20 lg:py-24">
      <div className="flex flex-col sm:flex-row sm:items-end sm:justify-between gap-6 mb-14">
        <SectionHeader eyebrow="Platform" title="Built to be trusted, not just timed">
          A proctored exam platform that enforces its rules server-side and tells everyone exactly what it saw.
        </SectionHeader>
        <Link to="/features" className={`${btnGhost} shrink-0`}>
          Explore All Features
          <Icon name="arrow" width={15} height={15} />
        </Link>
      </div>

      <ul className="list-none grid sm:grid-cols-2 lg:grid-cols-4 gap-5">
        {HIGHLIGHTS.map((f, i) => (
          <li
            key={f.title}
            className={`p-6 bg-surface ${cardBase}`}
            style={{ animationDelay: `${i * 0.06}s` }}
          >
            <IconBadge name={f.icon} />
            <h3 className="font-bold text-ink mt-4 mb-2">{f.title}</h3>
            <p className="text-sm text-muted leading-relaxed">{f.desc}</p>
          </li>
        ))}
      </ul>
    </section>
  );
}

function WorkflowSection() {
  return (
    <section className="bg-surface border-y border-border">
      <div className="max-w-7xl mx-auto px-5 sm:px-6 lg:px-8 py-20 lg:py-24">
        <SectionHeader eyebrow="Workflow" title="How it works" className="mb-12">
          Four steps from question bank to graded report.
        </SectionHeader>

        <ol className="list-none grid sm:grid-cols-2 lg:grid-cols-4 gap-6">
          {STEPS.map((step, i) => (
            <li
              key={step.n}
              className={`relative flex flex-col p-6 bg-page ${cardBase}`}
              style={{ animationDelay: `${i * 0.08}s` }}
            >
              <span
                aria-hidden="true"
                className="pointer-events-none absolute top-3 right-4 text-5xl font-extrabold leading-none text-primary/10 select-none"
              >
                {step.n}
              </span>

              <span
                aria-hidden="true"
                className="relative inline-flex items-center justify-center w-10 h-10 rounded-xl brand-gradient text-white text-sm font-extrabold shadow-[0_6px_16px_-6px_rgba(37,99,235,0.7)] mb-4"
              >
                {step.n}
              </span>

              <h3 className="relative font-bold text-ink mb-2">{step.title}</h3>
              <p className="relative text-sm text-muted leading-relaxed">{step.desc}</p>

              {i < STEPS.length - 1 && (
                <span
                  aria-hidden="true"
                  className="hidden lg:flex absolute top-11 -translate-y-1/2 -right-6 w-6 items-center justify-center text-muted/40"
                >
                  <Icon name="arrow" width={15} height={15} />
                </span>
              )}
            </li>
          ))}
        </ol>
      </div>
    </section>
  );
}

function UseCasesSection() {
  return (
    <section className="bg-surface border-y border-border">
      <div className="max-w-7xl mx-auto px-5 sm:px-6 lg:px-8 py-20 lg:py-24">
        <SectionHeader eyebrow="Use cases" icon="briefcase" title="What people actually run on it" className="mb-12">
          Four situations this was built around. If yours is not one of them, the honest answer is
          probably on the Features page, which also lists what the platform cannot do.
        </SectionHeader>

        <div className="grid sm:grid-cols-2 gap-6">
          {USE_CASES.map((item, i) => (
            <div key={item.title} className={`p-6 bg-page ${cardBase}`} style={{ animationDelay: `${i * 0.08}s` }}>
              <span aria-hidden="true"
                    className="inline-flex items-center justify-center w-10 h-10 rounded-xl bg-primary/10 text-primary mb-4">
                <Icon name={item.icon} width={18} height={18} />
              </span>
              <h3 className="font-bold text-ink mb-2">{item.title}</h3>
              <p className="text-sm text-muted leading-relaxed">{item.desc}</p>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

function TrustSection() {
  return (
    <section className="max-w-7xl mx-auto px-5 sm:px-6 lg:px-8 py-20 lg:py-24">
      <SectionHeader eyebrow="Where we stand" icon="shield-check"
                     title="What we claim, and what we don't" className="mb-12">
        An exam platform asking for a webcam in someone's bedroom owes them both halves. Every line
        below is a real property of the software, including the limits.
      </SectionHeader>

      <dl className="grid sm:grid-cols-2 gap-x-8 gap-y-7">
        {TRUST.map((item) => (
          <div key={item.claim}>
            <dt className="flex items-start gap-2.5 font-bold text-ink mb-1.5">
              <Icon name="check" width={16} height={16} className="mt-0.5 shrink-0 text-success" />
              <span>{item.claim}</span>
            </dt>
            <dd className="text-sm text-muted leading-relaxed pl-[26px]">{item.detail}</dd>
          </div>
        ))}
      </dl>

      <p className="mt-8 text-sm text-muted">
        The full list, limits included, is on the{" "}
        <Link to="/features" className="font-semibold text-primary hover:underline">Features</Link>{" "}
        and{" "}
        <Link to="/privacy" className="font-semibold text-primary hover:underline">Privacy</Link> pages.
      </p>
    </section>
  );
}

function AudiencesSection() {
  return (
    <section className="max-w-7xl mx-auto px-5 sm:px-6 lg:px-8 py-20 lg:py-24">
      <SectionHeader eyebrow="Two ways in" title="Built for every role" className="mb-14">
        Candidates sign up in a minute. Institutions get a verified examiner account, set up by our team.
      </SectionHeader>

      <ul className="list-none grid md:grid-cols-2 gap-5 max-w-4xl mx-auto">
        {AUDIENCES.map((a, i) => (
          <li
            key={a.title}
            className={`relative flex flex-col overflow-hidden pt-8 px-7 pb-7 sm:px-8 sm:pb-8 bg-surface ${cardBase}`}
            style={{ animationDelay: `${i * 0.08}s` }}
          >
            <span aria-hidden="true" className="absolute inset-x-0 top-0 h-1 brand-gradient" />

            <IconBadge name={a.icon} tone="surface" />
            <h3 className="font-bold text-lg text-ink mt-4 mb-2">{a.title}</h3>
            <p className="text-sm text-muted leading-relaxed mb-5 md:min-h-[4.25rem]">{a.desc}</p>

            <ul className="list-none space-y-2.5 mb-7 pt-5 border-t border-border">
              {a.bullets.map((b) => (
                <li key={b} className="flex items-start gap-2.5 text-sm text-ink">
                  <span className="mt-0.5 text-success shrink-0">
                    <Icon name="check" width={15} height={15} />
                  </span>
                  {b}
                </li>
              ))}
            </ul>

            <div className="mt-auto">
              <Link to={a.cta.to} className={`w-full ${btnPrimary.replace("py-3", "py-2.5")}`}>
                {a.cta.label}
                <Icon name="arrow" width={15} height={15} />
              </Link>
              <p className="mt-3 text-center text-xs text-muted">{a.note}</p>
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}

function FinalCta() {
  return (
    <section className="max-w-7xl mx-auto px-5 sm:px-6 lg:px-8 pb-20 lg:pb-24">
      <div className="relative overflow-hidden rounded-3xl brand-gradient text-white px-6 py-12 sm:px-12 sm:py-14 text-center shadow-[0_30px_60px_-30px_rgba(23,65,182,0.8)]">
        <div
          className="absolute inset-0 opacity-[0.15]"
          style={{ backgroundImage: "radial-gradient(circle, #fff 1px, transparent 1px)", backgroundSize: "20px 20px" }}
          aria-hidden="true"
        />
        <div className="absolute -top-16 -left-16 w-64 h-64 bg-white/15 rounded-full blur-3xl" aria-hidden="true" />
        <div className="absolute -bottom-20 -right-16 w-72 h-72 bg-white/10 rounded-full blur-3xl" aria-hidden="true" />

        <div className="relative max-w-2xl mx-auto">
          <span className="inline-flex items-center gap-2 rounded-full border border-white/25 bg-white/10 px-3.5 py-1.5 text-xs font-semibold mb-6">
            <Icon name="shield-check" width={13} height={13} />
            Free for candidates
          </span>

          <h2 className="text-3xl sm:text-4xl font-extrabold tracking-tight mb-4 text-balance">
            Ready to run your next exam with confidence?
          </h2>
          <p className="text-white/80 text-base sm:text-lg mb-8">
            Request access for your institution, build your question bank, and publish a fully proctored exam.
            Candidates sign up free.
          </p>

          <div className="flex flex-col sm:flex-row flex-wrap justify-center gap-3 sm:gap-4 mb-8">
            <Link to="/request-access" className={btnPrimaryOnDark}>
              Request Institution Access
              <Icon name="arrow" width={16} height={16} />
            </Link>
            <Link to="/pricing" className={btnGhostOnDark}>
              See Pricing
            </Link>
          </div>

          <ul className="list-none flex flex-wrap items-center justify-center gap-x-6 gap-y-2 text-xs text-white/75 pt-6 border-t border-white/15">
            {/* "Works in any modern browser" was false: Exam.jsx itself recommends
                Chrome or Edge, because screen sharing, fullscreen behaviour,
                battery and network information are not equally available in
                Firefox and Safari. Telling a candidate their browser is fine
                and then failing their system check on exam day is worse than
                saying so here. */}
            {["No credit card required", "Nothing for candidates to install", "Best on Chrome or Edge desktop"].map((item) => (
              <li key={item} className="inline-flex items-center gap-1.5">
                <Icon name="check" width={13} height={13} />
                {item}
              </li>
            ))}
          </ul>
        </div>
      </div>
    </section>
  );
}
