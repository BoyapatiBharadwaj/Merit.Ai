import { Link } from "react-router-dom";
import Navbar from "../components/Navbar.jsx";
import Footer from "../components/Footer.jsx";
import Icon, { IconBadge } from "../components/Icon.jsx";
import { btnPrimary, btnGhost, sectionEyebrow } from "../lib/ui.js";

/**
 * Dedicated features + security deep-dive, linked from the top nav.
 */

const PILLARS = [
  {
    title: "Enforced Exam Lockdown",
    desc: "Fullscreen is required to interact. Leaving it, switching tabs, or minimising pauses the exam behind a blocking overlay and costs a strike — three ends the attempt automatically.",
    icon: "maximize",
    visual: "strikes",
  },
  {
    title: "Two-Step Identity Verification",
    desc: "Candidates register a face and pass an ID-card name match before their first proctored exam. Once verified, their name and email are locked to that identity.",
    icon: "shield-check",
    visual: "identity",
  },
];

const FEATURES = [
  {
    title: "Multi-Signal AI Proctoring",
    desc: "Face matching with anti-spoofing, gaze and head-pose estimation, and detection of phones, books, and extra people in frame.",
    icon: "eye",
  },
  {
    title: "Sandboxed Coding Questions",
    desc: "Python and JavaScript run against hidden and sample tests in isolated, network-disabled containers, scored per test case passed.",
    icon: "code",
  },
  {
    title: "Auto-Save & Recovery",
    desc: "Answers and code save continuously with retry-on-reconnect. If a connection drops, candidates resume exactly where they left off.",
    icon: "shield",
  },
  {
    title: "Live Monitoring & Reports",
    desc: "Examiners watch active attempts in real time; every submission produces a PDF report backed by a full violation log.",
    icon: "chart",
  },
  {
    title: "Drag-and-Drop Exam Builder",
    desc: "Sections, MCQs, multi-select and coding questions reorder by drag, with bulk MCQ import for question banks you already have.",
    icon: "layout",
  },
  {
    title: "Organizations & Rosters",
    desc: "Exams are scoped to an institution's roster automatically — only enrolled candidates (or an explicit per-exam invite) can ever see or start one.",
    icon: "users",
  },
];

const SECURITY_LAYERS = [
  {
    icon: "maximize",
    title: "Window lockdown",
    desc: "Fullscreen is mandatory. Exiting it, switching tabs, minimising, or clicking away pauses the exam behind a blocking overlay the candidate must dismiss by returning to fullscreen.",
  },
  {
    icon: "flag",
    title: "Three-strike enforcement",
    desc: "Each breach costs a strike and is shown to the candidate in real time. On the third, the server force-submits the attempt — the decision is made server-side, so refreshing the page can't reset the count.",
  },
  {
    icon: "lock",
    title: "Input restrictions",
    desc: "Copy, paste, cut, right-click, text selection, drag, and devtools shortcuts are all blocked for the duration of the attempt.",
  },
  {
    icon: "user",
    title: "Identity binding",
    desc: "Face registration plus an ID-card name match are both required before a candidate's first proctored exam, and their name and email freeze once verified.",
  },
  {
    icon: "eye",
    title: "Continuous AI observation",
    desc: "Face matching with anti-spoofing, gaze and head-pose tracking, object and multi-person detection, plus voice-activity analysis — sampled throughout the attempt.",
  },
  {
    icon: "doc",
    title: "Complete audit trail",
    desc: "Every signal, warning, and lockdown breach is timestamped and severity-graded. Examiners get the full per-attempt timeline, and each candidate's PDF report carries the violation summary.",
  },
];

const cardBase =
  "rounded-2xl border border-border transition-all duration-200 " +
  "hover:border-primary/30 hover:-translate-y-1 hover:shadow-[0_16px_40px_-16px_rgba(15,23,42,0.18)] animate-slide-up";

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

