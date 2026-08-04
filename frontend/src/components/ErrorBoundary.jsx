import { Component } from "react";
import Icon from "./Icon.jsx";
import { btnPrimary, btnGhost } from "../lib/ui.js";

/**
 * Catches render-time errors anywhere below it.
 *
 * Without one, any thrown error during render unmounts the entire React tree
 * and leaves a blank white page -- no message, no navigation, nothing telling
 * the person what happened or what to do. That is bad on a marketing page and
 * genuinely serious mid-exam, where a candidate staring at a white screen has
 * no way to know whether their answers were saved.
 *
 * Still a class component: `componentDidCatch` / `getDerivedStateFromError`
 * have no hooks equivalent, and this is the one place React still requires a
 * class. Not an oversight.
 *
 * Deliberately NOT a full-screen replacement of the app for recoverable cases:
 * the primary action is "Reload", because a chunk that failed to download (the
 * most likely cause now that routes are code-split) succeeds on a retry, and a
 * reload preserves the URL so the candidate returns to the same exam.
 */
export default class ErrorBoundary extends Component {
  constructor(props) {
    super(props);
    this.state = { error: null };
  }

  static getDerivedStateFromError(error) {
    return { error };
  }

  componentDidCatch(error, info) {
    // Console rather than a reporting service: this app has no error-tracking
    // integration yet, and inventing a silent network call to nowhere would be
    // worse than a log the developer can actually find. When Sentry (or
    // similar) is wired up, this is the single place it hooks in.
    console.error("Unhandled render error:", error, info?.componentStack);
  }

  render() {
    if (!this.state.error) return this.props.children;

    // A lazily-loaded route chunk that fails to download throws a distinctive
    // error. Worth telling apart from a genuine bug, because the remedy the
    // person needs is completely different -- and on the flaky connections this
    // app is built to tolerate, this is the likelier of the two.
    const isChunkError = /Loading chunk|dynamically imported module|Failed to fetch/i.test(
      this.state.error?.message || ""
    );

    return (
      <div className="min-h-screen bg-page flex items-center justify-center p-6">
        <div className="max-w-md w-full rounded-2xl border border-border bg-surface shadow-card p-7">
          <span className="inline-flex items-center justify-center w-11 h-11 rounded-xl bg-danger/10 text-danger mb-4">
            <Icon name="alert" width={20} height={20} />
          </span>

          <h1 className="text-lg font-bold text-ink mb-2">
            {isChunkError ? "Couldn't finish loading" : "Something went wrong"}
          </h1>

          <p className="text-sm text-muted leading-relaxed mb-5">
            {isChunkError
              ? "Part of the page didn't download. This is usually a connection blip — reloading normally fixes it."
              : "An unexpected error stopped this page from rendering. Your saved work is not affected."}
          </p>

          <div className="flex flex-col sm:flex-row gap-2.5">
            {/* Reload rather than router navigation: the tree below has already
                failed, and a soft navigation would re-mount the same broken
                component. A reload also re-requests a chunk that failed. */}
            <button type="button" onClick={() => window.location.reload()}
                    className={`${btnPrimary} justify-center flex-1`}>
              Reload the page
            </button>
            <a href="/dashboard" className={`${btnGhost} justify-center flex-1`}>
              Go to dashboard
            </a>
          </div>

          <p className="text-xs text-muted leading-relaxed mt-5">
            If this keeps happening, contact your administrator and mention what you were doing at the time.
          </p>
        </div>
      </div>
    );
  }
}
