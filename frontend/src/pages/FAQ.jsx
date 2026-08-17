import { useState } from "react";
import { Link } from "react-router-dom";
import Navbar from "../components/Navbar.jsx";
import Footer from "../components/Footer.jsx";
import Icon from "../components/Icon.jsx";
import { btnPrimary, btnGhost, sectionEyebrow } from "../lib/ui.js";

/**
 * FAQ page, grouped by audience/category with a plain accordion (no new dependency.
 */

const CATEGORIES = [
  {
    title: "General",
    icon: "compass",
    items: [
      {
        q: "What is Merit.Ai?",
        a: "An online exam platform that combines exam building, live AI proctoring, sandboxed coding assessments, and role-based dashboards for admins, examiners, and candidates — everything runs in the browser, nothing to install.",
      },
      {
        q: "Is it free to use?",
        a: "Candidates never pay to register or take an exam. Institutions get a verified examiner account set up by our team, with pricing based on cohort size — see the Pricing page for details.",
      },
      {
        q: "What do I need to take an exam?",
        a: "A modern browser (Chrome, Edge, or Firefox recommended), a working webcam and microphone for proctored exams, and a stable internet connection. No app or extension to install.",
      },
    ],
  },
  {
    title: "For Candidates",
    icon: "user",
    items: [
      {
        q: "Do I need to verify my identity before every exam?",
        a: "No — identity verification (face registration plus an ID-card name match) happens once. After both are confirmed, your name and email lock to that identity and every future proctored exam reuses the same verification.",
      },
      {
        q: "What happens if my internet drops mid-exam?",
        a: "Your answers and code save continuously and retry automatically on reconnect. Reloading the page resumes your exact attempt with the correct time remaining — the timer can't be rewound by refreshing.",
      },
      {
        q: "Can I get a retake if something goes wrong?",
        a: "Yes. If a crash, disconnection, or proctoring interruption makes an attempt unusable, your examiner can reset it and give you a clean retake — the previous attempt is kept on record for audit purposes.",
      },
      {
        q: "What if I get flagged unfairly?",
        a: "Every flagged moment is logged with a timestamp, a severity, and often a screenshot, so your examiner (or an admin) reviews exactly what happened rather than relying on an automated verdict alone.",
      },
    ],
  },
  {
    title: "Security & Proctoring",
    icon: "shield-check",
    items: [
      {
        q: "What does the AI proctoring actually watch for?",
        a: "Face presence and identity match, head pose and gaze direction, phones/books/extra people in frame, and sustained loud audio — each signal has to persist for a couple of seconds before it counts as a violation, so a single blink or glance never triggers one.",
      },
      {
        q: "Can it stop someone from using a second device to cheat?",
        a: "No browser-based platform can prevent an OS-level screenshot, a screen recorder, or a phone camera pointed at the monitor, and we say so plainly rather than implying otherwise. What we do is detect and log everything observable from the browser, and require a shared full-screen for the exam window.",
      },
      {
        q: "What happens after three lockdown strikes?",
        a: "The server automatically force-submits the attempt. That decision is made and enforced server-side from a persisted violation log, so refreshing the page or clearing local storage can't reset the count.",
      },
      {
        q: "How is my face and ID data used?",
        a: "Only to verify that the person taking the exam is the person who registered. It's never shared with third parties or used for anything beyond exam integrity, and proctoring stops the moment your attempt is submitted.",
      },
    ],
  },
  {
    title: "For Institutions",
    icon: "briefcase",
    items: [
      {
        q: "How do I create an exam?",
        a: "As an examiner, you build sections, add MCQ, multi-select, or coding questions (with drag-and-drop reordering and bulk MCQ import), set a duration and pass mark, then publish. Published exams can still have their schedule adjusted afterward.",
      },
      {
        q: "How do we control who can take an exam?",
        a: "Exams are scoped to your organization's roster automatically — only enrolled candidates can see or start them. You can also invite specific people to one exam without adding them to your general roster.",
      },
      {
        q: "Can we monitor exams while they're happening?",
        a: "Yes — a live sessions view shows active attempts as they happen, and every submission produces a full violation timeline and a downloadable PDF report.",
      },
      {
        q: "Do coding questions get partial credit?",
        a: "Yes. Marks are awarded proportionally to the number of test cases passed, not all-or-nothing, and grading runs once at submission against both sample and hidden test cases.",
      },
    ],
  },
  {
    title: "Technical",
    icon: "code",
    items: [
      {
        q: "What languages are supported for coding questions?",
        a: "Python and JavaScript today. Each submission runs in an isolated, network-disabled container so candidates' code can never affect the exam environment or each other.",
      },
      {
        q: "What if a proctoring signal is unavailable on my device?",
        a: "Each signal degrades independently rather than breaking the exam — if, say, object detection can't run, that one check reports itself unavailable and stops polling while everything else continues normally.",
      },
      {
        q: "Who can see my exam and identity data?",
        a: "Access is authenticated and scoped by role throughout the platform: a candidate's data is visible only to that candidate, their examiner, and admins — never to another institution or another candidate.",
      },
    ],
  },
];