function StrikeVisual() {
  return (
    <div className="rounded-xl border border-border bg-page p-4" aria-hidden="true">
      <div className="flex items-center justify-between mb-3">
        <span className="text-xs font-semibold text-muted">Lockdown strikes</span>
        <span className="text-xs font-bold text-warning tabular-nums">1 of 3</span>
      </div>
      <div className="flex gap-1.5 mb-3">
        <span className="h-1.5 flex-1 rounded-full bg-warning" />
        <span className="h-1.5 flex-1 rounded-full bg-border" />
        <span className="h-1.5 flex-1 rounded-full bg-border" />
      </div>
      <p className="text-[11px] text-muted">Third strike auto-submits the attempt.</p>
    </div>
  );
}

function IdentityVisual() {
  const gates = ["Face registered", "ID card name matched"];
  return (
    <div className="rounded-xl border border-border bg-page p-4 space-y-2.5" aria-hidden="true">
      {gates.map((gate) => (
        <div key={gate} className="flex items-center gap-2.5">
          <span className="inline-flex items-center justify-center w-5 h-5 rounded-full bg-success/15 text-success shrink-0">
            <Icon name="check" width={11} height={11} />
          </span>
          <span className="text-xs text-ink">{gate}</span>
        </div>
      ))}
      <div className="flex items-center gap-2.5 pt-2.5 border-t border-border">
        <span className="inline-flex items-center justify-center w-5 h-5 rounded-full bg-primary/15 text-primary shrink-0">
          <Icon name="lock" width={11} height={11} />
        </span>
        <span className="text-xs font-semibold text-ink">Identity locked</span>
      </div>
    </div>
  );
}

