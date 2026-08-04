import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import DashboardHeader from "../components/DashboardHeader.jsx";
import Icon from "../components/Icon.jsx";
import EmptyState from "../components/EmptyState.jsx";
import StatCard from "../components/StatCard.jsx";
import { TextField, PasswordField } from "../components/FormField.jsx";
import CredentialsHandoff, { generatePassword } from "../components/CredentialsHandoff.jsx";
import { Api, ApiError } from "../lib/api.js";
import { btnPrimary } from "../lib/ui.js";

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
  { id: "violations", label: "Violations", icon: "flag", to: "/admin/violations" },
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
      try {
        const [a, s, requests] = await Promise.all([
          Api.get("/analytics/platform"),
          Api.get("/admin/summary"),
          Api.get("/access-requests"),
        ]);
        if (cancelled) return;
        setAnalytics(a);
        setSummary(s);
        setAccessRequests(requests);
      } catch (err) {
        if (!cancelled) setLoadError(err instanceof ApiError ? err.message : "Couldn't load the admin dashboard.");
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
              <StatCard label="Total Candidates" value={summary ? summary.total_candidates : null} tone="primary" to="/admin/candidates" prominent />
            </div>
            <div className="grid grid-cols-2 lg:grid-cols-5 gap-4">
              <StatCard label="Active Exams" value={summary ? summary.active_exams : null} tone="success" to="/admin/exams?status=active" />
              <StatCard label="Upcoming Exams" value={summary ? summary.upcoming_exams : null} tone="primary" to="/admin/exams?status=upcoming" />
              <StatCard label="Completed Exams" value={summary ? summary.completed_exams : null} tone="muted" to="/admin/exams?status=completed" />
              <StatCard label="Live Sessions" value={summary ? summary.live_sessions : null} tone="warning" to="/admin/live-sessions" />
              <StatCard label="Violations Logged" value={summary ? summary.violations_logged : null} tone="danger" to="/admin/violations" />
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
            <div className="rounded-2xl border border-border bg-surface shadow-card p-5 max-w-lg text-sm text-muted leading-relaxed">
              Platform-wide settings (branding, retention windows, notification preferences) aren't yet configurable
              from this UI — they're managed via the backend's <code className="text-ink">.env</code> configuration.
              This section is reserved for that control panel as it's built out.
            </div>
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
 * Approving is the primary path for creating an examiner account, so the
 * approve action collects the initial password inline rather than bouncing
 * the admin to the separate "+ New Examiner" form on the Examiners page and
 * making them re-key details the requester already supplied.
 */
function AccessRequestsPanel({ requests, onChange }) {
  const [expandedId, setExpandedId] = useState(null);
  const [approvedCredentials, setApprovedCredentials] = useState(null);
  const [password, setPassword] = useState("");
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
    setPassword("");
    setNote("");
  }

  async function refresh() {
    onChange(await Api.get("/access-requests"));
  }

  async function handleApprove(request) {
    if (password.length < 8) {
      setError("Set a temporary password of at least 8 characters.");
      return;
    }
    setError("");
    setSuccess("");
    setBusyId(request.id);
    try {
      await Api.post(`/access-requests/${request.id}/approve`, { password, review_note: note.trim() || null });
      await refresh();
      // Capture before resetForm() clears `password` -- this is the only
      // render where it can be shown, since the server stores only a hash.
      setApprovedCredentials({ email: request.email, password });
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
      {approvedCredentials && (
        <div className="mb-4">
          <CredentialsHandoff
            email={approvedCredentials.email}
            password={approvedCredentials.password}
            onDismiss={() => setApprovedCredentials(null)}
          />
        </div>
      )}
      {success && !approvedCredentials && (
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
                      <PasswordField
                        id={`req-password-${r.id}`}
                        label="Temporary password"
                        required
                        minLength={8}
                        value={password}
                        onChange={(e) => setPassword(e.target.value)}
                        hint="At least 8 characters. Send this to the examiner — only an admin can reset it later."
                      />
                      <button
                        type="button"
                        onClick={() => setPassword(generatePassword())}
                        className="-mt-1 mb-3 text-xs font-semibold text-primary hover:underline"
                      >
                        Generate a strong password
                      </button>
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
