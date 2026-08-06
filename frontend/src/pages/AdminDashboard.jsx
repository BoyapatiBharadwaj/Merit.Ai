import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import DashboardHeader from "../components/DashboardHeader.jsx";
import Icon from "../components/Icon.jsx";
import EmptyState from "../components/EmptyState.jsx";
import StatCard from "../components/StatCard.jsx";
import { TextField } from "../components/FormField.jsx";
import { Api, ApiError } from "../lib/api.js";
import { btnPrimary } from "../lib/ui.js";
import "../lib/vendorChart.js";

/**
 * Sidebar items are a mix of same-page anchors (Overview, Access Requests,
 * Settings -- sections that still live on this page) and real routes
 * (Examiners, Candidates, Exams, Live Sessions, Violations -- now their own
 * dedicated pages under /admin/..., see App.jsx). Only the anchor items
 * participate in the scroll-spy IntersectionObserver below; route items are
 * plain links, the same as DashboardHeader's Dashboard/Profile links.
 */
const NAV_ITEMS = [
  { id: "overview", label: "Overview", icon: "layout", anchor: true },
  { id: "examiners", label: "Examiners", icon: "briefcase", to: "/admin/examiners" },
  { id: "candidates", label: "Candidates", icon: "users", to: "/admin/candidates" },
  { id: "exams", label: "Exams", icon: "doc", to: "/admin/exams" },
  { id: "live", label: "Live Sessions", icon: "eye", to: "/admin/live-sessions" },
  // No standalone violations list -- an admin reviews violations through
  // exam -> student (Exams -> a candidate's row -> full report), the same
  // path an examiner uses. Review Queue is a distinct, kept worklist.
  { id: "review", label: "Review Queue", icon: "shield-check", to: "/admin/review-queue" },
  { id: "organizations", label: "Organizations", icon: "briefcase", to: "/admin/organizations" },
  { id: "requests", label: "Access Requests", icon: "mail", anchor: true },
  { id: "settings", label: "Settings", icon: "settings", anchor: true },
];

const CHART_PALETTE = ["#2563eb", "#16a34a", "#d97706", "#dc2626", "#7c3aed", "#0891b2", "#db2777", "#65a30d"];

function AdminSidebar({ collapsed, forceExpanded, onToggle, activeId }) {
  return (
    <aside className={`dash-sidebar ${collapsed ? "collapsed" : ""} ${forceExpanded ? "force-expanded" : ""} flex-none border-r border-border bg-surface flex flex-col`}>
      <div className="flex items-center gap-2.5 px-4 py-4 border-b border-border overflow-hidden">
        <div className="h-8 w-8 flex-none rounded-lg bg-primary/10 text-primary flex items-center justify-center">
          <Icon name="shield-check" width={16} height={16} />
        </div>
        <span className="sidebar-brand font-bold text-sm truncate">Admin Portal</span>
      </div>
      <nav className="flex-1 flex flex-col gap-1 p-3">
        {NAV_ITEMS.map((item) =>
          item.to ? (
            <Link
              key={item.id}
              to={item.to}
              className="sidebar-link flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium text-ink hover:bg-page transition-colors"
            >
              <Icon name={item.icon} width={18} height={18} className="flex-none" />
              <span className="sidebar-label truncate">{item.label}</span>
            </Link>
          ) : (
            <a
              key={item.id}
              href={`#${item.id}`}
              className={`sidebar-link flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium text-ink hover:bg-page transition-colors ${
                activeId === item.id ? "active" : ""
              }`}
            >
              <Icon name={item.icon} width={18} height={18} className="flex-none" />
              <span className="sidebar-label truncate">{item.label}</span>
            </a>
          ),
        )}
      </nav>
      <button
        type="button"
        onClick={onToggle}
        className="flex items-center justify-center gap-2 border-t border-border py-3 text-xs font-semibold text-muted hover:text-ink transition-colors"
      >
        <Icon name="chevron-left" width={16} height={16} className={`transition-transform ${collapsed ? "rotate-180" : ""}`} />
        <span className="sidebar-label">Collapse</span>
      </button>
    </aside>
  );
}

