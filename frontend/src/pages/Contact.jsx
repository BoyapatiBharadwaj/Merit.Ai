import { useState } from "react";
import { Link } from "react-router-dom";
import Navbar from "../components/Navbar.jsx";
import Footer from "../components/Footer.jsx";
import Icon from "../components/Icon.jsx";
import { TextField, TextAreaField } from "../components/FormField.jsx";
import { btnPrimary, btnGhost, sectionEyebrow, fieldInput, fieldLabel } from "../lib/ui.js";

/**
 * Contact page.
 *
 * There is no backend email/ticketing capability anywhere in this codebase
 * (confirmed by inspecting the API for any mail client). Rather than fake a
 * "message sent" success state that silently does nothing server-side, this
 * form opens the visitor's own email client with the message pre-filled via
 * a mailto: link -- honest about what actually happens, and it still works
 * with zero backend changes. Institution/pricing inquiries are pointed at
 * POST /access-requests instead (via the /request-access page), since that
 * one *is* a real, working, admin-reviewed intake path.
 */

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const CONTACT_EMAIL = "hello@merit.ai";

const REASONS = [
  { value: "general", label: "General question" },
  { value: "institution", label: "Institution / pricing" },
  { value: "support", label: "Technical support" },
  { value: "security", label: "Security or privacy question" },
  { value: "other", label: "Something else" },
];

const CONTACT_CARDS = [
  {
    icon: "mail",
    title: "General inquiries",
    desc: "Questions about the platform, a partnership, or press.",
    action: { label: CONTACT_EMAIL, href: `mailto:${CONTACT_EMAIL}` },
  },
  {
    icon: "briefcase",
    title: "Institutions & pricing",
    desc: "Setting up exams for a school, bootcamp, or company.",
    action: { label: "Request examiner access", to: "/request-access" },
  },
  {
    icon: "shield-check",
    title: "Security & privacy",
    desc: "Questions about data handling, biometrics, or retention.",
    action: { label: "Read our privacy notice", to: "/privacy" },
  },
];

