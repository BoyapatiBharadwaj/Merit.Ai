import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import RedirectToLogin from "../components/RedirectToLogin.jsx";
import DashboardHeader from "../components/DashboardHeader.jsx";
import Icon from "../components/Icon.jsx";
import EmptyState from "../components/EmptyState.jsx";
import Breadcrumbs from "../components/Breadcrumbs.jsx";
import Pagination, { paginate } from "../components/Pagination.jsx";
import { Api, ApiError } from "../lib/api.js";
import { fieldInput } from "../lib/ui.js";
import { badgeClass } from "../lib/adminUi.js";
import { isLoggedIn, getRole } from "../lib/auth.js";

const PAGE_SIZE = 10;

/**
 * Download the current view as CSV.
 */
function ExportButton({ kind, params = {} }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function download() {
    setBusy(true);
    setError("");
    try {
      const query = new URLSearchParams(
        Object.entries(params).filter(([, value]) => value !== "" && value != null),
      ).toString();
      await Api.downloadFile(`/admin/export/${kind}${query ? `?${query}` : ""}`,
                             `meritai-${kind}.csv`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't export.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="inline-flex flex-col items-end">
      <button type="button" onClick={download} disabled={busy}
              className="inline-flex items-center gap-1.5 px-3 py-2 text-sm font-semibold rounded-xl border border-border text-ink hover:border-primary hover:text-primary transition-colors disabled:opacity-60">
        <Icon name="doc" width={14} height={14} />
        {busy ? "Preparing…" : "Export CSV"}
      </button>
      {error && <span role="alert" className="mt-1 text-xs text-danger">{error}</span>}
    </div>
  );
}

export default function AdminCandidates() {
  const navigate = useNavigate();
  const [organizations, setOrganizations] = useState([]);
  // Whether the organization list could be loaded at
  // all. Empty and unavailable are different facts.
  const [organizationsFailed, setOrganizationsFailed] = useState(false);
  const [rows, setRows] = useState(null);
  const [loadError, setLoadError] = useState("");

  const [searchInput, setSearchInput] = useState("");
  const [search, setSearch] = useState("");
  const [organizationId, setOrganizationId] = useState("");
  const [page, setPage] = useState(1);
  // The server's figures, not derived from a list the client holds.
  const [total, setTotal] = useState(0);
  const [pageCount, setPageCount] = useState(1);

  useEffect(() => {
    Api.get("/admin/organizations")
      .then((data) => { setOrganizations(data); setOrganizationsFailed(false); })
      .catch(() => setOrganizationsFailed(true));
  }, []);

  useEffect(() => {
    const t = setTimeout(() => setSearch(searchInput.trim()), 300);
    return () => clearTimeout(t);
  }, [searchInput]);

  useEffect(() => {
    // A request ticket, so a slow OLD search cannot overwrite a newer one.
    let cancelled = false;
    setRows(null);
    setLoadError("");
    const params = new URLSearchParams();
    if (search) params.set("search", search);
    if (organizationId) params.set("organization_id", organizationId);
    params.set("page", String(page));
    params.set("page_size", String(PAGE_SIZE));
    Api.get(`/admin/candidates?${params.toString()}`)
      .then((data) => { if (!cancelled) { setRows(data.items); setTotal(data.total); setPageCount(data.total_pages); } })
      .catch((err) => {
        if (!cancelled) setLoadError(err instanceof ApiError ? err.message : "Couldn't load candidates.");
      });
    return () => { cancelled = true; };
  }, [search, organizationId, page]);

  if (!isLoggedIn() || getRole() !== "admin") return <RedirectToLogin />;

  // The server decides the page; `rows` IS the page.
  const pageRows = rows || [];
  const safePage = page;

  return (
    <div className="min-h-screen bg-page text-ink">
      <DashboardHeader title="Candidates" />
      <main className="max-w-6xl mx-auto w-full p-4 sm:p-6">
        <Breadcrumbs trail={[{ label: "Dashboard", to: "/dashboard" }, { label: "Candidates" }]} />

        <div className="flex flex-wrap items-start justify-between gap-3 mb-5">
          <div>
            <h1 className="text-2xl font-extrabold tracking-tight mb-1">Candidates</h1>
            <p className="text-sm text-muted">Every registered student across the platform.</p>
          </div>
          <ExportButton kind="candidates" params={{ search, organization_id: organizationId }} />
        </div>

        {loadError && (
          <div className="mb-4 flex items-start gap-2.5 rounded-xl border border-danger/30 bg-danger/5 px-4 py-3 text-sm text-danger">
            <Icon name="alert" width={16} height={16} className="mt-0.5 shrink-0" />
            <span>{loadError}</span>
          </div>
        )}

        <div className="flex flex-wrap gap-3 mb-4">
          <div className="relative flex-1 min-w-[220px]">
            <Icon name="user" width={16} height={16} className="absolute left-3.5 top-1/2 -translate-y-1/2 text-muted pointer-events-none" />
            <input type="search" placeholder="Search by name or email…" value={searchInput}
                   onChange={(e) => setSearchInput(e.target.value)} className={`${fieldInput} pl-10`} />
          </div>
          <select value={organizationId} onChange={(e) => setOrganizationId(e.target.value)} className={`${fieldInput} sm:w-56`}>
            <option value="">{organizationsFailed ? "Organizations unavailable" : "All organizations"}</option>
            {organizations.map((o) => (
              <option key={o.id} value={o.id}>{o.name}</option>
            ))}
          </select>
        </div>

        {!rows ? (
          <div className="skeleton skeleton-card h-64" />
        ) : rows.length === 0 ? (
          <EmptyState icon="users" title="No candidates found" description="Try a different search or filter." />
        ) : (
          <div className="rounded-2xl border border-border bg-surface shadow-card overflow-x-auto">
            <table className="min-w-full text-sm">
              <thead>
                <tr className="text-left text-xs text-muted uppercase tracking-wide border-b border-border">
                  <th className="px-4 py-3 font-semibold">Candidate</th>
                  <th className="px-4 py-3 font-semibold">Email</th>
                  <th className="px-4 py-3 font-semibold">Organization</th>
                  <th className="px-4 py-3 font-semibold text-right">Exams Taken</th>
                  <th className="px-4 py-3 font-semibold text-right">Completed</th>
                  <th className="px-4 py-3 font-semibold text-right">In Progress</th>
                  <th className="px-4 py-3 font-semibold text-right">Violations</th>
                  <th className="px-4 py-3 font-semibold">Status</th>
                  <th className="px-4 py-3 font-semibold">Action</th>
                </tr>
              </thead>
              <tbody>
                {pageRows.map((c) => (
                  <tr key={c.id} onClick={() => navigate(`/admin/candidates/${c.id}`)}
                      className="border-b border-border last:border-0 hover:bg-page/60 transition-colors cursor-pointer">
                    <td className="px-4 py-3 font-medium">{c.full_name}</td>
                    <td className="px-4 py-3 text-muted">{c.email}</td>
                    <td className="px-4 py-3">{c.organization_name || "-"}</td>
                    <td className="px-4 py-3 text-right tabular-nums">{c.exams_taken}</td>
                    <td className="px-4 py-3 text-right tabular-nums">{c.completed_exams}</td>
                    <td className="px-4 py-3 text-right tabular-nums">{c.in_progress_exams}</td>
                    <td className="px-4 py-3 text-right tabular-nums">{c.total_violations}</td>
                    <td className="px-4 py-3"><span className={badgeClass(c.is_active ? "success" : "muted")}>{c.is_active ? "Active" : "Disabled"}</span></td>
                    <td className="px-4 py-3">
                      <button type="button" onClick={(e) => { e.stopPropagation(); navigate(`/admin/candidates/${c.id}`); }}
                              className="text-xs font-semibold text-primary hover:underline">
                        View Profile
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <Pagination page={safePage} pageCount={pageCount} onChange={setPage} totalCount={total} pageSize={PAGE_SIZE} />
          </div>
        )}
      </main>
    </div>
  );
}
