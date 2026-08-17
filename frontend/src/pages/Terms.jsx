import { Link } from "react-router-dom";
import Navbar from "../components/Navbar.jsx";
import Footer from "../components/Footer.jsx";
import Icon from "../components/Icon.jsx";
import { sectionEyebrow } from "../lib/ui.js";

/**
 * Terms of Service.
 */
export const TERMS_VERSION = "2026-08-05";

const SECTIONS = [
  {
    title: "Who these terms are between",
    body: "Merit.Ai is deployed by an institution -- a college, university, or employer -- who runs exams on it. " +
      "Your relationship over marks, eligibility, appeals and academic misconduct is with that institution, not with " +
      "the software. Merit.Ai records what happened; the institution decides what it means.",
  },
  {
    title: "Your account",
    body: "You must register with an email address you control and confirm it with the code sent to it. Your name " +
      "must match the identity document you verify, because they are compared to each other -- once that match " +
      "succeeds, your name and email are locked and only an administrator can change them. Do not share your " +
      "account. An exam sat by anyone other than you is a matter for your institution.",
  },
  {
    title: "What is monitored during a proctored exam",
    body: "Your camera, microphone and shared screen are analysed while a proctored exam is in progress, and leaving " +
      "fullscreen, switching away from the exam, or stopping screen sharing is detected and recorded. Everything " +
      "collected is listed on the privacy page. Enough recorded breaches will end the exam and submit the answers " +
      "you have given so far.",
  },
  {
    title: "What the monitoring is, and is not",
    body: "Automated signals are evidence, not a verdict. A flag means the system saw something worth a person " +
      "looking at -- a second face, a phone-shaped object, the window losing focus -- and it can be wrong: a sibling " +
      "walking behind you, a reflection, a notification stealing focus. No conclusion about misconduct is reached " +
      "automatically. A human reviewer decides, and you are entitled to ask your institution what was recorded.",
  },
  {
    title: "What this software cannot do",
    body: "A web page cannot lock your computer. Merit.Ai can require fullscreen and detect leaving it, but it " +
      "cannot stop the operating system minimising the window, prevent another application or device being used, or " +
      "see anything outside the camera's view. Any claim otherwise, from anyone, is wrong.",
  },
  {
    title: "Your answers and your results",
    body: "Answers are saved as you work and again when you submit. Once submitted -- by you, or automatically when " +
      "time expires -- they cannot be changed. If a technical failure disrupts your exam, your examiner can grant a " +
      "retake; the earlier attempt is kept rather than deleted, so what happened can still be examined afterwards.",
  },
  {
    title: "Acceptable use",
    body: "Do not attempt to interfere with the proctoring, extract questions or answer keys, submit code intended " +
      "to break out of the execution sandbox, or access another candidate's data. Coding answers run in a sandbox " +
      "with no network access, limited CPU and memory, and a hard timeout.",
  },
  {
    title: "Availability",
    body: "This software is provided as-is by whoever deployed it. It has no uptime guarantee from the project " +
      "itself. If an exam is disrupted by an outage, that is between you and your institution -- and the attempt " +
      "record is preserved to support exactly that conversation.",
  },
  {
    title: "Changes",
    body: `These terms are versioned. This is version ${TERMS_VERSION}. The version you accepted is recorded with ` +
      "your account, so a later change cannot be applied to you retroactively without your being asked again.",
  },
];

export default function Terms() {
  return (
    <div className="min-h-screen flex flex-col bg-page text-ink">
      <Navbar />
      <main className="flex-1 max-w-3xl mx-auto w-full px-5 sm:px-6 py-12 sm:py-16">
        <span className={sectionEyebrow}>
          <Icon name="doc" width={13} height={13} />
          Terms of Service
        </span>
        <h1 className="text-3xl font-extrabold tracking-tight mb-4">Terms of Service</h1>
        <p className="text-muted leading-relaxed mb-3">
          Version {TERMS_VERSION}. What you are agreeing to when you create an account and sit an exam on this
          platform, in plain language.
        </p>
        <div className="mb-10 flex items-start gap-2.5 rounded-xl border border-warning/30 bg-warning/5 px-4 py-3">
          <span className="text-warning mt-0.5 shrink-0"><Icon name="alert" width={15} height={15} /></span>
          <p className="text-sm text-ink leading-relaxed">
            This is written from what the software actually does, not from a legal template — and it has not been
            reviewed by a lawyer. An institution deploying Merit.Ai for real exams should have counsel review this
            and the{" "}
            <Link to="/privacy" className="font-semibold text-primary hover:underline">privacy notice</Link>{" "}
            before candidates' results and biometric data are at stake.
          </p>
        </div>

        <div className="flex flex-col gap-5">
          {SECTIONS.map((section) => (
            <section key={section.title} className="rounded-2xl border border-border bg-surface shadow-card p-5">
              <h2 className="font-bold text-ink mb-2">{section.title}</h2>
              <p className="text-sm text-muted leading-relaxed">{section.body}</p>
            </section>
          ))}
        </div>

        <p className="mt-10 text-sm text-muted">
          See also:{" "}
          <Link to="/privacy" className="font-semibold text-primary hover:underline">
            what Merit.Ai collects, and why
          </Link>
          .
        </p>
      </main>
      <Footer />
    </div>
  );
}