export default function Features() {
  return (
    <div className="min-h-screen flex flex-col bg-page text-ink overflow-x-hidden">
      <Navbar />
      <main className="flex-1">
        {/* Page header */}
        <section className="relative overflow-hidden">
          <div
            className="absolute inset-x-0 top-0 h-[420px] -z-10 opacity-60"
            style={{
              background:
                "radial-gradient(600px circle at 15% 10%, rgba(37,99,235,0.12), transparent 60%), radial-gradient(500px circle at 85% 0%, rgba(22,163,74,0.10), transparent 55%)",
            }}
            aria-hidden="true"
          />
          <div className="max-w-4xl mx-auto px-5 sm:px-6 lg:px-8 pt-16 pb-10 lg:pt-20 text-center">
            <span className={`${sectionEyebrow} mb-6`}>
              <Icon name="grid" width={13} height={13} />
              Platform
            </span>
            <h1 className="text-4xl sm:text-5xl font-extrabold tracking-tight mb-5 text-balance">
              Everything a remote exam needs
            </h1>
            <p className="text-lg text-muted leading-relaxed max-w-2xl mx-auto">
              From identity verification to sandboxed code grading, Merit.Ai handles the full lifecycle of a proctored
              assessment — not just the timer and the submit button. Here&apos;s the whole platform, in detail.
            </p>
          </div>
        </section>

        {/* Feature grid */}
        <section className="max-w-7xl mx-auto px-5 sm:px-6 lg:px-8 pb-20 lg:pb-24">
          <ul className="list-none grid sm:grid-cols-2 lg:grid-cols-12 gap-5">
            {PILLARS.map((p, i) => (
              <li
                key={p.title}
                className={`sm:col-span-2 lg:col-span-6 flex flex-col p-7 sm:p-8 bg-surface ${cardBase}`}
                style={{ animationDelay: `${i * 0.05}s` }}
              >
                <IconBadge name={p.icon} />
                <h3 className="text-lg font-bold text-ink mt-5 mb-2.5">{p.title}</h3>
                <p className="text-sm text-muted leading-relaxed mb-6">{p.desc}</p>
                <div className="mt-auto">{p.visual === "strikes" ? <StrikeVisual /> : <IdentityVisual />}</div>
              </li>
            ))}

            {FEATURES.map((f, i) => (
              <li
                key={f.title}
                className={`sm:col-span-1 lg:col-span-4 p-6 bg-surface ${cardBase}`}
                style={{ animationDelay: `${(i + 2) * 0.05}s` }}
              >
                <IconBadge name={f.icon} />
                <h3 className="font-bold text-ink mt-4 mb-2">{f.title}</h3>
                <p className="text-sm text-muted leading-relaxed">{f.desc}</p>
              </li>
            ))}
          </ul>
        </section>

        {/* Security deep-dive */}
        <section className="relative overflow-hidden bg-accent border-y border-border">
          <div
            className="absolute inset-0"
            style={{
              backgroundImage: "radial-gradient(circle, rgb(var(--text) / 0.06) 1px, transparent 1px)",
              backgroundSize: "22px 22px",
            }}
            aria-hidden="true"
          />
          <div className="absolute -top-24 -right-24 w-96 h-96 bg-primary/10 rounded-full blur-3xl" aria-hidden="true" />
          <div className="absolute -bottom-24 -left-24 w-96 h-96 bg-success/10 rounded-full blur-3xl" aria-hidden="true" />

          <div className="relative max-w-7xl mx-auto px-5 sm:px-6 lg:px-8 py-20 lg:py-24">
            <SectionHeader
              eyebrow="Security & Integrity"
              icon="shield-check"
              title="Six layers between a candidate and a shortcut"
              className="mb-14"
            >
              Most tools log that something happened. Merit.Ai stops the exam, tells the candidate what they did, and
              ends the attempt if it keeps happening — with every decision made on the server, not in the browser.
            </SectionHeader>

            <ol className="list-none grid sm:grid-cols-2 lg:grid-cols-3 gap-5 mb-12">
              {SECURITY_LAYERS.map((layer, i) => (
                <li
                  key={layer.title}
                  className={`relative p-6 bg-surface ${cardBase}`}
                  style={{ animationDelay: `${i * 0.05}s` }}
                >
                  <span aria-hidden="true" className="absolute top-6 right-6 text-xs font-bold tabular-nums text-muted/50">
                    {String(i + 1).padStart(2, "0")}
                  </span>
                  <IconBadge name={layer.icon} />
                  <h3 className="font-bold text-ink mt-4 mb-2">{layer.title}</h3>
                  <p className="text-sm text-muted leading-relaxed">{layer.desc}</p>
                </li>
              ))}
            </ol>

            <div className="rounded-2xl border border-warning/30 bg-warning/5 p-6 sm:p-8">
              <div className="flex flex-col sm:flex-row gap-5">
                <span className="inline-flex items-center justify-center w-11 h-11 rounded-xl bg-warning/15 text-warning shrink-0">
                  <Icon name="alert" width={19} height={19} />
                </span>
                <div className="min-w-0">
                  <h3 className="font-bold text-ink mb-2">And what we don&apos;t claim</h3>
                  <p className="text-sm text-muted leading-relaxed mb-4">
                    No browser-based platform can prevent an operating-system screenshot, a screen recorder, or a
                    second device pointed at the monitor — including every vendor that implies otherwise. What
                    Merit.Ai does is intercept capture shortcuts, clear the clipboard, and record each attempt as a
                    high-severity violation on the candidate&apos;s report, so an invigilator sees exactly what happened.
                  </p>
                  <p className="text-sm text-muted/80 leading-relaxed">
                    For environments that need OS-level enforcement, the platform is designed to sit behind a
                    dedicated lockdown browser.
                  </p>
                </div>
              </div>
            </div>
          </div>
        </section>

        {/* CTA */}
        <section className="max-w-7xl mx-auto px-5 sm:px-6 lg:px-8 py-20 lg:py-24 text-center">
          <h2 className="text-2xl sm:text-3xl font-extrabold tracking-tight mb-4 text-balance">
            See it running your own exam
          </h2>
          <p className="text-muted mb-8 max-w-xl mx-auto">
            Create a free candidate account to try the exam experience, or check pricing for institutions.
          </p>
          <div className="flex flex-wrap items-center justify-center gap-4">
            <Link to="/register" className={btnPrimary}>
              Get Started Free
              <Icon name="arrow" width={16} height={16} />
            </Link>
            <Link to="/pricing" className={btnGhost}>
              See Pricing
            </Link>
          </div>
        </section>
      </main>
      <Footer />
    </div>
  );
}
