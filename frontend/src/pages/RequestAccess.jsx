import { useState } from "react";
import { Link } from "react-router-dom";
import Navbar from "../components/Navbar.jsx";
import Footer from "../components/Footer.jsx";
import Icon from "../components/Icon.jsx";
import { TextField, TextAreaField } from "../components/FormField.jsx";
import { btnPrimary, btnGhost, sectionEyebrow } from "../lib/ui.js";
import { Api, ApiError } from "../lib/api.js";

/**
 * "Request examiner access" — the public front door for institutions.
 */

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export default function RequestAccess() {
  const [form, setForm] = useState({ firstName: "", lastName: "", email: "", organizationName: "", purpose: "" });
  const [errors, setErrors] = useState({});
  const [touched, setTouched] = useState({});
  const [formError, setFormError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [submitted, setSubmitted] = useState(false);

  function computeErrors(values) {
    const next = {};
    if (!values.firstName.trim()) next.firstName = "First name is required.";
    if (!values.lastName.trim()) next.lastName = "Last name is required.";
    if (!values.email.trim()) next.email = "Email is required.";
    else if (!EMAIL_RE.test(values.email.trim())) next.email = "Enter a valid email address.";
    if (values.organizationName.trim().length < 2) next.organizationName = "Enter your organization's name.";
    // Mirrors the server's min_length=10 -- this is the field the reviewing
    // admin actually reads, so a one-word answer helps nobody.
    if (values.purpose.trim().length < 10) next.purpose = "Tell us a little about how you'll use the platform.";
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

  async function handleSubmit(e) {
    e.preventDefault();
    setFormError("");
    setTouched({ firstName: true, lastName: true, email: true, organizationName: true, purpose: true });
    const next = computeErrors(form);
    setErrors(next);
    if (Object.keys(next).length > 0) return;

    setSubmitting(true);
    try {
      await Api.post("/access-requests", {
        first_name: form.firstName.trim(),
        last_name: form.lastName.trim(),
        email: form.email.trim(),
        organization_name: form.organizationName.trim(),
        purpose: form.purpose.trim(),
      });
      setSubmitted(true);
    } catch (err) {
      setFormError(err instanceof ApiError ? err.message : "Something went wrong. Please try again.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="min-h-screen flex flex-col bg-page text-ink">
      <Navbar />

      <main className="flex-1 flex items-start justify-center px-5 sm:px-6 py-14 lg:py-20">
        <div className="w-full max-w-xl">
          {submitted ? <SuccessPanel email={form.email.trim()} /> : (
            <>
              <div className="mb-8 animate-fade-in">
                <span className={`${sectionEyebrow} mb-4`}>
                  <Icon name="briefcase" width={14} height={14} />
                  For Institutions &amp; Examiners
                </span>
                <h1 className="text-3xl sm:text-4xl font-extrabold tracking-tight mb-3">Request examiner access</h1>
                <p className="text-muted leading-relaxed">
                  Examiner accounts are created by our team, so exams are always run by a verified institution. Tell us
                  a little about yourself and we'll set you up.
                </p>
              </div>

              <div className="rounded-2xl border border-border bg-surface shadow-card p-6 sm:p-8">
                {formError && (
                  <div className="mb-5 flex items-start gap-2.5 rounded-xl border border-danger/30 bg-danger/5 px-4 py-3 text-sm text-danger animate-fade-in">
                    <Icon name="alert" width={16} height={16} className="mt-0.5 shrink-0" />
                    <span>{formError}</span>
                  </div>
                )}

                <form onSubmit={handleSubmit} noValidate>
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-4">
                    <TextField
                      id="firstName"
                      label="First name"
                      icon="user"
                      autoComplete="given-name"
                      autoFocus
                      placeholder="Jane"
                      value={form.firstName}
                      onChange={update("firstName")}
                      onBlur={handleBlur("firstName")}
                      error={touched.firstName ? errors.firstName : undefined}
                    />
                    <TextField
                      id="lastName"
                      label="Last name"
                      icon="user"
                      autoComplete="family-name"
                      placeholder="Doe"
                      value={form.lastName}
                      onChange={update("lastName")}
                      onBlur={handleBlur("lastName")}
                      error={touched.lastName ? errors.lastName : undefined}
                    />
                  </div>

                  <TextField
                    id="email"
                    label="Work email"
                    icon="mail"
                    type="email"
                    autoComplete="email"
                    placeholder="you@institution.edu"
                    value={form.email}
                    onChange={update("email")}
                    onBlur={handleBlur("email")}
                    error={touched.email ? errors.email : undefined}
                  />

                  <TextField
                    id="organizationName"
                    label="Organization name"
                    icon="briefcase"
                    autoComplete="organization"
                    placeholder="Acme Institute of Technology"
                    value={form.organizationName}
                    onChange={update("organizationName")}
                    onBlur={handleBlur("organizationName")}
                    error={touched.organizationName ? errors.organizationName : undefined}
                  />

                  <TextAreaField
                    id="purpose"
                    label="What will you use Merit.Ai for?"
                    rows={4}
                    placeholder="e.g. Running end-of-semester programming assessments for around 300 students."
                    value={form.purpose}
                    onChange={update("purpose")}
                    onBlur={handleBlur("purpose")}
                    error={touched.purpose ? errors.purpose : undefined}
                    hint="A sentence or two is plenty — it helps us set your account up correctly."
                  />

                  <button
                    type="submit"
                    disabled={submitting}
                    className={`${btnPrimary} w-full ${submitting ? "opacity-70 pointer-events-none" : ""}`}
                  >
                    {submitting && <Icon name="spinner" width={16} height={16} className="animate-spin" />}
                    {submitting ? "Submitting…" : "Submit Request"}
                  </button>
                </form>
              </div>

              <p className="mt-6 text-center text-sm text-muted">
                Taking an exam instead?{" "}
                <Link to="/register" className="font-semibold text-primary hover:underline">
                  Create a candidate account
                </Link>
              </p>
            </>
          )}
        </div>
      </main>

      <Footer />
    </div>
  );
}

/**
 * Replaces the form entirely on success rather than showing a banner above it.
 * A submitted request can't be edited, so leaving the filled-in fields on
 * screen would only invite a confused second submission.
 */
function SuccessPanel({ email }) {
  return (
    <div className="rounded-2xl border border-border bg-surface shadow-card p-8 sm:p-10 text-center animate-fade-in">
      <span className="inline-flex items-center justify-center w-16 h-16 rounded-2xl bg-success/10 text-success mb-6">
        <Icon name="check" width={28} height={28} />
      </span>

      <h1 className="text-2xl font-extrabold tracking-tight mb-3">Request received</h1>

      <p className="text-muted leading-relaxed mb-6">
        Thanks — your request is with our team. You'll receive an email
        {email ? (
          <>
            {" "}at <span className="font-semibold text-ink break-words">{email}</span>
          </>
        ) : null}{" "}
        with your login credentials once an administrator approves your account.
      </p>

      <div className="rounded-xl border border-border bg-page px-5 py-4 text-left mb-7">
        <p className="text-xs font-semibold text-ink mb-2.5">What happens next</p>
        <ol className="list-none space-y-2">
          {[
            "An administrator reviews your request.",
            "Your examiner account is created and credentials are emailed to you.",
            "Log in, change your password, and start building exams.",
          ].map((step, i) => (
            <li key={step} className="flex items-start gap-2.5 text-xs text-muted leading-relaxed">
              <span className="inline-flex items-center justify-center w-4 h-4 rounded-full bg-primary/10 text-primary text-[10px] font-bold shrink-0 mt-0.5">
                {i + 1}
              </span>
              {step}
            </li>
          ))}
        </ol>
      </div>

      <div className="flex flex-col sm:flex-row gap-3">
        <Link to="/" className={`${btnGhost} flex-1 justify-center`}>
          Back to Home
        </Link>
        <Link to="/login" className={`${btnPrimary} flex-1 justify-center`}>
          Go to Login
        </Link>
      </div>
    </div>
  );
}
