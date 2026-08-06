import { useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import RedirectToLogin from "../components/RedirectToLogin.jsx";
import DashboardHeader from "../components/DashboardHeader.jsx";
import Icon from "../components/Icon.jsx";
import EmptyState from "../components/EmptyState.jsx";
import Breadcrumbs from "../components/Breadcrumbs.jsx";
import Pagination, { paginate } from "../components/Pagination.jsx";
import { TextField } from "../components/FormField.jsx";
import { Api, ApiError } from "../lib/api.js";
import { btnPrimary, fieldInput, fieldLabel } from "../lib/ui.js";
import { badgeClass } from "../lib/adminUi.js";
import { isLoggedIn, getRole } from "../lib/auth.js";

const PAGE_SIZE = 10;

/**
 * Download the current view as CSV.
 *
 * The filters are passed through deliberately: an export that silently ignored
 * what is on screen would hand someone a different dataset from the one they
 * were looking at, which is the kind of discrepancy that surfaces in a meeting.
 * The download is recorded server-side in the activity trail.
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

export default function AdminExaminers() {
  const [organizations, setOrganizations] = useState([]);
  // Whether the organization list could be loaded at all. Empty and
  // unavailable are different facts: one means there are no organizations,
  // the other means we could not ask -- and the filter silently degrades to
  // "no filtering" in the second case, which looks identical to a filter that
  // matched everything.
  const [organizationsFailed, setOrganizationsFailed] = useState(false);
  const [rows, setRows] = useState(null);
  const [loadError, setLoadError] = useState("");

  const [searchInput, setSearchInput] = useState("");
  const [search, setSearch] = useState("");
  const [organizationId, setOrganizationId] = useState("");
  const [status, setStatus] = useState("");
  const [page, setPage] = useState(1);
  // The server's figures, not derived from a list the client holds.
  const [total, setTotal] = useState(0);
  const [pageCount, setPageCount] = useState(1);

  const [formOpen, setFormOpen] = useState(false);
  const [form, setForm] = useState({ firstName: "", lastName: "", email: "", organizationName: "" });
  // Confirms what was created and whether the activation email actually went
  // out -- never a password, since the account no longer has one anybody
  // but its eventual owner will ever type.
  const [created, setCreated] = useState(null);
  const [formError, setFormError] = useState("");
  const [creating, setCreating] = useState(false);

  useEffect(() => {
    const t = setTimeout(() => setSearch(searchInput.trim()), 300);
    return () => clearTimeout(t);
  }, [searchInput]);

  // A request ticket, so a slow OLD request cannot overwrite a newer one.
  //
  // The search box debounced but never cancelled: typing "ann" then "annie"
  // fired two requests, and if the first was slower its results replaced the
  // second's. The admin was left looking at results for a query they had
  // already moved past, with the newer term still in the box and nothing to
  // indicate the list was stale.
  const requestRef = useRef(0);

  async function load() {
    const ticket = ++requestRef.current;
    try {
      const params = new URLSearchParams();
      if (search) params.set("search", search);
      if (organizationId) params.set("organization_id", organizationId);
      if (status) params.set("status", status);
      params.set("page", String(page));
      params.set("page_size", String(PAGE_SIZE));
      const data = await Api.get(`/admin/examiners?${params.toString()}`);
      if (ticket !== requestRef.current) return; // a newer search won
      setRows(data.items);
      setTotal(data.total);
      setPageCount(Math.max(1, data.total_pages));
      setLoadError("");
    } catch (err) {
      if (ticket !== requestRef.current) return;
      setLoadError(err instanceof ApiError ? err.message : "Couldn't load examiners.");
    }
  }

  useEffect(() => {
    Api.get("/admin/organizations")
      .then((data) => { setOrganizations(data); setOrganizationsFailed(false); })
      .catch(() => setOrganizationsFailed(true));
  }, []);

  // Filters reset to page 1; changing page refetches at the new offset.
  useEffect(() => {
    setPage(1);
  }, [search, organizationId, status]);

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [search, organizationId, status, page]);

  if (!isLoggedIn() || getRole() !== "admin") return <RedirectToLogin />;

  // The server decides the page; `rows` IS the page.
  const pageRows = rows || [];
  const safePage = page;

  async function handleCreate(e) {
    e.preventDefault();
    setFormError("");
    setCreating(true);
    try {
      const result = await Api.post("/auth/examiners", {
        first_name: form.firstName.trim(),
        last_name: form.lastName.trim(),
        email: form.email.trim(),
        organization_name: form.organizationName.trim(),
      });
      // No password to hand over: the account was created with an unusable
      // random one, and the examiner sets their own through the activation
      // email this just triggered.
      setCreated({ email: result.email, activationSent: result.activation_sent });
      setForm({ firstName: "", lastName: "", email: "", organizationName: "" });
      await load();
      Api.get("/admin/organizations")
      .then((data) => { setOrganizations(data); setOrganizationsFailed(false); })
      .catch(() => setOrganizationsFailed(true));
    } catch (err) {
      setFormError(err instanceof ApiError ? err.message : "Something went wrong.");
    } finally {
      setCreating(false);
    }
  }

  return (
    <div className="min-h-screen bg-page text-ink">
      <DashboardHeader title="Examiners" />
      <main className="max-w-6xl mx-auto w-full p-4 sm:p-6">
        <Breadcrumbs trail={[{ label: "Dashboard", to: "/dashboard" }, { label: "Examiners" }]} />

        <div className="flex flex-wrap items-center justify-between gap-3 mb-5">
          <div>
            <h1 className="text-2xl font-extrabold tracking-tight">Examiners</h1>
            <p className="text-sm text-muted mt-1">Every examiner account across the platform.</p>
          </div>
          <ExportButton kind="examiners" params={{ search, organization_id: organizationId }} />
          <button type="button" onClick={() => setFormOpen((v) => !v)} className={`${btnPrimary.replace("px-5 py-3", "px-4 py-2")} text-xs`}>
            + Add Examiner
          </button>
        </div>

        {loadError && (
          <div className="mb-4 flex items-start gap-2.5 rounded-xl border border-danger/30 bg-danger/5 px-4 py-3 text-sm text-danger">
            <Icon name="alert" width={16} height={16} className="mt-0.5 shrink-0" />
            <span>{loadError}</span>
          </div>
        )}

        {formOpen && (
          <div className="rounded-2xl border border-border bg-surface shadow-card p-5 mb-5 max-w-lg animate-slide-up">
            {formError && (
              <div className="mb-4 flex items-start gap-2.5 rounded-xl border border-danger/30 bg-danger/5 px-4 py-3 text-sm text-danger">
                <Icon name="alert" width={16} height={16} className="mt-0.5 shrink-0" />
                <span>{formError}</span>
              </div>
            )}
            {created && (
              <div className={`mb-4 flex items-start gap-2.5 rounded-xl border px-4 py-3 text-sm ${
                created.activationSent ? "border-success/30 bg-success/5 text-success" : "border-warning/30 bg-warning/5 text-warning"
              }`}>
                <Icon name={created.activationSent ? "check" : "alert"} width={16} height={16} className="mt-0.5 shrink-0" />
                <span>
                  {created.activationSent
                    ? <>Account created. An activation email was sent to <strong>{created.email}</strong> — the examiner sets their own password from that link.</>
                    : <>Account created for <strong>{created.email}</strong>, but the activation email could not be delivered. Use "Resend activation" on their row once your mail settings are fixed.</>}
                </span>
              </div>
            )}
            <form onSubmit={handleCreate}>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-4">
                <TextField id="examinerFirstName" label="First name" icon="user" required
                  value={form.firstName} onChange={(e) => setForm((f) => ({ ...f, firstName: e.target.value }))} />
                <TextField id="examinerLastName" label="Last name" icon="user" required
                  value={form.lastName} onChange={(e) => setForm((f) => ({ ...f, lastName: e.target.value }))} />
              </div>
              <TextField id="examinerEmail" label="Email" icon="mail" type="email" required
                value={form.email} onChange={(e) => setForm((f) => ({ ...f, email: e.target.value }))} />
              <TextField id="examinerOrganization" label="Organization name" icon="briefcase" required
                placeholder="e.g. Acme Institute of Technology"
                value={form.organizationName} onChange={(e) => setForm((f) => ({ ...f, organizationName: e.target.value }))} />
              <p className="text-xs text-muted leading-relaxed mb-4 -mt-1">
                No password to set here — the examiner receives a single-use activation link by email and
                chooses their own password from it.
              </p>
              <button type="submit" disabled={creating} className={`${btnPrimary} w-full ${creating ? "opacity-70 pointer-events-none" : ""}`}>
                {creating && <Icon name="spinner" width={16} height={16} className="animate-spin" />}
                {creating ? "Creating…" : "Create Examiner Account"}
              </button>
            </form>
          </div>
        )}

        <div className="flex flex-wrap gap-3 mb-4">
          <div className="relative flex-1 min-w-[220px]">
            <Icon name="user" width={16} height={16} className="absolute left-3.5 top-1/2 -translate-y-1/2 text-muted pointer-events-none" />
            <input
              type="search"
              placeholder="Search by name or email…"
              value={searchInput}
              onChange={(e) => setSearchInput(e.target.value)}
              className={`${fieldInput} pl-10`}
            />
          </div>
          <select value={organizationId} onChange={(e) => setOrganizationId(e.target.value)} className={`${fieldInput} sm:w-56`}>
            <option value="">{organizationsFailed ? "Organizations unavailable" : "All organizations"}</option>
            {organizations.map((o) => (
              <option key={o.id} value={o.id}>{o.name}</option>
            ))}
          </select>
          <select value={status} onChange={(e) => setStatus(e.target.value)} className={`${fieldInput} sm:w-40`}>
            <option value="">All statuses</option>
            <option value="active">Active</option>
            <option value="disabled">Disabled</option>
          </select>
        </div>

        {!rows ? (
          <div className="skeleton skeleton-card h-64" />
        ) : rows.length === 0 ? (
          <EmptyState icon="briefcase" title="No examiners found" description="Try a different search or filter, or add the first examiner account." />
        ) : (
          <div className="rounded-2xl border border-border bg-surface shadow-card overflow-x-auto">
            <table className="min-w-full text-sm">
              <thead>
                <tr className="text-left text-xs text-muted uppercase tracking-wide border-b border-border">
                  <th className="px-4 py-3 font-semibold">Examiner</th>
                  <th className="px-4 py-3 font-semibold">Email</th>
                  <th className="px-4 py-3 font-semibold">Organization</th>
                  <th className="px-4 py-3 font-semibold text-right">Active</th>
                  <th className="px-4 py-3 font-semibold text-right">Upcoming</th>
                  <th className="px-4 py-3 font-semibold text-right">Completed</th>
                  <th className="px-4 py-3 font-semibold text-right">Candidates</th>
                  <th className="px-4 py-3 font-semibold">Status</th>
                  <th className="px-4 py-3 font-semibold">Action</th>
                </tr>
              </thead>
              <tbody>
                {pageRows.map((e) => (
                  <ExaminerRow key={e.id} examiner={e} onChanged={load} />
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

function ExaminerRow({ examiner, onChanged }) {
  const navigate = useNavigate();
  const [editing, setEditing] = useState(false);
  const [editForm, setEditForm] = useState({ firstName: "", lastName: "", organizationName: examiner.organization_name || "" });
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [busy, setBusy] = useState(false);
  const [rowError, setRowError] = useState("");
  const [resendNotice, setResendNotice] = useState("");

  function goToDetail() {
    navigate(`/admin/examiners/${examiner.id}`);
  }

  async function handleToggleActive(e) {
    e.stopPropagation();
    setBusy(true);
    setRowError("");
    try {
      await Api.post(`/users/${examiner.user_id}/${examiner.is_active ? "deactivate" : "activate"}`);
      onChanged();
    } catch (err) {
      setRowError(err instanceof ApiError ? err.message : "Couldn't update this account.");
    } finally {
      setBusy(false);
    }
  }

  async function handleSaveEdit(e) {
    e.stopPropagation();
    setBusy(true);
    setRowError("");
    try {
      await Api.patch(`/admin/examiners/${examiner.id}`, {
        first_name: editForm.firstName.trim() || undefined,
        last_name: editForm.lastName.trim() || undefined,
        organization_name: editForm.organizationName.trim(),
      });
      setEditing(false);
      onChanged();
    } catch (err) {
      setRowError(err instanceof ApiError ? err.message : "Couldn't save changes.");
    } finally {
      setBusy(false);
    }
  }

  async function handleResendActivation(e) {
    e.stopPropagation();
    setBusy(true);
    setRowError("");
    setResendNotice("");
    try {
      const result = await Api.post(`/auth/examiners/${examiner.user_id}/resend-activation`);
      setResendNotice(result.activation_sent
        ? "Activation email sent."
        : "Could not deliver it — check the server's email configuration and try again.");
    } catch (err) {
      setRowError(err instanceof ApiError ? err.message : "Couldn't resend the activation email.");
    } finally {
      setBusy(false);
    }
  }

  async function handleDelete(e) {
    e.stopPropagation();
    setBusy(true);
    setRowError("");
    try {
      await Api.del(`/admin/examiners/${examiner.id}`);
      onChanged();
    } catch (err) {
      setRowError(err instanceof ApiError ? err.message : "Couldn't delete this examiner.");
      setConfirmingDelete(false);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <tr onClick={goToDetail} className="border-b border-border last:border-0 hover:bg-page/60 transition-colors cursor-pointer">
        <td className="px-4 py-3 font-medium">{examiner.full_name}</td>
        <td className="px-4 py-3 text-muted">{examiner.email}</td>
        <td className="px-4 py-3">{examiner.organization_name || "-"}</td>
        <td className="px-4 py-3 text-right tabular-nums">{examiner.active_exams}</td>
        <td className="px-4 py-3 text-right tabular-nums">{examiner.upcoming_exams}</td>
        <td className="px-4 py-3 text-right tabular-nums">{examiner.completed_exams}</td>
        <td className="px-4 py-3 text-right tabular-nums">{examiner.candidate_count}</td>
        <td className="px-4 py-3">
          <div className="flex flex-col items-start gap-1">
            <span className={badgeClass(examiner.is_active ? "success" : "muted")}>{examiner.is_active ? "Active" : "Disabled"}</span>
            {examiner.pending_activation && (
              <span className={badgeClass("warning")}>Activation pending</span>
            )}
          </div>
        </td>
        <td className="px-4 py-3">
          <div className="flex flex-col items-start gap-1.5">
            <Link to={`/admin/examiners/${examiner.id}`} onClick={(e) => e.stopPropagation()} className="text-xs font-semibold text-primary hover:underline">
              View Details
            </Link>
            {resendNotice && <span className="text-xs text-muted">{resendNotice}</span>}
            <div className="flex flex-wrap gap-2">
              <button type="button" disabled={busy} onClick={(e) => { e.stopPropagation(); setEditing((v) => !v); }}
                      className="text-xs font-semibold text-muted hover:text-ink disabled:opacity-50">
                Edit
              </button>
              {examiner.pending_activation && (
                <button type="button" disabled={busy} onClick={handleResendActivation}
                        className="text-xs font-semibold text-primary hover:underline disabled:opacity-50">
                  Resend activation
                </button>
              )}
              <button type="button" disabled={busy} onClick={handleToggleActive}
                      className="text-xs font-semibold text-muted hover:text-ink disabled:opacity-50">
                {examiner.is_active ? "Disable" : "Enable"}
              </button>
              {confirmingDelete ? (
                <>
                  <button type="button" disabled={busy} onClick={handleDelete} className="text-xs font-semibold text-danger hover:underline disabled:opacity-50">
                    Confirm
                  </button>
                  <button type="button" onClick={(e) => { e.stopPropagation(); setConfirmingDelete(false); }} className="text-xs font-semibold text-muted hover:text-ink">
                    Cancel
                  </button>
                </>
              ) : (
                <button type="button" disabled={busy} onClick={(e) => { e.stopPropagation(); setConfirmingDelete(true); }}
                        className="text-xs font-semibold text-danger hover:underline disabled:opacity-50">
                  Delete
                </button>
              )}
            </div>
          </div>
        </td>
      </tr>
      {(editing || rowError) && (
        <tr className="border-b border-border last:border-0" onClick={(e) => e.stopPropagation()}>
          <td colSpan={9} className="px-4 pb-4">
            {rowError && (
              <div className="mb-3 flex items-start gap-2.5 rounded-xl border border-danger/30 bg-danger/5 px-4 py-3 text-sm text-danger">
                <Icon name="alert" width={16} height={16} className="mt-0.5 shrink-0" />
                <span>{rowError}</span>
              </div>
            )}
            {editing && (
              <div className="rounded-xl border border-border bg-page p-4 max-w-xl">
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 mb-3">
                  <div>
                    <label className={fieldLabel}>First name</label>
                    <input className={fieldInput} placeholder={examiner.full_name.split(" ")[0]}
                           value={editForm.firstName} onChange={(e) => setEditForm((f) => ({ ...f, firstName: e.target.value }))} />
                  </div>
                  <div>
                    <label className={fieldLabel}>Last name</label>
                    <input className={fieldInput} placeholder={examiner.full_name.split(" ").slice(1).join(" ")}
                           value={editForm.lastName} onChange={(e) => setEditForm((f) => ({ ...f, lastName: e.target.value }))} />
                  </div>
                </div>
                <label className={fieldLabel}>Organization name</label>
                <input className={`${fieldInput} mb-3`} value={editForm.organizationName}
                       onChange={(e) => setEditForm((f) => ({ ...f, organizationName: e.target.value }))} />
                <div className="flex gap-2">
                  <button type="button" disabled={busy} onClick={handleSaveEdit} className={`${btnPrimary.replace("px-5 py-3", "px-4 py-2")} text-xs`}>
                    {busy ? "Saving…" : "Save changes"}
                  </button>
                  <button type="button" onClick={() => setEditing(false)} className="px-4 py-2 text-xs font-semibold text-muted hover:text-ink">
                    Cancel
                  </button>
                </div>
              </div>
            )}
          </td>
        </tr>
      )}
    </>
  );
}
