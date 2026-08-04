import { useState } from "react";
import Icon from "./Icon.jsx";
import { btnGhost } from "../lib/ui.js";

/**
 * One-time display of a newly created account's sign-in details.
 *
 * Passwords are stored bcrypt-hashed, so this is the ONLY moment they can be
 * shown -- once this panel is dismissed nobody, including an administrator
 * and including the database, can read the password back. That is a feature,
 * not a limitation to work around: making passwords retrievable would mean a
 * single database leak exposes every account. If the examiner loses it, an
 * admin resets it to a new one rather than looking the old one up.
 *
 * Hence the deliberate friction: the panel does not auto-dismiss, and the
 * copy button is the primary action, because an admin who closes this without
 * copying has to go and reset the password.
 */
export default function CredentialsHandoff({ email, password, onDismiss }) {
  const [copied, setCopied] = useState(false);
  const [revealed, setRevealed] = useState(false);

  const block = `Email: ${email}\nPassword: ${password}`;

  async function copyAll() {
    try {
      await navigator.clipboard.writeText(block);
      setCopied(true);
      setTimeout(() => setCopied(false), 2500);
    } catch {
      // Clipboard needs permission and a focused document; the password is
      // on screen either way, so this is a convenience, not the mechanism.
      setRevealed(true);
    }
  }

  return (
    <div className="rounded-xl border border-emerald-500/40 bg-emerald-500/5 p-4">
      <div className="flex items-start gap-2.5">
        <span className="mt-0.5 text-emerald-600 dark:text-emerald-400 shrink-0">
          <Icon name="check" width={16} height={16} />
        </span>
        <div className="min-w-0 flex-1">
          <p className="text-sm font-semibold text-emerald-700 dark:text-emerald-300">
            Account created — send these details now
          </p>
          <p className="mt-1 text-xs text-muted">
            This password cannot be shown again. If it's lost, an admin has to reset it.
          </p>

          <dl className="mt-3 rounded-lg border border-border bg-page px-3 py-2.5 text-sm">
            <div className="flex items-baseline gap-2">
              <dt className="w-20 shrink-0 text-xs text-muted">Email</dt>
              <dd className="font-mono text-xs text-ink break-all">{email}</dd>
            </div>
            <div className="mt-1.5 flex items-baseline gap-2">
              <dt className="w-20 shrink-0 text-xs text-muted">Password</dt>
              <dd className="font-mono text-xs text-ink break-all">
                {revealed ? password : "•".repeat(Math.min(password.length, 24))}
                <button
                  type="button"
                  onClick={() => setRevealed((v) => !v)}
                  className="ml-2 align-middle text-muted hover:text-ink transition-colors"
                  aria-label={revealed ? "Hide password" : "Show password"}
                >
                  <Icon name={revealed ? "eye-off" : "eye"} width={13} height={13} />
                </button>
              </dd>
            </div>
          </dl>

          <div className="mt-3 flex flex-wrap items-center gap-2">
            <button
              type="button"
              onClick={copyAll}
              className={`${btnGhost.replace("px-5 py-3", "px-3 py-1.5")} text-xs`}
            >
              <Icon name={copied ? "check" : "doc"} width={13} height={13} />
              {copied ? "Copied" : "Copy email & password"}
            </button>
            <button
              type="button"
              onClick={onDismiss}
              className="text-xs font-semibold text-muted hover:text-ink transition-colors"
            >
              Done
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

/** Generate a readable but strong temporary password.
 *
 * Avoids the characters people misread when a password is dictated over the
 * phone or copied off a screen (O/0, l/1/I), because this one is going to be
 * transcribed by hand more often than not. ~62 bits of entropy at length 14,
 * which is ample for a credential the holder is expected to change. */
export function generatePassword(length = 14) {
  const alphabet = "ABCDEFGHJKMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz23456789";
  const symbols = "!@#$%^&*?";
  const values = new Uint32Array(length);
  crypto.getRandomValues(values);

  const chars = Array.from(values, (v, i) =>
    // Guarantee at least one symbol and one digit so the result always
    // satisfies a typical policy, rather than failing validation by chance.
    i === 3 ? symbols[v % symbols.length]
      : i === 7 ? "23456789"[v % 8]
      : alphabet[v % alphabet.length],
  );
  return chars.join("");
}