export default function AdminDashboard() {
  const [collapsed, setCollapsed] = useState(() => localStorage.getItem("aep_admin_sidebar") === "collapsed");
  const [forceExpanded, setForceExpanded] = useState(false);
  const [activeId, setActiveId] = useState("overview");

  const [analytics, setAnalytics] = useState(null);
  const [summary, setSummary] = useState(null); // the 7 overview-card counts, see admin_service.dashboard_summary
  const [accessRequests, setAccessRequests] = useState(null);
  const [loadError, setLoadError] = useState("");

  const statusChartRef = useRef(null);
  const typeChartRef = useRef(null);
  const chartInstances = useRef({});

  useEffect(() => {
    let cancelled = false;
    (async () => {
      // allSettled, not all.
      //
      // Promise.all rejects on the FIRST failure and discards the results that
      // did arrive, so one unavailable widget blanked the entire dashboard --
      // an admin whose access-requests endpoint was briefly failing lost their
      // platform stats and their summary too, and the page said only that
      // something went wrong. Each panel now succeeds or fails on its own.
      const [a, s, requests] = await Promise.allSettled([
        Api.get("/analytics/platform"),
        Api.get("/admin/summary"),
        Api.get("/access-requests"),
      ]);
      if (cancelled) return;
      if (a.status === "fulfilled") setAnalytics(a.value);
      if (s.status === "fulfilled") setSummary(s.value);
      if (requests.status === "fulfilled") setAccessRequests(requests.value);

      const failures = [a, s, requests].filter((r) => r.status === "rejected");
      if (failures.length) {
        const reason = failures[0].reason;
        setLoadError(
          `${failures.length} of 3 panels could not be loaded (${
            reason instanceof ApiError ? reason.message : "unexpected error"
          }). Everything else below is current.`,
        );
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // Active-link highlighting as the admin scrolls through the anchor
  // sections still on this page (Overview, Access Requests, Settings).
  useEffect(() => {
    if (!window.IntersectionObserver) return;
    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) setActiveId(entry.target.id);
        });
      },
      { rootMargin: "-40% 0px -50% 0px" },
    );
    NAV_ITEMS.filter((item) => item.anchor).forEach((item) => {
      const el = document.getElementById(item.id);
      if (el) observer.observe(el);
    });
    return () => observer.disconnect();
  }, []);

  // Build/rebuild Chart.js charts once analytics data is in.
  useEffect(() => {
    if (!analytics || !window.Chart) return;
    const styles = getComputedStyle(document.documentElement);
    const textColor = styles.getPropertyValue("--text-muted").trim() || "#64748b";
    const gridColor = styles.getPropertyValue("--border").trim() || "#e2e8f0";

    Object.values(chartInstances.current).forEach((c) => c?.destroy());
    chartInstances.current = {};

    // Each canvas below is only mounted once its dataset is non-empty (see
    // the render below) -- an EmptyState card takes its place otherwise --
    // so every chart here is skipped rather than built against a fake
    // "No data yet" placeholder slice/bar.
    const statusLabels = Object.keys(analytics.exams_by_status);
    const statusCounts = Object.values(analytics.exams_by_status);
    if (statusChartRef.current) {
      chartInstances.current.status = new window.Chart(statusChartRef.current, {
        type: "doughnut",
        data: { labels: statusLabels, datasets: [{ data: statusCounts, backgroundColor: CHART_PALETTE }] },
        options: { plugins: { legend: { labels: { color: textColor } } } },
      });
    }

    const typeLabels = Object.keys(analytics.violations_by_type);
    const typeCounts = Object.values(analytics.violations_by_type);
    if (typeChartRef.current) {
      chartInstances.current.type = new window.Chart(typeChartRef.current, {
        type: "bar",
        data: { labels: typeLabels, datasets: [{ label: "Count", data: typeCounts, backgroundColor: "#2563eb" }] },
        options: {
          plugins: { legend: { display: false } },
          scales: {
            x: { ticks: { color: textColor, autoSkip: false, maxRotation: 60, minRotation: 30 }, grid: { color: gridColor } },
            y: { beginAtZero: true, ticks: { color: textColor, precision: 0 }, grid: { color: gridColor } },
          },
        },
      });
    }

    return () => {
      Object.values(chartInstances.current).forEach((c) => c?.destroy());
    };
  }, [analytics]);

  function toggleSidebar() {
    setCollapsed((prev) => {
      const next = !prev;
      localStorage.setItem("aep_admin_sidebar", next ? "collapsed" : "expanded");
      setForceExpanded(!next);
      return next;
    });
  }

  return (
    <div className="min-h-screen flex bg-page text-ink">
      <AdminSidebar collapsed={collapsed} forceExpanded={forceExpanded} onToggle={toggleSidebar} activeId={activeId} />

      <div className="flex-1 min-w-0 flex flex-col">
        <DashboardHeader title="Admin Portal" />

        <main className="max-w-6xl mx-auto w-full p-4 sm:p-6 flex flex-col gap-10">
          {loadError && (
            <div className="flex items-start gap-2.5 rounded-xl border border-danger/30 bg-danger/5 px-4 py-3 text-sm text-danger">
              <Icon name="alert" width={16} height={16} className="mt-0.5 shrink-0" />
              <span>{loadError}</span>
            </div>
          )}

          <section id="overview" className="scroll-mt-20">
            <h1 className="text-2xl font-extrabold tracking-tight mb-1">Platform Overview</h1>
            <p className="text-sm text-muted mb-5">A snapshot of examiners, candidates, exams, and AI proctoring activity across the platform.</p>

            {/* Total Examiners + Total Candidates are the two prominent cards --
                they represent the platform's two main user groups, so they get
                a bigger, wider treatment than the five secondary cards below. */}
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 mb-4">
              <StatCard label="Total Examiners" value={summary ? summary.total_examiners : null} tone="primary" to="/admin/examiners" prominent />
              <StatCard label="Total Candidates" value={summary ? summary.total_candidates : null} tone="primary" to="/admin/candidates" prominent
                             hint="Registered accounts, not roster invitations" />
            </div>
            <div className="grid grid-cols-2 lg:grid-cols-5 gap-4">
              <StatCard label="Active Exams" value={summary ? summary.active_exams : null} tone="success" to="/admin/exams?status=active" />
              <StatCard label="Upcoming Exams" value={summary ? summary.upcoming_exams : null} tone="primary" to="/admin/exams?status=upcoming" />
              <StatCard label="Completed Exams" value={summary ? summary.completed_exams : null} tone="muted" to="/admin/exams?status=completed" />
              <StatCard label="Live Sessions" value={summary ? summary.live_sessions : null} tone="warning" to="/admin/live-sessions" />
              {/* No /admin/violations to link to anymore -- reviewing a flag
                  means opening that candidate's exam report, so this card
                  points at the review queue (undecided flags) instead of a
                  flat browsing list. */}
              <StatCard label="Violations Logged" value={summary ? summary.violations_logged : null} tone="danger" to="/admin/review-queue"
                             hint="All flags, before review" />
            </div>

            <div className="grid md:grid-cols-2 gap-4 mt-6">
              <div className="rounded-2xl border border-border bg-surface shadow-card p-4">
                <div className="text-sm font-semibold mb-3">Exams by Status</div>
                {analytics && Object.keys(analytics.exams_by_status).length === 0 ? (
                  <EmptyState icon="chart" title="No exams yet" description="Once exams are created on the platform, their status breakdown will show up here." />
                ) : (
                  <canvas ref={statusChartRef} height="220" />
                )}
              </div>
              <div className="rounded-2xl border border-border bg-surface shadow-card p-4">
                <div className="text-sm font-semibold mb-3">Violations by Type</div>
                {analytics && Object.keys(analytics.violations_by_type).length === 0 ? (
                  <EmptyState icon="chart" title="No violations yet" description="Proctoring violations across all exams will be broken down by type here as they're detected." />
                ) : (
                  <canvas ref={typeChartRef} height="220" />
                )}
              </div>
            </div>
          </section>

          {/* Approving a request *is* how most examiner accounts get created --
              the "+ New Examiner" manual form now lives on the Examiners page
              (/admin/examiners) alongside the rest of examiner management. */}
          <AccessRequestsPanel requests={accessRequests} onChange={setAccessRequests} />

          <section id="settings" className="scroll-mt-20 pb-10">
            <h2 className="text-lg font-bold mb-3">Settings</h2>
            {/* Was a paragraph saying settings live in .env and nothing else --
                so an administrator asking "is email actually working here?" or
                "how many strikes end an exam?" had to read a file on a server
                they may not have access to. Read-only, and labelled as such:
                a settings page that appears to save and does not would be worse
                than one that explains where the values come from. */}
            <SettingsPanel />
          </section>
        </main>
      </div>
    </div>
  );
}

