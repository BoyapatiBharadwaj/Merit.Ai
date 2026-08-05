import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import RedirectToLogin from "../components/RedirectToLogin.jsx";
import DashboardHeader from "../components/DashboardHeader.jsx";
import Breadcrumbs from "../components/Breadcrumbs.jsx";
import EmptyState from "../components/EmptyState.jsx";
import Icon from "../components/Icon.jsx";
import { Api, ApiError } from "../lib/api.js";
import { btnGhost } from "../lib/ui.js";
import { isLoggedIn, getRole } from "../lib/auth.js";

/**
 * Organizations, and what is in each one.
 *
 * An organization is this platform's tenancy boundary: it decides which
 * candidates an examiner can see, which roster an exam draws from, and which
 * students may sit it. Every access decision runs through it — and there was no
 * page anywhere showing which organizations existed, let alone what was in
 * them. An administrator investigating "why can this examiner see these
 * candidates?" had to infer the answer from two other screens.
 *
 * Registered and invited are shown separately on purpose. "50 on the roster,
 * 12 registered" is the number someone chasing enrolment before an exam
 * actually needs; one combined figure hides the 38 who cannot sit it yet.
 */
export default function AdminOrganizations() {
  const [rows, setRows] = useState([]);
  const [state, setState] = useState("loading");
  const [error, setError] = useState("");

  const load = useCallback(() => {
    let cancelled = false;
    setState("loading");
    Api.get("/admin/organizations/overview")
      .then((data) => {
        if (cancelled) return;
        setRows(data || []);
        setState("ready");
      })
      .catch((err) => {
        if (cancelled) return;
        setError(err instanceof ApiError ? err.message : "Couldn't load organizations.");
        setState("error");
      });
    return () => { cancelled = true; };
  }, []);

  useEffect(() => load(), [load]);

  if (!isLoggedIn() || getRole() !== "admin") return <RedirectToLogin />;

  const totals = rows.reduce(
    (acc, row) => ({
      examiners: acc.examiners + row.examiner_count,
      candidates: acc.candidates + row.candidate_count,
      exams: acc.exams + row.exam_count,
      pending: acc.pending + row.pending_invites,
    }),
    { examiners: 0, candidates: 0, exams: 0, pending: 0 },
  );

  return (
    <div className="min-h-screen bg-page text-ink">
      <DashboardHeader title="Admin" />
      <main className="max-w-5xl mx-auto w-full p-4 sm:p-6">
        <Breadcrumbs items={[{ label: "Admin", to: "/dashboard" }, { label: "Organizations" }]} />

        <div className="mt-4 mb-5">
          <h1 className="text-2xl font-extrabold tracking-tight">Organizations</h1>
          <p className="text-sm text-muted mt-1">
            The tenancy boundary: which candidates an examiner can see, and which exams a student
            may sit, both follow from these.
          </p>
        </div>

        {state === "loading" && (
          <div className="h-48 rounded-2xl border border-border bg-surface animate-pulse" aria-hidden="true" />
        )}

        {state === "error" && (
          <div role="alert" className="rounded-2xl border border-danger/30 bg-danger/5 p-5">
            <p className="font-semibold text-ink mb-1">Couldn't load organizations.</p>
            <p className="text-sm text-muted mb-3">{error}</p>
            <button onClick={load} className={`${btnGhost} px-4 py-2 text-sm`}>Try again</button>
          </div>
        )}

        {state === "ready" && rows.length === 0 && (
          <EmptyState
            icon="briefcase"
            title="No organizations yet"
            description="An organization is created the first time an examiner account names one."
          />
        )}

        {state === "ready" && rows.length > 0 && (
          <>
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-5">
              {[
                ["Organizations", rows.length],
                ["Examiners", totals.examiners],
                ["Candidates", totals.candidates],
                ["Exams", totals.exams],
              ].map(([label, value]) => (
                <div key={label} className="rounded-xl border border-border bg-surface p-4">
                  <div className="text-2xl font-extrabold text-ink tabular-nums">{value}</div>
                  <div className="text-xs font-semibold uppercase tracking-wide text-muted mt-0.5">{label}</div>
                </div>
              ))}
            </div>

            <div className="rounded-2xl border border-border bg-surface shadow-card overflow-hidden">
              <div className="overflow-x-auto">
                <table className="min-w-full text-sm">
                  <thead>
                    <tr className="text-left text-xs text-muted uppercase tracking-wide border-b border-border">
                      <th className="py-3 px-4 font-semibold">Organization</th>
                      <th className="py-3 px-4 font-semibold">Examiners</th>
                      <th className="py-3 px-4 font-semibold">Candidates</th>
                      <th className="py-3 px-4 font-semibold">Invited, not registered</th>
                      <th className="py-3 px-4 font-semibold">Exams</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((row) => (
                      <tr key={row.id} className="border-b border-border last:border-0 hover:bg-page/60 transition-colors">
                        <td className="py-3 px-4 font-medium text-ink">{row.name}</td>
                        <td className="py-3 px-4 tabular-nums">
                          <Link to={`/admin/examiners?organization_id=${row.id}`}
                                className="text-primary hover:underline">
                            {row.examiner_count}
                          </Link>
                        </td>
                        <td className="py-3 px-4 tabular-nums">
                          <Link to={`/admin/candidates?organization_id=${row.id}`}
                                className="text-primary hover:underline">
                            {row.candidate_count}
                          </Link>
                        </td>
                        <td className="py-3 px-4 tabular-nums">
                          {row.pending_invites > 0 ? (
                            <span className="inline-flex items-center gap-1.5 text-warning font-semibold">
                              <Icon name="alert" width={13} height={13} />
                              {row.pending_invites}
                            </span>
                          ) : (
                            <span className="text-muted">—</span>
                          )}
                        </td>
                        <td className="py-3 px-4 tabular-nums">{row.exam_count}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>

            {totals.pending > 0 && (
              <p className="mt-4 text-sm text-muted leading-relaxed">
                {totals.pending} address{totals.pending === 1 ? " is" : "es are"} on a roster without a
                registered account. Those people cannot sit an exam until they sign up with that exact
                address — worth chasing before exam day rather than on it.
              </p>
            )}
          </>
        )}
      </main>
    </div>
  );
}
