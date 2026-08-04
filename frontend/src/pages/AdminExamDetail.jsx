import { useEffect, useState } from "react";
import { Navigate, useNavigate, useParams } from "react-router-dom";
import DashboardHeader from "../components/DashboardHeader.jsx";
import Icon from "../components/Icon.jsx";
import EmptyState from "../components/EmptyState.jsx";
import Breadcrumbs from "../components/Breadcrumbs.jsx";
import StatCard from "../components/StatCard.jsx";
import { Api, ApiError } from "../lib/api.js";
import { fieldInput } from "../lib/ui.js";
import { badgeClass, fmtDateTime, fmtPercent } from "../lib/adminUi.js";
import { isLoggedIn, getRole } from "../lib/auth.js";

export default function AdminExamDetail() {
  const { examId } = useParams();
  const navigate = useNavigate();

  const [exam, setExam] = useState(null);
  const [students, setStudents] = useState(null);
  const [loadError, setLoadError] = useState("");

  const [searchInput, setSearchInput] = useState("");
  const [search, setSearch] = useState("");
  const [examStatus, setExamStatus] = useState("");
  const [result, setResult] = useState("");
  const [risk, setRisk] = useState("");
  const [verification, setVerification] = useState("");

  useEffect(() => {
    Api.get(`/admin/exams/${examId}`)
      .then(setExam)
      .catch((err) => setLoadError(err instanceof ApiError ? err.message : "Couldn't load this exam."));
  }, [examId]);

  useEffect(() => {
    const t = setTimeout(() => setSearch(searchInput.trim()), 300);
    return () => clearTimeout(t);
  }, [searchInput]);

  useEffect(() => {
    setStudents(null);
    const params = new URLSearchParams();
    if (search) params.set("search", search);
    if (examStatus) params.set("exam_status", examStatus);
    if (result) params.set("result", result);
    if (risk) params.set("risk", risk);
    if (verification) params.set("verification", verification);
    const qs = params.toString();
    Api.get(`/admin/exams/${examId}/students${qs ? `?${qs}` : ""}`)
      .then(setStudents)
      .catch((err) => setLoadError(err instanceof ApiError ? err.message : "Couldn't load enrolled students."));
  }, [examId, search, examStatus, result, risk, verification]);

  if (!isLoggedIn() || getRole() !== "admin") return <Navigate to="/login" replace />;

  return (
    <div className="min-h-screen bg-page text-ink">
      <DashboardHeader title="Exam Details" />
      <main className="max-w-6xl mx-auto w-full p-4 sm:p-6">
        <Breadcrumbs
          trail={[
            { label: "Dashboard", to: "/dashboard" },
            { label: "Examiners", to: "/admin/examiners" },
            { label: exam ? exam.examiner_name : "…", to: exam ? `/admin/examiners/${exam.examiner_id}` : undefined },
            { label: exam ? exam.title : "…" },
          ]}
        />

        {loadError && (
          <div className="mb-4 flex items-start gap-2.5 rounded-xl border border-danger/30 bg-danger/5 px-4 py-3 text-sm text-danger">
            <Icon name="alert" width={16} height={16} className="mt-0.5 shrink-0" />
            <span>{loadError}</span>
          </div>
        )}

        {!exam ? (
          <div className="skeleton skeleton-card h-40 mb-6" />
        ) : (
          <div className="rounded-2xl border border-border bg-surface shadow-card p-5 mb-6">
            <div className="flex flex-wrap items-start justify-between gap-4 mb-1">
              <h1 className="text-xl font-extrabold tracking-tight">{exam.title}</h1>
              <span className={badgeClass(exam.status === "active" ? "success" : exam.status === "upcoming" ? "primary" : "muted")}>{exam.status}</span>
            </div>
            <p className="text-sm text-muted mb-4">Examiner: {exam.examiner_name}</p>
            <dl className="grid grid-cols-2 sm:grid-cols-3 gap-x-6 gap-y-3 text-sm">
              <div><dt className="text-xs text-muted uppercase tracking-wide">Type</dt><dd className="font-medium">{exam.type}</dd></div>
              <div><dt className="text-xs text-muted uppercase tracking-wide">Scheduled</dt><dd className="font-medium">{fmtDateTime(exam.scheduled_date)}</dd></div>
              <div><dt className="text-xs text-muted uppercase tracking-wide">Duration</dt><dd className="font-medium">{exam.duration_minutes} min</dd></div>
              <div><dt className="text-xs text-muted uppercase tracking-wide">Total Marks</dt><dd className="font-medium">{exam.total_marks}</dd></div>
              <div><dt className="text-xs text-muted uppercase tracking-wide">Passing %</dt><dd className="font-medium">{exam.pass_percentage}%</dd></div>
            </dl>
          </div>
        )}

        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-4 mb-6">
          <StatCard label="Enrolled" value={exam ? exam.enrolled : null} tone="primary" />
          <StatCard label="Attended" value={exam ? exam.attended : null} tone="primary" />
          <StatCard label="Submitted" value={exam ? exam.submitted : null} tone="success" />
          <StatCard label="Absent" value={exam ? exam.absent : null} tone="muted" />
          <StatCard label="Violations" value={exam ? exam.violations : null} tone="danger" />
          <StatCard label="Average Score" value={exam ? fmtPercent(exam.average_score) : null} tone="primary" />
        </div>

        <h2 className="text-lg font-bold mb-3">Enrolled Students</h2>

        <div className="flex flex-wrap gap-3 mb-4">
          <div className="relative flex-1 min-w-[200px]">
            <Icon name="user" width={16} height={16} className="absolute left-3.5 top-1/2 -translate-y-1/2 text-muted pointer-events-none" />
            <input type="search" placeholder="Search candidates…" value={searchInput}
                   onChange={(e) => setSearchInput(e.target.value)} className={`${fieldInput} pl-10`} />
          </div>
          <select value={examStatus} onChange={(e) => setExamStatus(e.target.value)} className={`${fieldInput} sm:w-44`}>
            <option value="">All statuses</option>
            <option value="completed">Completed</option>
            <option value="in_progress">In Progress</option>
            <option value="not_started">Not Started</option>
            <option value="absent">Absent</option>
            <option value="terminated">Terminated</option>
          </select>
          <select value={result} onChange={(e) => setResult(e.target.value)} className={`${fieldInput} sm:w-36`}>
            <option value="">All results</option>
            <option value="passed">Passed</option>
            <option value="failed">Failed</option>
          </select>
          <select value={risk} onChange={(e) => setRisk(e.target.value)} className={`${fieldInput} sm:w-36`}>
            <option value="">All risk</option>
            <option value="low">Low</option>
            <option value="medium">Medium</option>
            <option value="high">High</option>
          </select>
          <select value={verification} onChange={(e) => setVerification(e.target.value)} className={`${fieldInput} sm:w-40`}>
            <option value="">All verification</option>
            <option value="verified">Verified</option>
            <option value="pending">Pending</option>
          </select>
        </div>

        {!students ? (
          <div className="skeleton skeleton-card h-64" />
        ) : students.length === 0 ? (
          <EmptyState icon="users" title="No candidates found" description="Try a different search or filter." />
        ) : (
          <div className="rounded-2xl border border-border bg-surface shadow-card overflow-x-auto">
            <table className="min-w-full text-sm">
              <thead>
                <tr className="text-left text-xs text-muted uppercase tracking-wide border-b border-border">
                  <th className="px-4 py-3 font-semibold">Candidate</th>
                  <th className="px-4 py-3 font-semibold">Candidate ID</th>
                  <th className="px-4 py-3 font-semibold">Verification</th>
                  <th className="px-4 py-3 font-semibold">Exam Status</th>
                  <th className="px-4 py-3 font-semibold text-right">Score</th>
                  <th className="px-4 py-3 font-semibold">Result</th>
                  <th className="px-4 py-3 font-semibold text-right">Violations</th>
                  <th className="px-4 py-3 font-semibold">Risk</th>
                  <th className="px-4 py-3 font-semibold">Action</th>
                </tr>
              </thead>
              <tbody>
                {students.map((s) => {
                  const clickable = !!s.attempt_id;
                  return (
                    <tr key={s.student_id}
                        onClick={() => clickable && navigate(`/admin/attempts/${s.attempt_id}`)}
                        className={`border-b border-border last:border-0 transition-colors ${clickable ? "hover:bg-page/60 cursor-pointer" : ""}`}>
                      <td className="px-4 py-3 font-medium">{s.name}</td>
                      <td className="px-4 py-3 text-muted">{s.candidate_id || "-"}</td>
                      <td className="px-4 py-3"><span className={badgeClass(s.verification === "Verified" ? "success" : "warning")}>{s.verification}</span></td>
                      <td className="px-4 py-3"><span className={badgeClass(s.exam_status === "Completed" ? "success" : s.exam_status === "Terminated" ? "danger" : s.exam_status === "In Progress" ? "warning" : "muted")}>{s.exam_status}</span></td>
                      <td className="px-4 py-3 text-right tabular-nums">{fmtPercent(s.score)}</td>
                      <td className="px-4 py-3">{s.result ? <span className={badgeClass(s.result === "Passed" ? "success" : "danger")}>{s.result}</span> : "—"}</td>
                      <td className="px-4 py-3 text-right tabular-nums">{s.violations}</td>
                      <td className="px-4 py-3">{s.risk ? <span className={badgeClass(s.risk)}>{s.risk}</span> : "—"}</td>
                      <td className="px-4 py-3">
                        {clickable ? (
                          <button type="button" onClick={(e) => { e.stopPropagation(); navigate(`/admin/attempts/${s.attempt_id}`); }}
                                  className="text-xs font-semibold text-primary hover:underline">
                            View Report
                          </button>
                        ) : (
                          <span className="text-xs text-muted">—</span>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </main>
    </div>
  );
}