function AccordionItem({ item, open, onToggle }) {
  return (
    <div className="rounded-2xl border border-border bg-surface overflow-hidden">
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={open}
        className="w-full flex items-center justify-between gap-4 text-left px-5 sm:px-6 py-4 sm:py-5 hover:bg-page/60 transition-colors"
      >
        <span className="font-semibold text-ink text-sm sm:text-base">{item.q}</span>
        <span
          className={`inline-flex items-center justify-center w-7 h-7 rounded-full border border-border text-muted shrink-0 transition-transform duration-200 ${
            open ? "rotate-180 border-primary text-primary" : ""
          }`}
        >
          <Icon name="chevron-down" width={14} height={14} />
        </span>
      </button>
      {open && (
        <div className="px-5 sm:px-6 pb-5 sm:pb-6 -mt-1 animate-slide-up">
          <p className="text-sm text-muted leading-relaxed">{item.a}</p>
        </div>
      )}
    </div>
  );
}

export default function FAQ() {
  // Keyed "categoryIndex-itemIndex" so open state survives category re-renders and multiple
  // items (even across categories) can stay open at once.
  const [openKeys, setOpenKeys] = useState(() => new Set(["0-0"]));

  function toggle(key) {
    setOpenKeys((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }

  return (
    <div className="min-h-screen flex flex-col bg-page text-ink overflow-x-hidden">
      <Navbar />
      <main className="flex-1">
        <section className="relative overflow-hidden">
          <div
            className="absolute inset-x-0 top-0 h-[380px] -z-10 opacity-60"
            style={{
              background:
                "radial-gradient(600px circle at 15% 10%, rgba(37,99,235,0.12), transparent 60%), radial-gradient(500px circle at 85% 0%, rgba(22,163,74,0.10), transparent 55%)",
            }}
            aria-hidden="true"
          />
          <div className="max-w-3xl mx-auto px-5 sm:px-6 lg:px-8 pt-16 pb-10 lg:pt-20 text-center">
            <span className={`${sectionEyebrow} mb-6`}>
              <Icon name="alert" width={13} height={13} />
              FAQ
            </span>
            <h1 className="text-4xl sm:text-5xl font-extrabold tracking-tight mb-5 text-balance">
              Frequently asked questions
            </h1>
            <p className="text-lg text-muted leading-relaxed max-w-2xl mx-auto">
              Straight answers about proctoring, pricing, and how the platform actually works — organized by who's
              asking.
            </p>
          </div>
        </section>

        <section className="max-w-3xl mx-auto px-5 sm:px-6 lg:px-8 pb-20 lg:pb-24">
          <div className="flex flex-col gap-12">
            {CATEGORIES.map((cat, ci) => (
              <div key={cat.title}>
                <div className="flex items-center gap-3 mb-5">
                  <span className="inline-flex items-center justify-center w-9 h-9 rounded-xl bg-primary/10 text-primary shrink-0">
                    <Icon name={cat.icon} width={17} height={17} />
                  </span>
                  <h2 className="text-xl font-extrabold tracking-tight">{cat.title}</h2>
                </div>
                <div className="flex flex-col gap-3">
                  {cat.items.map((item, ii) => {
                    const key = `${ci}-${ii}`;
                    return (
                      <AccordionItem key={key} item={item} open={openKeys.has(key)} onToggle={() => toggle(key)} />
                    );
                  })}
                </div>
              </div>
            ))}
          </div>
        </section>

        <section className="bg-surface border-t border-border">
          <div className="max-w-3xl mx-auto px-5 sm:px-6 lg:px-8 py-16 lg:py-20 text-center">
            <h2 className="text-2xl font-extrabold tracking-tight mb-3">Still have a question?</h2>
            <p className="text-muted mb-8">We read every message that comes through the contact page.</p>
            <div className="flex flex-wrap items-center justify-center gap-4">
              <Link to="/contact" className={btnPrimary}>
                Contact Us
                <Icon name="arrow" width={16} height={16} />
              </Link>
              <Link to="/pricing" className={btnGhost}>
                See Pricing
              </Link>
            </div>
          </div>
        </section>
      </main>
      <Footer />
    </div>
  );
}