export default function Contact() {
  const [form, setForm] = useState({ name: "", email: "", reason: "general", message: "" });
  const [errors, setErrors] = useState({});
  const [touched, setTouched] = useState({});
  const [sent, setSent] = useState(false);

  function computeErrors(values) {
    const next = {};
    if (!values.name.trim()) next.name = "Your name is required.";
    if (!values.email.trim()) next.email = "Email is required.";
    else if (!EMAIL_RE.test(values.email.trim())) next.email = "Enter a valid email address.";
    if (values.message.trim().length < 10) next.message = "Give us a little more detail (at least 10 characters).";
    return next;
  }

  function update(field) {
    return (e) => {
      const value = e.target.value;
      const nextForm = { ...form, [field]: value };
      setForm(nextForm);
      if (touched[field]) setErrors(computeErrors(nextForm));
    };
  }

  function handleBlur(field) {
    return () => {
      setTouched((t) => ({ ...t, [field]: true }));
      setErrors(computeErrors(form));
    };
  }

  function handleSubmit(e) {
    e.preventDefault();
    setTouched({ name: true, email: true, message: true });
    const next = computeErrors(form);
    setErrors(next);
    if (Object.keys(next).length > 0) return;

    const reasonLabel = REASONS.find((r) => r.value === form.reason)?.label || "General question";
    const subject = `[Merit.Ai] ${reasonLabel} — ${form.name.trim()}`;
    const body = `${form.message.trim()}\n\n—\n${form.name.trim()} (${form.email.trim()})`;
    window.location.href = `mailto:${CONTACT_EMAIL}?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(body)}`;
    setSent(true);
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
              <Icon name="send" width={13} height={13} />
              Contact
            </span>
            <h1 className="text-4xl sm:text-5xl font-extrabold tracking-tight mb-5 text-balance">Let's talk</h1>
            <p className="text-lg text-muted leading-relaxed max-w-2xl mx-auto">
              Whether you're evaluating Merit.Ai for your institution or you hit a snag as a candidate, tell us
              what's going on and we'll get back to you.
            </p>
          </div>
        </section>

        <section className="max-w-6xl mx-auto px-5 sm:px-6 lg:px-8 pb-8">
          <ul className="list-none grid sm:grid-cols-3 gap-5 mb-16">
            {CONTACT_CARDS.map((card) => (
              <li key={card.title} className="rounded-2xl border border-border bg-surface p-6 flex flex-col">
                <span className="inline-flex items-center justify-center w-11 h-11 rounded-xl bg-primary/10 text-primary mb-4">
                  <Icon name={card.icon} width={19} height={19} />
                </span>
                <h3 className="font-bold text-ink mb-1.5">{card.title}</h3>
                <p className="text-sm text-muted leading-relaxed mb-4 flex-1">{card.desc}</p>
                {card.action.to ? (
                  <Link to={card.action.to} className="text-sm font-semibold text-primary hover:underline inline-flex items-center gap-1.5">
                    {card.action.label}
                    <Icon name="arrow" width={13} height={13} />
                  </Link>
                ) : (
                  <a href={card.action.href} className="text-sm font-semibold text-primary hover:underline break-all">
                    {card.action.label}
                  </a>
                )}
              </li>
            ))}
          </ul>
        </section>

        <section className="max-w-2xl mx-auto px-5 sm:px-6 lg:px-8 pb-20 lg:pb-24">
          <div className="rounded-2xl border border-border bg-surface shadow-card p-6 sm:p-8">
            {sent ? (
              <div className="text-center py-6 animate-fade-in">
                <span className="inline-flex items-center justify-center w-16 h-16 rounded-2xl bg-success/10 text-success mb-6">
                  <Icon name="check" width={28} height={28} />
                </span>
                <h2 className="text-xl font-extrabold tracking-tight mb-3">Your email client should be opening</h2>
                <p className="text-muted leading-relaxed mb-6">
                  We prefilled a message to <span className="font-semibold text-ink">{CONTACT_EMAIL}</span> with what
                  you wrote. If nothing opened, your browser may have blocked it — just email us directly instead.
                </p>
                <div className="flex flex-col sm:flex-row gap-3 justify-center">
                  <a href={`mailto:${CONTACT_EMAIL}`} className={btnGhost}>
                    Email {CONTACT_EMAIL}
                  </a>
                  <button type="button" onClick={() => setSent(false)} className={btnPrimary}>
                    Edit message
                  </button>
                </div>
              </div>
            ) : (
              <>
                <h2 className="text-lg font-bold text-ink mb-1">Send us a message</h2>
                <p className="text-sm text-muted mb-6">
                  This opens your email client with your message pre-filled — we don't have a live chat, so this is
                  the fastest way to reach a real person.
                </p>
                <form onSubmit={handleSubmit} noValidate>
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-4">
                    <TextField
                      id="name"
                      label="Your name"
                      icon="user"
                      autoComplete="name"
                      autoFocus
                      placeholder="Jane Doe"
                      value={form.name}
                      onChange={update("name")}
                      onBlur={handleBlur("name")}
                      error={touched.name ? errors.name : undefined}
                    />
                    <TextField
                      id="email"
                      label="Your email"
                      icon="mail"
                      type="email"
                      autoComplete="email"
                      placeholder="you@example.com"
                      value={form.email}
                      onChange={update("email")}
                      onBlur={handleBlur("email")}
                      error={touched.email ? errors.email : undefined}
                    />
                  </div>

                  <div className="mb-5">
                    <label htmlFor="reason" className={fieldLabel}>
                      What's this about?
                    </label>
                    <select id="reason" className={fieldInput} value={form.reason} onChange={update("reason")}>
                      {REASONS.map((r) => (
                        <option key={r.value} value={r.value}>
                          {r.label}
                        </option>
                      ))}
                    </select>
                  </div>

                  <TextAreaField
                    id="message"
                    label="Message"
                    rows={5}
                    placeholder="Tell us what you need help with..."
                    value={form.message}
                    onChange={update("message")}
                    onBlur={handleBlur("message")}
                    error={touched.message ? errors.message : undefined}
                  />

                  <button type="submit" className={`${btnPrimary} w-full`}>
                    <Icon name="send" width={16} height={16} />
                    Send Message
                  </button>
                </form>
              </>
            )}
          </div>

          <p className="mt-6 text-center text-sm text-muted">
            Looking to run exams for your institution?{" "}
            <Link to="/request-access" className="font-semibold text-primary hover:underline">
              Request examiner access
            </Link>{" "}
            instead — it goes straight to our team for review.
          </p>
        </section>
      </main>
      <Footer />
    </div>
  );
}
