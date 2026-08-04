import { useState } from "react";
import Icon from "./Icon.jsx";

// text-base (16px), not text-sm (14px): iOS Safari zooms the whole page in
// on focus for any input under 16px, which on a form with several fields in
// a row turns "tap the next field" into "tap, wait for the zoom, pinch back
// out, then tap again." py-3.5 brings real rendered height to ~52-54px, the
// same target used for buttons so a field and the button below it read as
// one consistent control size, not two different systems.
//
// Six states live here, not five: default (border-border), hover
// (border-primary/40 -- a preview, not a commitment), focus (solid
// border-primary + a soft 3px halo rather than the previous thick 4px ring,
// tuned to sit closer to a native browser focus ring than a UI-kit glow),
// error (border-danger, its own ring tint, and never JUST a color change --
// see the icon + text that always accompany it below), and disabled
// (dimmed, no pointer, border flattened) -- autofill is handled separately
// in index.css since neither Tailwind nor inline styles can override a
// browser's own autofill paint.
const baseInput =
  "w-full rounded-xl border bg-input text-ink placeholder:text-muted/60 py-3.5 text-base outline-none " +
  "transition-colors disabled:opacity-60 disabled:cursor-not-allowed disabled:bg-page disabled:border-border";

function stateClasses(error) {
  return error
    ? "border-danger hover:border-danger focus:border-danger focus:ring-[3px] focus:ring-danger/[0.14]"
    : "border-border hover:border-primary/40 focus:border-primary focus:ring-[3px] focus:ring-primary/[0.12]";
}

// Shared by every label below: the field's own icon/label pick up the brand
// color the instant a person focuses the input, via CSS :focus-within on the
// wrapping `group` rather than tracked focus state -- one less thing that
// can drift out of sync with the input's real focus.
const labelCls = "block text-sm font-semibold text-ink mb-2 transition-colors duration-150 group-focus-within:text-primary";

export function TextField({ id, label, icon, error, valid, className = "", ...props }) {
  // `valid` is opt-in (callers pass it once a field has real validation),
  // so a plain required-but-unvalidated field never shows a false-positive
  // checkmark just because it happens to be non-empty. It only ever appears
  // once the field is both touched AND correct -- never mid-typing -- so the
  // green tick reads as "this is done" rather than flickering on every
  // keystroke that happens to pass validation for a moment.
  const showValid = valid && !error;
  return (
    <div className={`mb-5 group ${className}`}>
      <label htmlFor={id} className={labelCls}>
        {label}
      </label>
      <div className="relative">
        {icon && (
          <span
            className={`pointer-events-none absolute left-3.5 top-1/2 -translate-y-1/2 transition-colors duration-150 ${
              error ? "text-danger" : "text-muted group-focus-within:text-primary"
            }`}
          >
            <Icon name={icon} width={17} height={17} />
          </span>
        )}
        <input
          id={id}
          name={id}
          className={`${baseInput} ${icon ? "pl-10" : "pl-4"} ${showValid ? "pr-10" : "pr-4"} ${stateClasses(error)}`}
          aria-invalid={!!error}
          aria-describedby={error ? `${id}-error` : undefined}
          {...props}
        />
        {showValid && (
          <span className="pointer-events-none absolute right-3.5 top-1/2 -translate-y-1/2 text-success animate-fade-in" aria-hidden="true">
            <Icon name="check" width={16} height={16} />
          </span>
        )}
      </div>
      {error && (
        // The icon is what keeps this from being a color-only error signal --
        // a red border alone is invisible to color-blind users and to anyone
        // on a washed-out screen; the icon and the text both say "error"
        // independent of the red.
        <p id={`${id}-error`} className="mt-1.5 flex items-start gap-1.5 text-xs text-danger animate-fade-in">
          <Icon name="alert" width={13} height={13} className="mt-0.5 shrink-0" />
          <span>{error}</span>
        </p>
      )}
    </div>
  );
}

/** Multi-line sibling of TextField. Shares `baseInput` so a textarea can
 * never drift away from the inputs sitting next to it in the same form. */
export function TextAreaField({ id, label, error, hint, rows = 4, className = "", ...props }) {
  return (
    <div className={`mb-5 group ${className}`}>
      <label htmlFor={id} className={labelCls}>
        {label}
      </label>
      <textarea
        id={id}
        name={id}
        rows={rows}
        className={`${baseInput} px-4 py-3 leading-relaxed resize-y ${stateClasses(error)}`}
        aria-invalid={!!error}
        aria-describedby={error ? `${id}-error` : hint ? `${id}-hint` : undefined}
        {...props}
      />
      {hint && !error && (
        <p id={`${id}-hint`} className="mt-1.5 text-xs text-muted">
          {hint}
        </p>
      )}
      {error && (
        <p id={`${id}-error`} className="mt-1.5 flex items-start gap-1.5 text-xs text-danger">
          <Icon name="alert" width={13} height={13} className="mt-0.5 shrink-0" />
          <span>{error}</span>
        </p>
      )}
    </div>
  );
}

// Length + character-class variety, scored 0-4. Simple and fully transparent
// on purpose -- this is feedback to nudge a stronger password while typing,
// not a security gate (the real rule, 8+ characters, is still enforced by
// Register.jsx's own validation regardless of what this reports).
const STRENGTH_LEVELS = [
  { label: "Very weak", barClass: "bg-danger", textClass: "text-danger" },
  { label: "Weak", barClass: "bg-danger", textClass: "text-danger" },
  { label: "Fair", barClass: "bg-warning", textClass: "text-warning" },
  { label: "Good", barClass: "bg-success", textClass: "text-success" },
  { label: "Strong", barClass: "bg-success", textClass: "text-success" },
];