const REQUEST_STATUS_STYLES = {
  pending: "bg-warning/10 text-warning",
  approved: "bg-success/10 text-success",
  rejected: "bg-danger/10 text-danger",
};

/**
 * Examiner access requests submitted from the public site.
 *
 * Approving is the primary path for creating an examiner account.
 *
 * It used to collect an initial password here, which was then emailed to the
 * new examiner in plain text and shown once to the admin to relay by hand. That
 * is gone: approving now mints the account with an unusable random secret and
 * emails a single-use activation link, so the only person who ever knows the
 * password is the person it belongs to. There is nothing for the admin to type
 * and nothing for them to pass on.
 */
function AccessRequestsPanel({ requests, onChange }) {
  const [expandedId, setExpandedId] = useState(null);
  const [note, setNote] = useState("");
  const [busyId, setBusyId] = useState(null);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [showHandled, setShowHandled] = useState(false);

  const pending = (requests || []).filter((r) => r.status === "pending");
  const handled = (requests || []).filter((r) => r.status !== "pending");
  const visible = showHandled ? handled : pending;

  function resetForm() {
    setExpandedId(null);
    setNote("");
  }

  async function refresh() {
    onChange(await Api.get("/access-requests"));
  }

  async function handleApprove(request) {
    setError("");
    setSuccess("");
    setBusyId(request.id);
    try {
      await Api.post(`/access-requests/${request.id}/approve`, { review_note: note.trim() || null });
      await refresh();
      setSuccess(
        `Approved. An activation link has been emailed to ${request.email} — they choose their own ` +
        "password from it. Nothing needs to be sent by hand.",
      );
      resetForm();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't approve this request.");
    } finally {
      setBusyId(null);
    }
  }

  async function handleReject(request) {
    setError("");
    setSuccess("");
    setBusyId(request.id);
    try {
      await Api.post(`/access-requests/${request.id}/reject`, { review_note: note.trim() || null });
      await refresh();
      setSuccess(`Request from ${request.email} was rejected.`);
      resetForm();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't reject this request.");
    } finally {
      setBusyId(null);
    }
  }

  return (
    <section id="requests" className="scroll-mt-20">
      <div className="flex flex-wrap items-center justify-between gap-3 mb-3">
        <div className="flex items-center gap-2.5">
          <h2 className="text-lg font-bold">Access Requests</h2>
          {pending.length > 0 && (
            <span className="inline-flex items-center rounded-full bg-warning/10 text-warning px-2.5 py-1 text-xs font-semibold">
              {pending.length} pending
            </span>
          )}
        </div>
        <button
          type="button"
          onClick={() => {
            setShowHandled((v) => !v);
            resetForm();
          }}
          className="text-xs font-semibold text-muted hover:text-ink transition-colors"
        >
          {showHandled ? `← Back to pending (${pending.length})` : `View handled (${handled.length})`}
        </button>
      </div>

      {error && (
        <div className="mb-4 flex items-start gap-2.5 rounded-xl border border-danger/30 bg-danger/5 px-4 py-3 text-sm text-danger">
          <Icon name="alert" width={16} height={16} className="mt-0.5 shrink-0" />
          <span>{error}</span>
        </div>
      )}
      {success && (
        <div className="mb-4 flex items-start gap-2.5 rounded-xl border border-success/30 bg-success/5 px-4 py-3 text-sm text-success">
          <Icon name="check" width={16} height={16} className="mt-0.5 shrink-0" />
          <span>{success}</span>
        </div>
      )}

      {!requests ? (
        <div className="skeleton skeleton-card h-40" />
      ) : visible.length === 0 ? (
        <EmptyState
          icon="mail"
          title={showHandled ? "No handled requests yet" : "No pending requests"}
          description={
            showHandled
              ? "Approved and rejected requests will be listed here."
              : "Requests submitted from the public site will appear here for review."
          }
        />
      ) : (
        <div className="flex flex-col gap-3">
          {visible.map((r) => {
            const isOpen = expandedId === r.id;
            const isBusy = busyId === r.id;
            return (
              <article key={r.id} className="rounded-2xl border border-border bg-surface shadow-card p-5">
                <div className="flex flex-wrap items-start justify-between gap-3 mb-2">
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <strong className="text-sm">{r.full_name}</strong>
                      <span
                        className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-semibold capitalize ${
                          REQUEST_STATUS_STYLES[r.status] || "bg-page text-muted border border-border"
                        }`}
                      >
                        {r.status}
                      </span>
                    </div>
                    <p className="text-xs text-muted mt-1 break-words">
                      {r.email} · {r.organization_name}
                    </p>
                  </div>
                  {r.created_at && (
                    <span className="text-xs text-muted shrink-0">{new Date(r.created_at).toLocaleDateString()}</span>
                  )}
                </div>

                <p className="text-sm text-ink leading-relaxed rounded-xl bg-page border border-border px-4 py-3 mb-3 whitespace-pre-wrap break-words">
                  {r.purpose}
                </p>

                {r.review_note && <p className="text-xs text-muted mb-3">Note: {r.review_note}</p>}

                {r.status === "pending" &&
                  (isOpen ? (
                    <div className="rounded-xl border border-border bg-page p-4">
                      <p className="mb-3 flex items-start gap-2 text-sm text-muted leading-relaxed">
                        <Icon name="mail" width={15} height={15} className="mt-0.5 shrink-0" />
                        <span>
                          Approving emails <strong className="text-ink">{r.email}</strong> a
                          single-use activation link. They choose their own password from it — you
                          will not see it, and there is nothing to send on.
                        </span>
                      </p>
                      <TextField
                        id={`req-note-${r.id}`}
                        label={
                          <>
                            Internal note <span className="font-normal text-muted">(optional)</span>
                          </>
                        }
                        value={note}
                        onChange={(e) => setNote(e.target.value)}
                      />
                      <div className="flex flex-wrap gap-2">
                        <button
                          type="button"
                          disabled={isBusy}
                          onClick={() => handleApprove(r)}
                          className={`${btnPrimary.replace("px-5 py-3", "px-4 py-2")} text-xs ${isBusy ? "opacity-70 pointer-events-none" : ""}`}
                        >
                          {isBusy && <Icon name="spinner" width={14} height={14} className="animate-spin" />}
                          Approve &amp; Create Account
                        </button>
                        <button
                          type="button"
                          disabled={isBusy}
                          onClick={() => handleReject(r)}
                          className="inline-flex items-center gap-1.5 rounded-lg border border-danger/40 px-4 py-2 text-xs font-semibold text-danger hover:bg-danger/10 transition-colors disabled:opacity-60"
                        >
                          Reject
                        </button>
                        <button
                          type="button"
                          disabled={isBusy}
                          onClick={resetForm}
                          className="px-3 py-2 text-xs font-semibold text-muted hover:text-ink transition-colors"
                        >
                          Cancel
                        </button>
                      </div>
                    </div>
                  ) : (
                    <button
                      type="button"
                      onClick={() => {
                        setExpandedId(r.id);
                        setPassword("");
                        setNote("");
                        setError("");
                        setSuccess("");
                      }}
                      className={`${btnPrimary.replace("px-5 py-3", "px-4 py-2")} text-xs`}
                    >
                      Review Request
                    </button>
                  ))}
              </article>
            );
          })}
        </div>
      )}
    </section>
  );
}

/**
 * What this deployment is actually configured to do.
 *
 * Read-only by design, and says so. Making these writable means persisting
 * overrides and reloading them across every worker process; a page that looks
 * like it saved and silently did not is worse than one that tells an
 * administrator where the value comes from.
 *
 * No secrets reach here — the endpoint returns whether email is configured, not
 * the credentials behind it. There is a test asserting that.
 */
function SettingsPanel() {
  const [config, setConfig] = useState(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    Api.get("/admin/settings")
      .then((data) => { if (!cancelled) setConfig(data); })
      .catch((err) => {
        if (!cancelled) setError(err instanceof ApiError ? err.message : "Couldn't load settings.");
      });
    return () => { cancelled = true; };
  }, []);

  if (error) {
    return (
      <div role="alert" className="rounded-2xl border border-danger/30 bg-danger/5 p-5 text-sm text-danger">
        {error}
      </div>
    );
  }
  if (!config) {
    return <div className="h-64 rounded-2xl border border-border bg-surface animate-pulse" aria-hidden="true" />;
  }

  const groups = [
    ["Accounts", [
      ["Email verification required", config.authentication.require_email_verification ? "Yes" : "No"],
      ["Consent required at signup", config.authentication.require_consent_on_signup ? "Yes" : "No"],
      ["Minimum password length", `${config.authentication.password_min_length} characters`],
      ["Session length", `${config.authentication.session_minutes} minutes`],
      ["Exam token grace", `${config.authentication.attempt_token_grace_minutes} minutes past the deadline`],
    ]],
    ["Proctoring", [
      ["Strikes before auto-submit", config.proctoring.strike_limit],
      ["Face match tolerance", config.proctoring.face_match_tolerance],
      ["Risk weights", Object.entries(config.proctoring.risk_weights)
        .map(([severity, weight]) => `${severity} ${weight}`).join(" · ")],
    ]],
    ["Email", [
      ["Delivery configured", config.email.configured ? "Yes" : "No — codes cannot be sent"],
      ["Code length", `${config.email.otp_length} digits`],
      ["Code lifetime", `${config.email.otp_ttl_minutes} minutes`],
    ]],
    ["Scaling", [
      ["Shared state (Redis)", config.scaling.shared_state ? "Connected" : "Not configured — single worker only"],
      ["Trusted proxy networks", config.scaling.trusted_proxies],
    ]],
  ];

  return (
    <div className="space-y-4 max-w-2xl">
      <div className="flex items-start gap-2.5 rounded-xl border border-border bg-page px-4 py-3">
        <span className="text-muted mt-0.5 shrink-0"><Icon name="alert" width={15} height={15} /></span>
        <p className="text-sm text-muted leading-relaxed">
          Read-only. {config.note} Running as{" "}
          <strong className="text-ink">{config.environment}</strong>.
        </p>
      </div>

      {groups.map(([title, rows]) => (
        <div key={title} className="rounded-2xl border border-border bg-surface shadow-card overflow-hidden">
          <div className="px-5 py-3 border-b border-border">
            <h3 className="text-sm font-bold text-ink">{title}</h3>
          </div>
          <dl className="divide-y divide-border">
            {rows.map(([label, value]) => (
              <div key={label} className="flex flex-wrap items-baseline justify-between gap-3 px-5 py-2.5">
                <dt className="text-sm text-muted">{label}</dt>
                <dd className="text-sm font-medium text-ink text-right">{String(value)}</dd>
              </div>
            ))}
          </dl>
        </div>
      ))}
    </div>
  );
}
