import { useEffect, useState } from "react";
import { Navigate, useNavigate } from "react-router-dom";
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

export default function AdminCandidates() {
  const navigate = useNavigate();
  const [organizations, setOrganizations] = useState([]);
  const [rows, setRows] = useState(null);
  const [loadError, setLoadError] = useState("");

  const [searchInput, setSearchInput] = useState("");
  const [search, setSearch] = useState("");
  const [organizationId, setOrganizationId] = useState("");
  const [page, setPage] = useState(1);

  useEffect(() => {
    Api.get("/admin/organizations").then(setOrganizations).catch(() => {});
  }, []);

  useEffect(() => {
    const t = setTimeout(() => setSearch(searchInput.trim()), 300);
    return () => clearTimeout(t);
  }, [searchInput]);

  useEffect(() => {
    setRows(null);
    setPage(1);
    const params = new URLSearchParams();
    if (search) params.set("search", search);
    if (organizationId) params.set("organization_id", organizationId);
    const qs = params.toString();
    Api.get(`/admin/candidates${qs ? `?${qs}` : ""}`)
      .then(setRows)
      .catch((err) => setLoadError(err instanceof ApiError ? err.message : "Couldn't load candidates."));
  }, [search, organizationId]);

  if (!isLoggedIn() || getRole() !== "admin") return <Navigate to="/login" replace />;

  const { pageRows, pageCount, safePage } = paginate(rows || [], page, PAGE_SIZE);

  return (
    <div className="min-h-screen bg-page text-ink">
      <DashboardHeader title="Candidates" />
      <main className="max-w-6xl mx-auto w-full p-4 sm:p-6">
        <Breadcrumbs trail={[{ label: "Dashboard", to: "/dashboard" }, { label: "Candidates" }]} />

        <h1 className="text-2xl font-extrabold tracking-tight mb-1">Candidates</h1>
        <p className="text-sm text-muted mb-5">Every registered student across the platform.</p>

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
            <option value="">All organizations</option>
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
            <Pagination page={safePage} pageCount={pageCount} onChange={setPage} totalCount={rows.length} pageSize={PAGE_SIZE} />
          </div>
        )}
      </main>
    </div>
  );
}
