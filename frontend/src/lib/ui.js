/**
 * Shared className recipes so every button/badge across the site (nav, hero, cards, footer,
 * final CTA) uses the exact same padding, radius, and hover behavior.
 */

// Every shared button/link recipe includes a visible focus-visible ring so keyboard users get a
// clear, on-brand indicator (not just the browser default outline, which the hover -translate-y
// transforms can throw off visually).
const focusRing = "outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2 focus-visible:ring-offset-page";
const focusRingOnDark = "outline-none focus-visible:ring-2 focus-visible:ring-white focus-visible:ring-offset-2 focus-visible:ring-offset-primary";

export const btnPrimary =
  "inline-flex items-center justify-center gap-2 rounded-xl brand-gradient " +
  `px-5 py-3 text-sm font-semibold text-white shadow-[0_1px_2px_rgba(15,23,42,0.08)] transition-all duration-200 ${focusRing} ` +
  "hover:shadow-[0_12px_24px_-8px_rgba(37,99,235,0.5)] hover:-translate-y-0.5 active:translate-y-0";

export const btnGhost =
  "inline-flex items-center justify-center gap-2 rounded-xl border border-border bg-surface px-5 py-3 text-sm " +
  `font-semibold text-ink transition-all duration-200 ${focusRing} hover:border-primary hover:text-primary hover:-translate-y-0.5 active:translate-y-0`;

export const btnGhostOnDark =
  "inline-flex items-center justify-center gap-2 rounded-xl border border-white/30 px-5 py-3 text-sm font-semibold " +
  `text-white transition-all duration-200 ${focusRingOnDark} hover:bg-white/10 hover:-translate-y-0.5 active:translate-y-0`;

export const btnPrimaryOnDark =
  "inline-flex items-center justify-center gap-2 rounded-xl bg-white px-5 py-3 text-sm font-semibold text-primary " +
  `shadow-[0_1px_2px_rgba(15,23,42,0.08)] transition-all duration-200 ${focusRingOnDark} hover:shadow-lg hover:-translate-y-0.5 active:translate-y-0`;

export const sectionEyebrow =
  "inline-flex items-center gap-2 rounded-full border border-border bg-surface px-3.5 py-1.5 text-xs font-semibold text-primary";

/**
 * Shared plain-input/textarea/select recipes for the hand-rolled forms
 * outside FormField.jsx's TextField/PasswordField (the exam builder's
 * Create Exam / Add Question / Add Section / Bulk Import forms).
 */
export const fieldInput =
  "w-full rounded-xl border border-border bg-input text-ink px-4 py-2.5 text-sm outline-none transition-colors " +
  "focus:border-primary focus:ring-4 focus:ring-primary/10";
export const fieldLabel = "block text-sm font-semibold text-ink mb-1.5";

export const fieldInputCompact =
  "w-full rounded-lg border border-border bg-input text-ink px-3 py-2 text-sm outline-none transition-colors " +
  "focus:border-primary focus:ring-4 focus:ring-primary/10";
export const fieldLabelCompact = "block text-xs font-semibold text-muted mb-1";
