/* Split out of ExaminerDashboard.jsx -- see examiner/shared.jsx for why. */
import { useEffect, useState } from "react";
import Icon from "../../components/Icon.jsx";
import EmptyState from "../../components/EmptyState.jsx";
import { Api, ApiError } from "../../lib/api.js";
import { btnPrimary, btnGhost, fieldInput, fieldLabel } from "../../lib/ui.js";
import { ExamStatusBadge, AsyncSection } from "./shared.jsx";

function CreateExamForm({ open, onClose, onCreated }) {
  const [form, setForm] = useState({ title: "", duration: "30", passPercentage: "40", description: "", startTime: "", endTime: "", randomize: true, randomizeOptions: true, proctoring: true });
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  if (!open) return null;

  function update(field, value) {
    setForm((f) => ({ ...f, [field]: value }));
  }

  async function handleSubmit(e) {
    e.preventDefault();
    setError("");
    setSubmitting(true);
    try {
      await Api.post("/exams", {
        title: form.title.trim(),
        description: form.description.trim() || null,
        duration_minutes: parseInt(form.duration, 10),
        // parseInt(..) || 40 turned a valid 0 into 40, because 0 is falsy.
        // The server has always accepted a 0% pass mark; the form silently
        // changed the examiner's answer on the way out, and nothing anywhere
        // would ever have shown them it had.
        pass_percentage: Number.isFinite(parseInt(form.passPercentage, 10))
          ? parseInt(form.passPercentage, 10)
          : 40,
        randomize_questions: form.randomize,
        randomize_options: form.randomizeOptions,
        proctoring_enabled: form.proctoring,
        start_time: form.startTime ? new Date(form.startTime).toISOString() : null,
        end_time: form.endTime ? new Date(form.endTime).toISOString() : null,
      });
      setForm({ title: "", duration: "30", passPercentage: "40", description: "", startTime: "", endTime: "", randomize: true, randomizeOptions: true, proctoring: true });
      onClose();
      onCreated();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Something went wrong.");
    } finally {
      setSubmitting(false);
    }
  }

  const inputCls = fieldInput;
  const labelCls = fieldLabel;

  return (
    <div className="rounded-2xl border border-border bg-surface shadow-card p-5 mb-6 max-w-2xl animate-slide-up">
      <div className="font-bold text-ink mb-4">Create Exam</div>
      {error && (
        <div className="mb-4 flex items-start gap-2.5 rounded-xl border border-danger/30 bg-danger/5 px-4 py-3 text-sm text-danger">
          <Icon name="alert" width={16} height={16} className="mt-0.5 shrink-0" />
          <span>{error}</span>
        </div>
      )}
      <form onSubmit={handleSubmit} className="flex flex-col gap-4">
        <div className="grid sm:grid-cols-2 gap-4">
          <div>
            <label className={labelCls}>Title</label>
            <input required value={form.title} onChange={(e) => update("title", e.target.value)} className={inputCls} />
          </div>
          <div>
            <label className={labelCls}>Duration (minutes)</label>
            <input required type="number" min="1" value={form.duration} onChange={(e) => update("duration", e.target.value)} className={inputCls} />
          </div>
        </div>
        <div className="grid sm:grid-cols-2 gap-4">
          <div>
            <label className={labelCls}>Passing Percentage</label>
            <input required type="number" min="0" max="100" value={form.passPercentage} onChange={(e) => update("passPercentage", e.target.value)} className={inputCls} />
          </div>
        </div>
        <div>
          <label className={labelCls}>Description</label>
          <textarea rows={2} value={form.description} onChange={(e) => update("description", e.target.value)} className={inputCls} />
        </div>
        <div className="grid sm:grid-cols-2 gap-4">
          <div>
            <label className={labelCls}>Start Time (optional)</label>
            <input type="datetime-local" value={form.startTime} onChange={(e) => update("startTime", e.target.value)} className={inputCls} />
          </div>
          <div>
            <label className={labelCls}>End Time (optional)</label>
            <input type="datetime-local" value={form.endTime} onChange={(e) => update("endTime", e.target.value)} className={inputCls} />
          </div>
        </div>
        <div className="grid sm:grid-cols-3 gap-4">
          <label className="flex items-center gap-2 text-sm text-ink">
            <input type="checkbox" checked={form.randomize} onChange={(e) => update("randomize", e.target.checked)} className="w-4 h-4 accent-primary" />
            Randomize question order
          </label>
          <label className="flex items-center gap-2 text-sm text-ink">
            <input type="checkbox" checked={form.randomizeOptions} onChange={(e) => update("randomizeOptions", e.target.checked)} className="w-4 h-4 accent-primary" />
            Randomize answer choices
          </label>
          <label className="flex items-center gap-2 text-sm text-ink">
            <input type="checkbox" checked={form.proctoring} onChange={(e) => update("proctoring", e.target.checked)} className="w-4 h-4 accent-primary" />
            Enable AI proctoring
          </label>
        </div>
        <p className="text-xs text-muted -mt-2">
          Everything here, plus title, description, and instructions, can be changed later while the exam is still a draft.
        </p>
        <button type="submit" disabled={submitting} className={`${btnPrimary} self-start ${submitting ? "opacity-70 pointer-events-none" : ""}`}>
          {submitting ? "Creating…" : "Create Exam (Draft)"}
        </button>
      </form>
    </div>
  );
}

