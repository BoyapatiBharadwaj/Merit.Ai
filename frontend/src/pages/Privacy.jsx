import { Link } from "react-router-dom";
import Navbar from "../components/Navbar.jsx";
import Footer from "../components/Footer.jsx";
import Icon from "../components/Icon.jsx";
import { sectionEyebrow } from "../lib/ui.js";

/**
 * What Merit.Ai actually collects and why -- linked from Register.jsx's
 * consent checkboxes.
 *
 * Written directly from the real data flows in this codebase (face_service,
 * ocr_service, proctor_service, code_runner_service), not generic
 * boilerplate -- every claim below is something the app genuinely does.
 * This is a plain-language notice, not a Terms of Service contract: a real
 * legal ToS binding candidates and institutions needs an actual lawyer's
 * review before this platform handles real exams, and nothing here should
 * be mistaken for that.
 */
const COLLECTS = [
  {
    icon: "camera",
    title: "A registered face photo",
    body: "Captured once from your webcam during account setup and stored as a numeric face signature, not the raw photo, for comparing against your face during future exams.",
  },
  {
    icon: "doc",
    title: "Your ID card image and the name read off it",
    body: "Captured once to confirm your registered name matches a government or institution-issued ID. Used only for that one comparison.",
  },
  {
    icon: "eye",
    title: "Periodic webcam frames during a proctored exam",
    body: "Sampled every few seconds while a proctored exam is in progress to check that you're the one present and visible -- not a continuous recording, and not retained once that check completes, except for the flagged moments below.",
  },
  {
    icon: "alert",
    title: "A snapshot at the moment of a flagged violation",
    body: "If proctoring flags something -- another face in frame, a phone, leaving fullscreen -- the frame at that moment is saved so your examiner can review what actually happened, alongside a timestamped log entry.",
  },
  {
    icon: "mic",
    title: "Microphone audio levels, not recordings",
    body: "Analyzed in your browser in real time for sustained loud speech; only the measurement (and, if triggered, a text log entry) is sent onward, never the audio itself.",
  },
  {
    icon: "code",
    title: "Code you submit for coding questions",
    body: "Run in an isolated, network-disabled container solely to grade it against test cases, then discarded once grading completes.",
  },
];

export default function Privacy() {
  return (
    <div className="min-h-screen flex flex-col bg-page text-ink">
      <Navbar />
      <main className="flex-1 max-w-3xl mx-auto w-full px-5 sm:px-6 py-14">
        <span className={`${sectionEyebrow} mb-4`}>
          <Icon name="shield-check" width={13} height={13} />
          Privacy &amp; Proctoring Data
        </span>
        <h1 className="text-3xl font-extrabold tracking-tight mb-4">What Merit.Ai collects, and why</h1>
        <p className="text-muted leading-relaxed mb-10">
          Proctoring only works if it can see and verify you, so this platform does collect real biometric and session
          data during identity verification and proctored exams. This page describes exactly what, in plain language.
          It is not a substitute for a full Terms of Service -- an institution deploying this platform for real exams
          should have both reviewed by counsel before candidates' data is at stake.
        </p>

        <div className="flex flex-col gap-5 mb-10">
          {COLLECTS.map((item) => (
            <div key={item.title} className="flex items-start gap-4 rounded-2xl border border-border bg-surface shadow-card p-5">
              <span className="inline-flex items-center justify-center w-10 h-10 rounded-xl bg-primary/10 text-primary shrink-0">
                <Icon name={item.icon} width={18} height={18} />
              </span>
              <div>
                <h2 className="text-sm font-bold text-ink mb-1">{item.title}</h2>
                <p className="text-sm text-muted leading-relaxed">{item.body}</p>
              </div>
            </div>
          ))}
        </div>

        <div className="rounded-2xl border border-border bg-page/60 p-5 text-sm text-muted leading-relaxed">
          <p className="font-semibold text-ink mb-1.5">What this platform does not do</p>
          <p>
            Proctoring stops the moment your attempt is submitted -- nothing is captured outside an active, proctored
            exam. Face and ID data are used only to verify identity for exams on this platform, never shared with
            third parties, and never used for any purpose beyond exam integrity.
          </p>
        </div>

        <Link to="/register" className="inline-flex items-center gap-1.5 text-sm font-semibold text-primary hover:underline mt-8">
          <Icon name="chevron-left" width={14} height={14} />
          Back to registration
        </Link>
      </main>
      <Footer />
    </div>
  );
}