function passwordStrengthScore(password) {
  if (!password) return 0;
  // Length dominates real-world strength far more than character variety --
  // a short password is never allowed to score above "Weak" just because it
  // happens to mix a digit and a symbol in; four random characters are
  // brute-forceable no matter what those characters are.
  if (password.length < 8) return password.length >= 4 ? 1 : 0;
  let score = 1;
  if (password.length >= 12) score++;
  if (/[a-z]/.test(password) && /[A-Z]/.test(password)) score++;
  if (/\d/.test(password)) score++;
  if (/[^A-Za-z0-9]/.test(password)) score++;
  return Math.min(score, 4);
}

// Shown as a checklist *before* the field ever turns red, on the theory that
// "here's what you need" is more useful up front than "you got it wrong"
// after the fact. Purely advisory except the first rule -- see the note on
// passwordStrengthScore above, the same 8-character floor is what
// Register.jsx actually enforces.
const REQUIREMENTS = [
  { test: (p) => p.length >= 8, label: "At least 8 characters" },
  { test: (p) => /[A-Z]/.test(p), label: "One uppercase letter" },
  { test: (p) => /\d/.test(p), label: "One number" },
  { test: (p) => /[^A-Za-z0-9]/.test(p), label: "One special character" },
];

function PasswordStrengthMeter({ password }) {
  const score = passwordStrengthScore(password);
  const level = STRENGTH_LEVELS[score];
  return (
    <div className="mt-3 animate-fade-in">
      <ul className="list-none space-y-1 mb-2.5">
        {REQUIREMENTS.map((req) => {
          const met = req.test(password);
          return (
            <li key={req.label} className={`flex items-center gap-1.5 text-[11px] transition-colors ${met ? "text-success" : "text-muted"}`}>
              <span
                className={`inline-flex items-center justify-center w-3.5 h-3.5 rounded-full shrink-0 ${
                  met ? "bg-success/15" : "border border-border"
                }`}
                aria-hidden="true"
              >
                {met && <Icon name="check" width={8} height={8} />}
              </span>
              {req.label}
            </li>
          );
        })}
      </ul>
      <div className="flex gap-1" aria-hidden="true">
        {[0, 1, 2, 3].map((i) => (
          <span key={i} className={`h-1 flex-1 rounded-full transition-colors duration-200 ${i < score ? level.barClass : "bg-border"}`} />
        ))}
      </div>
      {/* aria-live rather than aria-hidden on the bars above: a screen reader
          user still gets the same feedback, just as words instead of a bar. */}
      <p className={`mt-1 text-[11px] font-semibold ${level.textClass}`} aria-live="polite">
        Password strength: {level.label}
      </p>
    </div>
  );
}

export function PasswordField({ id, label, labelExtra, error, hint, showStrength, className = "", ...props }) {
  const [show, setShow] = useState(false);
  // The requirements/strength block only appears once the person has
  // actually focused this field, not the instant the page loads -- a wall of
  // rules above an empty, untouched input reads as a form scolding you
  // before you've done anything. Once shown it stays shown (even after
  // blurring with content still in the field) so it doesn't flicker in and
  // out as focus moves to the next field.
  const [everFocused, setEverFocused] = useState(false);
  return (
    <div className={`mb-5 group ${className}`}>
      {/* labelExtra (Login.jsx's "Forgot password?") shares this row with the
          real <label> rather than being a second, separately-labeled element
          next to it -- one accessible label for this input, one visual row. */}
      <div className="flex items-center justify-between mb-1.5">
        <label htmlFor={id} className={`${labelCls} mb-0`}>
          {label}
        </label>
        {labelExtra}
      </div>
      <div className="relative">
        <span
          className={`pointer-events-none absolute left-3.5 top-1/2 -translate-y-1/2 transition-colors duration-150 ${
            error ? "text-danger" : "text-muted group-focus-within:text-primary"
          }`}
        >
          <Icon name="lock" width={17} height={17} />
        </span>
        <input
          id={id}
          name={id}
          type={show ? "text" : "password"}
          className={`${baseInput} pl-10 pr-11 ${stateClasses(error)}`}
          aria-invalid={!!error}
          aria-describedby={error ? `${id}-error` : undefined}
          onFocus={(e) => {
            setEverFocused(true);
            props.onFocus?.(e);
          }}
          {...props}
        />
        <button
          type="button"
          tabIndex={-1}
          onClick={() => setShow((s) => !s)}
          aria-label={show ? "Hide password" : "Show password"}
          className="absolute right-3 top-1/2 -translate-y-1/2 text-muted hover:text-ink transition-colors"
        >
          <Icon name={show ? "eye-off" : "eye"} width={17} height={17} />
        </button>
      </div>
      {showStrength && everFocused && <PasswordStrengthMeter password={props.value || ""} />}
      {hint && !error && <p className="mt-1.5 text-xs text-muted">{hint}</p>}
      {error && (
        <p id={`${id}-error`} className="mt-1.5 flex items-start gap-1.5 text-xs text-danger animate-fade-in">
          <Icon name="alert" width={13} height={13} className="mt-0.5 shrink-0" />
          <span>{error}</span>
        </p>
      )}
    </div>
  );
}
