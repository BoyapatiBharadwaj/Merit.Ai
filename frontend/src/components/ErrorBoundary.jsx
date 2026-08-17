import { Component } from "react";
import Icon from "./Icon.jsx";
import { btnPrimary, btnGhost } from "../lib/ui.js";

/**
 * Catches render-time errors anywhere below it.
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
    // Console rather than a reporting service: this app has no
    // error-tracking integration yet, and inventing a silent network call
    // to nowhere would be worse than a log the developer can actually find.
    console.error("Unhandled render error:", error, info?.componentStack);
  }

  render() {
    if (!this.state.error) return this.props.children;

    // A lazily-loaded route chunk that fails to download throws a distinctive error.
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
