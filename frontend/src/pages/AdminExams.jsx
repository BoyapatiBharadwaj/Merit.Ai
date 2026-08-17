import { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import RedirectToLogin from "../components/RedirectToLogin.jsx";
import DashboardHeader from "../components/DashboardHeader.jsx";
import Icon from "../components/Icon.jsx";
import EmptyState from "../components/EmptyState.jsx";
import Breadcrumbs from "../components/Breadcrumbs.jsx";
import Tabs from "../components/Tabs.jsx";
import Pagination, { paginate } from "../components/Pagination.jsx";
import { Api, ApiError } from "../lib/api.js";
import { fieldInput } from "../lib/ui.js";
import { badgeClass, fmtDate, fmtPercent } from "../lib/adminUi.js";
import { isLoggedIn, getRole } from "../lib/auth.js";

const PAGE_SIZE = 10;

const TABS = [
  { id: "all", label: "All" },
  { id: "active", label: "Active" },
  { id: "upcoming", label: "Upcoming" },
  { id: "completed", label: "Completed" },
];

/** Platform-wide exam list behind the dashboard's Active/Upcoming/Completed
 * cards (and the top-level "Exams" nav item) -- distinct from an individual
 * examiner's own exam tab, since this spans every examiner at once. The
 * initial tab comes from the card that was clicked (?status=active etc.). */
export default function AdminExams() {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const tab = searchParams.get("status") || "all";

  const [rows, setRows] = useState(null);
  const [loadError, setLoadError] = useState("");
  const [searchInput, setSearchInput] = useState("");
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);

  useEffect(() => {
    const t = setTimeout(() => setSearch(searchInput.trim()), 300);
    return () => clearTimeout(t);
  }, [searchInput]);

  useEffect(() => {
    // A slow OLD search must not overwrite a newer one -- see AdminExaminers
    // for the full description of what that looked like to an admin.
    let cancelled = false;
    setRows(null);
    setLoadError("");
    setPage(1);
    const params = new URLSearchParams();
    if (tab !== "all") params.set("status", tab);
    if (search) params.set("search", search);
    const qs = params.toString();
    Api.get(`/admin/exams${qs ? `?${qs}` : ""}`)
      .then((data) => { if (!cancelled) setRows(data); })
      .catch((err) => {
        if (!cancelled) setLoadError(err instanceof ApiError ? err.message : "Couldn't load exams.");
      });
    return () => { cancelled = true; };
  }, [tab, search]);

  if (!isLoggedIn() || getRole() !== "admin") return <RedirectToLogin />;

  // Client-side, deliberately: /admin/exams is one row per exam an institution has ever
  // created, which is bounded by staff activity rather than by candidate numbers -- unlike
  // candidates and violations, which grow with every sitting and are paged by the server.
  const { pageRows, pageCount, safePage } = paginate(rows || [], page, PAGE_SIZE);

  return (
    <div className="min-h-screen bg-page text-ink">
      <DashboardHeader title="Exams" />
      <main className="max-w-6xl mx-auto w-full p-4 sm:p-6">
        <Breadcrumbs trail={[{ label: "Dashboard", to: "/dashboard" }, { label: "Exams" }]} />

        <h1 className="text-2xl font-extrabold tracking-tight mb-1">Exams</h1>
        <p className="text-sm text-muted mb-5">Every exam across every examiner, by lifecycle stage.</p>

        {loadError && (
          <div className="mb-4 flex items-start gap-2.5 rounded-xl border border-danger/30 bg-danger/5 px-4 py-3 text-sm text-danger">
            <Icon name="alert" width={16} height={16} className="mt-0.5 shrink-0" />
            <span>{loadError}</span>
          </div>
        )}

        <Tabs tabs={TABS} active={tab} onChange={(id) => setSearchParams(id === "all" ? {} : { status: id })} />

        <div className="relative mb-4 max-w-sm">
          <Icon name="user" width={16} height={16} className="absolute left-3.5 top-1/2 -translate-y-1/2 text-muted pointer-events-none" />
          <input
            type="search"
            placeholder="Search by exam or examiner name…"
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            className={`${fieldInput} pl-10`}
          />
        </div>

        {!rows ? (
          <div className="skeleton skeleton-card h-64" />
        ) : rows.length === 0 ? (
          <EmptyState icon="doc" title="No exams found" description="Try a different search, or switch tabs." />
        ) : (
          <div className="rounded-2xl border border-border bg-surface shadow-card overflow-x-auto">
            <table className="min-w-full text-sm">
              <thead>
                <tr className="text-left text-xs text-muted uppercase tracking-wide border-b border-border">
                  <th className="px-4 py-3 font-semibold">Exam Name</th>
                  <th className="px-4 py-3 font-semibold">Examiner</th>
                  <th className="px-4 py-3 font-semibold">Type</th>
                  <th className="px-4 py-3 font-semibold">Scheduled Date</th>
                  <th className="px-4 py-3 font-semibold text-right">Enrolled</th>
                  <th className="px-4 py-3 font-semibold text-right">Completed</th>
                  <th className="px-4 py-3 font-semibold text-right">Violations</th>
                  <th className="px-4 py-3 font-semibold text-right">Average Score</th>
                  <th className="px-4 py-3 font-semibold">Status</th>
                  <th className="px-4 py-3 font-semibold">Action</th>
                </tr>
              </thead>
              <tbody>
                {pageRows.map((exam) => (
                  <tr key={exam.id} onClick={() => navigate(`/admin/exams/${exam.id}`)}
                      className="border-b border-border last:border-0 hover:bg-page/60 transition-colors cursor-pointer">
                    <td className="px-4 py-3 font-medium">{exam.title}</td>
                    <td className="px-4 py-3 text-muted">{exam.examiner_name}</td>
                    <td className="px-4 py-3 text-muted">{exam.type}</td>
                    <td className="px-4 py-3 text-muted">{fmtDate(exam.scheduled_date)}</td>
                    <td className="px-4 py-3 text-right tabular-nums">{exam.enrolled}</td>
                    <td className="px-4 py-3 text-right tabular-nums">{exam.completed}</td>
                    <td className="px-4 py-3 text-right tabular-nums">{exam.violations}</td>
                    <td className="px-4 py-3 text-right tabular-nums">{fmtPercent(exam.average_score)}</td>
                    <td className="px-4 py-3"><span className={badgeClass(exam.status === "active" ? "success" : exam.status === "upcoming" ? "primary" : "muted")}>{exam.status}</span></td>
                    <td className="px-4 py-3">
                      <button type="button" onClick={(e) => { e.stopPropagation(); navigate(`/admin/exams/${exam.id}`); }}
                              className="text-xs font-semibold text-primary hover:underline">
                        View
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