function ExamsListView({ onManage }) {
  const [exams, setExams] = useState(null);
  const [loadError, setLoadError] = useState("");
  const [createOpen, setCreateOpen] = useState(false);
  const [confirmDeleteExamId, setConfirmDeleteExamId] = useState(null);
  const [deletingExamId, setDeletingExamId] = useState(null);
  const [deleteExamError, setDeleteExamError] = useState(null);

  async function loadExams() {
    try {
      setLoadError("");
      setExams(await Api.get("/exams/my"));
    } catch (err) {
      setLoadError(err instanceof ApiError ? err.message : "Couldn't load your exams.");
    }
  }

  useEffect(() => {
    loadExams();
  }, []);

  // Draft-only, mirroring the backend's own gate (exam_service._get_editable_exam)
  // -- once an exam is published it may already have real attempts riding on
  // it, so deletion is never offered for anything but a draft.
  async function handleDeleteExam(examId) {
    setDeletingExamId(examId);
    setDeleteExamError(null);
    try {
      await Api.del(`/exams/${examId}`);
      setConfirmDeleteExamId(null);
      await loadExams();
    } catch (err) {
      setDeleteExamError({ id: examId, message: err instanceof ApiError ? err.message : "Couldn't delete this exam." });
    } finally {
      setDeletingExamId(null);
    }
  }

  return (
    <main className="max-w-6xl mx-auto w-full p-4 sm:p-6">
      <div className="flex flex-wrap items-center justify-between gap-3 mb-6">
        <div>
          <h1 className="text-2xl font-extrabold tracking-tight">Examiner Dashboard</h1>
          <p className="text-sm text-muted mt-1">Create, manage, and monitor your exams.</p>
        </div>
        <button type="button" onClick={() => setCreateOpen((v) => !v)} className={btnPrimary.replace("px-5 py-3", "px-4 py-2.5")}>
          + New Exam
        </button>
      </div>

      <CreateExamForm open={createOpen} onClose={() => setCreateOpen(false)} onCreated={loadExams} />

      {loadError && (
        <div className="mb-6 flex items-start gap-2.5 rounded-xl border border-danger/30 bg-danger/5 px-4 py-3 text-sm text-danger">
          <Icon name="alert" width={16} height={16} className="mt-0.5 shrink-0" />
          <span>{loadError}</span>
        </div>
      )}

      <div className="text-xs font-bold uppercase tracking-wider text-muted mb-3">My Exams</div>
      {!exams ? (
        <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-4">
          <div className="skeleton skeleton-card" />
          <div className="skeleton skeleton-card" />
          <div className="skeleton skeleton-card" />
        </div>
      ) : exams.length ? (
        <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {exams.map((e) => (
            <div
              key={e.id}
              className="rounded-2xl border border-border bg-surface shadow-card p-4 flex flex-col animate-fade-in transition-all duration-200 hover:-translate-y-0.5 hover:border-primary/30 hover:shadow-[0_16px_32px_-16px_rgba(15,23,42,0.2)]"
            >
              <div className="flex items-center justify-between gap-2 mb-2">
                <strong className="text-sm">{e.title}</strong>
                <ExamStatusBadge status={e.status} />
              </div>
              <div className="text-muted text-xs mb-4">
                {e.duration_minutes} min · {e.randomize_questions ? "Randomized" : "Fixed order"}
                {e.start_time ? ` · opens ${new Date(e.start_time).toLocaleString()}` : ""}
              </div>

              {confirmDeleteExamId === e.id ? (
                <div className="mt-auto rounded-lg border border-danger/30 bg-danger/5 px-2.5 py-2">
                  <p className="text-xs text-danger font-medium mb-2">Delete this draft? This can't be undone.</p>
                  <div className="flex items-center gap-3">
                    <button
                      type="button"
                      onClick={() => handleDeleteExam(e.id)}
                      disabled={deletingExamId === e.id}
                      className="text-xs font-semibold text-danger hover:underline disabled:opacity-60"
                    >
                      {deletingExamId === e.id ? "Deleting…" : "Yes, delete"}
                    </button>
                    <button
                      type="button"
                      onClick={() => setConfirmDeleteExamId(null)}
                      disabled={deletingExamId === e.id}
                      className="text-xs font-semibold text-muted hover:text-ink disabled:opacity-60"
                    >
                      Cancel
                    </button>
                  </div>
                  {deleteExamError?.id === e.id && <p className="text-xs text-danger mt-1.5">{deleteExamError.message}</p>}
                </div>
              ) : (
                <div className="mt-auto flex items-center gap-2">
                  <button type="button" onClick={() => onManage(e.id)} className={`${btnGhost.replace("px-5 py-3", "px-4 py-2")} flex-1`}>
                    Manage
                  </button>
                  {e.status === "draft" && (
                    <button
                      type="button"
                      onClick={() => setConfirmDeleteExamId(e.id)}
                      title="Delete draft"
                      className="shrink-0 inline-flex items-center justify-center w-9 h-9 rounded-lg border border-border text-danger hover:bg-danger/5 hover:border-danger/30 transition-colors"
                    >
                      <Icon name="trash" width={15} height={15} />
                    </button>
                  )}
                </div>
              )}
            </div>
          ))}
        </div>
      ) : (
        <EmptyState icon="doc" title="No exams yet" description="Create your first exam using the button above to get started." />
      )}
    </main>
  );
}

/* ===================== Exam builder: sections & questions ===================== */

// Every question needs at least two options to mean anything (a
// single-option "choice" isn't one) -- the same floor the backend enforces
// in schemas/question.py. Four is just a friendly starting point; the
// examiner can add or remove rows freely down to this floor or up to
// however many the question needs (True/False needs 2, a longer list can
// have 5+).

export { CreateExamForm, ExamsListView };
