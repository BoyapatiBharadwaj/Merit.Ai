import { useCallback, useEffect, useRef, useState } from "react";
import DashboardHeader from "../components/DashboardHeader.jsx";
import Icon from "../components/Icon.jsx";
import EmptyState from "../components/EmptyState.jsx";
import IdentityPhotoModal from "../components/IdentityPhotoModal.jsx";
import { Api, ApiError } from "../lib/api.js";
import { btnPrimary, btnGhost, fieldInput, fieldLabel, fieldInputCompact, fieldLabelCompact } from "../lib/ui.js";

const CHART_PALETTE = ["#2563eb", "#16a34a", "#d97706", "#dc2626", "#7c3aed", "#0891b2", "#db2777", "#65a30d"];
const EXAM_STATUS_STYLES = {
  draft: "bg-page text-muted border border-border",
  published: "bg-success/10 text-success",
  closed: "bg-muted/10 text-muted",
};
const SEVERITY_STYLES = {
  low: "bg-primary/10 text-primary",
  medium: "bg-warning/10 text-warning",
  high: "bg-danger/10 text-danger",
};

function ExamStatusBadge({ status }) {
  return <span className={`inline-flex items-center rounded-full px-2.5 py-1 text-xs font-semibold capitalize ${EXAM_STATUS_STYLES[status] || "bg-page text-muted border border-border"}`}>{status}</span>;
}

/* ===================== Exams list view ===================== */

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
        pass_percentage: parseInt(form.passPercentage, 10) || 40,
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
const MIN_OPTIONS = 2;
const emptyOptions = () => [
  { text: "", isCorrect: true },
  { text: "", isCorrect: false },
  { text: "", isCorrect: false },
  { text: "", isCorrect: false },
];
const emptyTestCases = () => [
  { input: "", expected_output: "", is_sample: true },
  { input: "", expected_output: "", is_sample: false },
];

// Reshape an existing QuestionOut (id/is_correct-keyed, from the API) into
// this form's local editable-row shape (isCorrect). Keeps however many
// options the question actually has -- the count is dynamic, not a fixed 4
// -- only padding up to the MIN_OPTIONS floor for the (shouldn't-happen)
// case of a question with fewer than that already stored.
function optionsFromExisting(question) {
  const rows = (question.options || []).map((o) => ({ text: o.text, isCorrect: !!o.is_correct }));
  while (rows.length < MIN_OPTIONS) rows.push({ text: "", isCorrect: false });
  return rows;
}
function testCasesFromExisting(question) {
  const rows = (question.test_cases || []).map((c) => ({
    input: c.input || "", expected_output: c.expected_output || "", is_sample: !!c.is_sample,
  }));
  return rows.length ? rows : emptyTestCases();
}

// Shared by "+ Add Question" (question=null) and each card's "Edit" action
// (question=the existing QuestionOut). Editing pre-fills every field from
// the existing question and PUTs a full replacement on save, instead of
// POSTing a new one.
function QuestionForm({ sectionId, question, onSaved, onCancel }) {
  const isEditing = Boolean(question);
  const [text, setText] = useState(question?.text || "");
  const [marks, setMarks] = useState(String(question?.marks ?? 1));
  const [type, setType] = useState(question?.question_type || "mcq");
  const [options, setOptions] = useState(() => (question ? optionsFromExisting(question) : emptyOptions()));
  const [language, setLanguage] = useState(question?.language || "python");
  const [starterCode, setStarterCode] = useState(question?.starter_code || "");
  const [timeLimit, setTimeLimit] = useState(String(question?.time_limit_seconds || 6));
  const [testCases, setTestCases] = useState(() => (question ? testCasesFromExisting(question) : emptyTestCases()));
  const [explanation, setExplanation] = useState(question?.explanation || "");
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);

  function setOption(i, field, value) {
    setOptions((opts) => opts.map((o, idx) => {
      if (idx === i) return { ...o, [field]: value };
      // MCQ is single-answer (radio semantics): picking a new correct option
      // clears any previous one. Multi-select options are independent
      // checkboxes, so other rows are left alone.
      if (field === "isCorrect" && type === "mcq") return { ...o, isCorrect: false };
      return o;
    }));
  }
  function addOption() {
    setOptions((opts) => [...opts, { text: "", isCorrect: false }]);
  }
  function removeOption(i) {
    setOptions((opts) => {
      if (opts.length <= MIN_OPTIONS) return opts;
      const removed = opts[i];
      const next = opts.filter((_, idx) => idx !== i);
      // Removing a single-answer MCQ's one marked-correct option would
      // otherwise leave none, which the server rejects -- auto-promote the
      // first remaining option so "exactly one correct" never silently
      // breaks from a removal alone.
      if (type === "mcq" && removed?.isCorrect && next.length && !next.some((o) => o.isCorrect)) {
        next[0] = { ...next[0], isCorrect: true };
      }
      return next;
    });
  }
  function setTestCase(i, field, value) {
    setTestCases((rows) => rows.map((r, idx) => (idx === i ? { ...r, [field]: value } : r)));
  }
  function addTestCase() {
    setTestCases((rows) => [...rows, { input: "", expected_output: "", is_sample: false }]);
  }
  function removeTestCase(i) {
    setTestCases((rows) => rows.filter((_, idx) => idx !== i));
  }

  async function handleSubmit(e) {
    e.preventDefault();
    setError("");
    setSaving(true);
    try {
      let payload = { text: text.trim(), marks: parseInt(marks, 10) || 1, order_index: question?.order_index ?? 0, question_type: type, explanation: explanation.trim() || null };
      if (type === "coding") {
        payload = {
          ...payload,
          language,
          starter_code: starterCode,
          time_limit_seconds: parseInt(timeLimit, 10) || 6,
          test_cases: testCases.map((r) => ({ input: r.input, expected_output: r.expected_output, is_sample: r.is_sample })),
          options: [],
        };
      } else {
        payload.options = options.filter((o) => o.text.trim()).map((o) => ({ text: o.text.trim(), is_correct: o.isCorrect }));
      }
      if (isEditing) {
        await Api.put(`/exams/questions/${question.id}`, payload);
      } else {
        await Api.post(`/exams/sections/${sectionId}/questions`, payload);
      }
      onSaved();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Something went wrong.");
    } finally {
      setSaving(false);
    }
  }

  const inputCls = fieldInputCompact;
  const labelCls = fieldLabelCompact;

  return (
    <form onSubmit={handleSubmit} className="mt-4 border-t border-border pt-4 flex flex-col gap-3">
      {error && (
        <div className="flex items-start gap-2.5 rounded-xl border border-danger/30 bg-danger/5 px-3 py-2.5 text-sm text-danger">
          <Icon name="alert" width={15} height={15} className="mt-0.5 shrink-0" />
          <span>{error}</span>
        </div>
      )}
      <div>
        <label className={labelCls}>Question Text</label>
        <textarea required rows={2} value={text} onChange={(e) => setText(e.target.value)} className={inputCls} />
      </div>
      <div className="grid sm:grid-cols-2 gap-3">
        <div>
          <label className={labelCls}>Marks</label>
          <input required type="number" min="1" value={marks} onChange={(e) => setMarks(e.target.value)} className={inputCls} />
        </div>
        <div>
          <label className={labelCls}>Question Type</label>
          <select value={type} onChange={(e) => setType(e.target.value)} className={inputCls}>
            <option value="mcq">Multiple Choice (single answer)</option>
            <option value="multi_select">Multiple Selection (one or more answers)</option>
            <option value="coding">Coding</option>
          </select>
        </div>
      </div>

      <div>
        <label className={labelCls}>Explanation (optional -- shown to the candidate on their report)</label>
        <textarea rows={2} value={explanation} onChange={(e) => setExplanation(e.target.value)} className={inputCls} placeholder="Why this is the correct answer..." />
      </div>

      {(type === "mcq" || type === "multi_select") ? (
        <div className="flex flex-col gap-2">
          <p className="text-[11px] text-muted -mt-1">
            {type === "mcq" ? "Select exactly one correct option." : "Select one or more correct options -- a candidate must pick precisely this set to be marked correct."}
          </p>
          {options.map((opt, i) => (
            <div key={i} className="flex items-center gap-2">
              <input placeholder={`Option ${i + 1}`} value={opt.text} onChange={(e) => setOption(i, "text", e.target.value)} className={`${inputCls} flex-1`} />
              <label className="flex items-center gap-1.5 text-xs text-muted shrink-0">
                {type === "mcq" ? (
                  <input type="radio" name={`correct-${sectionId}`} checked={opt.isCorrect} onChange={() => setOption(i, "isCorrect", true)} className="accent-primary" />
                ) : (
                  <input type="checkbox" checked={opt.isCorrect} onChange={(e) => setOption(i, "isCorrect", e.target.checked)} className="accent-primary" />
                )}
                Correct
              </label>
              <button
                type="button"
                onClick={() => removeOption(i)}
                disabled={options.length <= MIN_OPTIONS}
                title={options.length <= MIN_OPTIONS ? `At least ${MIN_OPTIONS} options are required` : "Remove this option"}
                className="shrink-0 text-muted hover:text-danger disabled:opacity-30 disabled:hover:text-muted disabled:cursor-not-allowed"
              >
                <Icon name="x" width={14} height={14} />
              </button>
            </div>
          ))}
          <button type="button" onClick={addOption} className={`${btnGhost.replace("px-5 py-3", "px-3 py-1.5")} text-xs self-start`}>
            + Add Option
          </button>
        </div>
      ) : (
        <div className="flex flex-col gap-3">
          <div className="grid sm:grid-cols-2 gap-3">
            <div>
              <label className={labelCls}>Language</label>
              <select value={language} onChange={(e) => setLanguage(e.target.value)} className={inputCls}>
                <option value="python">Python</option>
                <option value="javascript">JavaScript</option>
              </select>
            </div>
            <div>
              <label className={labelCls}>Time limit per test case (seconds)</label>
              <input type="number" min="1" max="20" value={timeLimit} onChange={(e) => setTimeLimit(e.target.value)} className={inputCls} />
            </div>
          </div>
          <div>
            <label className={labelCls}>Starter Code (shown to students)</label>
            <textarea rows={4} value={starterCode} onChange={(e) => setStarterCode(e.target.value)} placeholder={"def solve():\n    pass"} className={`${inputCls} font-mono`} />
          </div>
          <div>
            <label className={labelCls}>Test Cases (stdin → expected stdout, exact match after trimming whitespace)</label>
            <div className="flex flex-col gap-2">
              {testCases.map((row, i) => (
                <div key={i} className="rounded-lg border border-border p-2.5">
                  <div className="grid sm:grid-cols-2 gap-2 mb-2">
                    <div>
                      <label className="block text-[11px] text-muted mb-1">Input (stdin)</label>
                      <textarea rows={2} value={row.input} onChange={(e) => setTestCase(i, "input", e.target.value)} className={`${inputCls} font-mono`} />
                    </div>
                    <div>
                      <label className="block text-[11px] text-muted mb-1">Expected Output</label>
                      <textarea rows={2} value={row.expected_output} onChange={(e) => setTestCase(i, "expected_output", e.target.value)} className={`${inputCls} font-mono`} />
                    </div>
                  </div>
                  <div className="flex items-center justify-between">
                    <label className="flex items-center gap-1.5 text-xs text-muted">
                      <input type="checkbox" checked={row.is_sample} onChange={(e) => setTestCase(i, "is_sample", e.target.checked)} className="accent-primary" />
                      Sample (visible to students; needs at least one)
                    </label>
                    <button type="button" onClick={() => removeTestCase(i)} className="text-xs font-semibold text-danger hover:underline">
                      Remove
                    </button>
                  </div>
                </div>
              ))}
            </div>
            <button type="button" onClick={addTestCase} className={`${btnGhost.replace("px-5 py-3", "px-3 py-1.5")} text-xs mt-2`}>
              + Add Test Case
            </button>
          </div>
        </div>
      )}

      <div className="flex gap-2">
        <button type="submit" disabled={saving} className={`${btnPrimary.replace("px-5 py-3", "px-4 py-2")} text-sm ${saving ? "opacity-70 pointer-events-none" : ""}`}>
          {saving ? "Saving…" : isEditing ? "Save Changes" : "Save Question"}
        </button>
        <button type="button" onClick={onCancel} className={`${btnGhost.replace("px-5 py-3", "px-4 py-2")} text-sm`}>
          Cancel
        </button>
      </div>
    </form>
  );
}

// Format: Question | Option1 | Option2 | ... | OptionN | CorrectNumber | Marks | Explanation
// The option list is dynamic (2 or more -- e.g. True/False needs just two),
// so Marks and Explanation are read from the *last two* fields and
// CorrectNumber from the third-to-last, with everything in between treated
// as options -- rather than fixed positions, which only worked when every
// question had exactly four options. Marks/Explanation may be left blank
// (an empty segment between two pipes), but their pipes must still be
// present so the parser knows where the option list ends.
function parseBulkLine(line) {
  const parts = line.split("|").map((p) => p.trim());
  if (parts.length < 6) return null; // text + >=2 options + correct + marks + explanation
  const text = parts[0];
  const explanation = parts[parts.length - 1];
  const marksStr = parts[parts.length - 2];
  const correctStr = parts[parts.length - 3];
  const optionTexts = parts.slice(1, parts.length - 3);
  const correctIdx = parseInt(correctStr, 10) - 1;
  const options = optionTexts.filter(Boolean).map((t, i) => ({ text: t, is_correct: i === correctIdx }));
  if (!text || options.length < 2 || correctIdx < 0 || correctIdx >= options.length) return null;
  return {
    text, marks: parseInt(marksStr, 10) || 1, order_index: 0, question_type: "mcq", options,
    explanation: explanation || null,
  };
}

function BulkImportForm({ sectionId, onImported, onCancel }) {
  const [text, setText] = useState("");
  const [status, setStatus] = useState("");
  const [importing, setImporting] = useState(false);

  async function handleImport() {
    setStatus("");
    const lines = text.split("\n").map((l) => l.trim()).filter(Boolean);
    const parsed = lines.map(parseBulkLine);
    const invalidCount = parsed.filter((p) => !p).length;
    const valid = parsed.filter(Boolean);
    if (!valid.length) {
      setStatus("No valid rows found. Check the format: Question | Option1 | Option2 | ... | CorrectNumber | Marks | Explanation");
      return;
    }
    setImporting(true);
    let created = 0;
    let failed = 0;
    for (const question of valid) {
      try {
        await Api.post(`/exams/sections/${sectionId}/questions`, question);
        created++;
      } catch {
        failed++;
      }
    }
    setImporting(false);
    onImported();
    if (invalidCount || failed) {
      setStatus(`Imported ${created} question(s). ${invalidCount} row(s) had a bad format and ${failed} were rejected by the server.`);
    } else {
      setText("");
    }
  }

  return (
    <div className="mt-4 border-t border-border pt-4 flex flex-col gap-3">
      <label className="block text-xs font-semibold text-muted">
        Paste one question per line:{" "}
        <code className="text-ink">Question text | Option 1 | Option 2 | ... | CorrectOptionNumber | Marks | Explanation</code>
      </label>
      <p className="text-[11px] text-muted -mt-1">
        Any number of options is fine (2 or more -- e.g. True/False just needs two). Marks and Explanation can be left
        blank, but keep their pipes, e.g. <code className="text-ink">... | 2 | | </code> for no explanation.
      </p>
      <textarea
        rows={6}
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder={"2 + 2 = ? | 3 | 4 | 5 | 6 | 2 | 1 | Basic addition\nIs the sky blue? | True | False | 1 | 1 |"}
        className={`${fieldInputCompact} font-mono`}
      />
      {status && (
        <div className="flex items-start gap-2.5 rounded-xl border border-danger/30 bg-danger/5 px-3 py-2.5 text-sm text-danger">
          <Icon name="alert" width={15} height={15} className="mt-0.5 shrink-0" />
          <span>{status}</span>
        </div>
      )}
      <div className="flex gap-2">
        <button type="button" onClick={handleImport} disabled={importing} className={`${btnPrimary.replace("px-5 py-3", "px-4 py-2")} text-sm ${importing ? "opacity-70 pointer-events-none" : ""}`}>
          {importing ? "Importing…" : "Import Questions"}
        </button>
        <button type="button" onClick={onCancel} className={`${btnGhost.replace("px-5 py-3", "px-4 py-2")} text-sm`}>
          Cancel
        </button>
      </div>
    </div>
  );
}

function SectionCard({ section, isDraft = false, onQuestionAdded, onReordered }) {
  const [formOpen, setFormOpen] = useState(false);
  const [bulkOpen, setBulkOpen] = useState(false);
  const [renaming, setRenaming] = useState(false);
  const [titleDraft, setTitleDraft] = useState(section.title);
  const [savingTitle, setSavingTitle] = useState(false);
  const [titleError, setTitleError] = useState("");
  const [draggedId, setDraggedId] = useState(null);
  const [dragOverId, setDragOverId] = useState(null);
  const [editingId, setEditingId] = useState(null);
  const [confirmDeleteId, setConfirmDeleteId] = useState(null);
  const [deletingId, setDeletingId] = useState(null);
  const [deleteError, setDeleteError] = useState(null); // { id, message } | null

  function handleDrop(targetId) {
    setDragOverId(null);
    if (!draggedId || draggedId === targetId) {
      setDraggedId(null);
      return;
    }
    const questions = [...section.questions];
    const fromIndex = questions.findIndex((q) => q.id === draggedId);
    const toIndex = questions.findIndex((q) => q.id === targetId);
    setDraggedId(null);
    if (fromIndex === -1 || toIndex === -1) return;
    const [moved] = questions.splice(fromIndex, 1);
    questions.splice(toIndex, 0, moved);
    onReordered(section.id, questions.map((q) => q.id));
  }

  function startEdit(questionId) {
    setEditingId(questionId);
    setConfirmDeleteId(null);
    setDeleteError(null);
    setFormOpen(false);
    setBulkOpen(false);
  }

  function startDeleteConfirm(questionId) {
    setConfirmDeleteId(questionId);
    setDeleteError(null);
  }

  async function confirmDelete(questionId) {
    setDeletingId(questionId);
    try {
      await Api.del(`/exams/questions/${questionId}`);
      setConfirmDeleteId(null);
      setDeleteError(null);
      onQuestionAdded();
    } catch (err) {
      setDeleteError({ id: questionId, message: err instanceof ApiError ? err.message : "Couldn't delete this question." });
    } finally {
      setDeletingId(null);
    }
  }

  // Dragging a row while it's mid-edit or mid-delete-confirmation would be
  // confusing (its own content is changing underneath the gesture), so
  // reordering is disabled for every row while either is active anywhere in
  // the section.
  const interactionLocked = Boolean(editingId || confirmDeleteId);

  async function saveTitle() {
    const next = titleDraft.trim();
    if (!next) {
      setTitleError("A section needs a name.");
      return;
    }
    if (next === section.title) {
      setRenaming(false);
      return;
    }
    setSavingTitle(true);
    setTitleError("");
    try {
      await Api.patch(`/exams/sections/${section.id}`, { title: next });
      setRenaming(false);
      onQuestionAdded(); // reloads the exam detail -- same refresh the rest of this card uses
    } catch (err) {
      setTitleError(err instanceof ApiError ? err.message : "Couldn't rename this section.");
    } finally {
      setSavingTitle(false);
    }
  }

  return (
    <div className="rounded-2xl border border-border bg-surface shadow-card p-5">
      {/* Renaming is draft-only, matching the backend's own gate
          (exam_service.update_section) rather than relying on the button
          being hidden -- a published exam's section title is part of the
          paper candidates already saw. */}
      {renaming ? (
        <div className="mb-3">
          <div className="flex flex-wrap items-center gap-2">
            <input
              autoFocus
              value={titleDraft}
              maxLength={150}
              onChange={(e) => setTitleDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") saveTitle();
                if (e.key === "Escape") {
                  setRenaming(false);
                  setTitleDraft(section.title);
                  setTitleError("");
                }
              }}
              className={`${fieldInputCompact} max-w-xs`}
            />
            <button type="button" disabled={savingTitle} onClick={saveTitle}
                    className="text-xs font-semibold text-primary hover:underline disabled:opacity-50">
              {savingTitle ? "Saving…" : "Save"}
            </button>
            <button type="button" onClick={() => { setRenaming(false); setTitleDraft(section.title); setTitleError(""); }}
                    className="text-xs font-semibold text-muted hover:text-ink">
              Cancel
            </button>
          </div>
          {titleError && <p className="mt-1.5 text-xs text-danger">{titleError}</p>}
        </div>
      ) : (
        <div className="flex items-center gap-2 mb-3">
          <span className="font-semibold text-ink">{section.title}</span>
          {isDraft && (
            <button type="button" onClick={() => { setRenaming(true); setTitleDraft(section.title); setTitleError(""); }}
                    className="text-xs font-semibold text-muted hover:text-primary transition-colors">
              Rename
            </button>
          )}
        </div>
      )}
      <div className="flex flex-col gap-2 mb-3">
        {section.questions.length ? (
          section.questions.map((q, i) =>
            editingId === q.id ? (
              <div key={q.id} className="rounded-xl border border-primary/40 bg-page/60 px-3 py-3">
                <div className="text-xs font-bold uppercase tracking-wide text-primary mb-1">Editing question {i + 1}</div>
                <QuestionForm
                  sectionId={section.id}
                  question={q}
                  onSaved={() => {
                    setEditingId(null);
                    onQuestionAdded();
                  }}
                  onCancel={() => setEditingId(null)}
                />
              </div>
            ) : (
              <div
                key={q.id}
                draggable={!interactionLocked}
                onDragStart={() => setDraggedId(q.id)}
                onDragOver={(e) => {
                  e.preventDefault();
                  setDragOverId(q.id);
                }}
                onDragLeave={() => setDragOverId((id) => (id === q.id ? null : id))}
                onDrop={(e) => {
                  e.preventDefault();
                  handleDrop(q.id);
                }}
                onDragEnd={() => {
                  setDraggedId(null);
                  setDragOverId(null);
                }}
                className={`flex items-start gap-2 rounded-xl border bg-page/60 px-3 py-2.5 transition-colors ${
                  interactionLocked ? "" : "cursor-grab"
                } ${draggedId === q.id ? "opacity-40" : ""} ${dragOverId === q.id ? "border-primary" : "border-border"}`}
              >
                <Icon name="grip" width={16} height={16} className="text-muted mt-0.5 shrink-0" />
                <div className="flex-1 min-w-0">
                  <div className="flex items-center justify-between gap-2">
                    <strong className="text-sm truncate">
                      {i + 1}. {q.text}
                    </strong>
                    <span
                      className={`shrink-0 inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-semibold ${
                        q.question_type === "coding" ? "bg-success/10 text-success" : "bg-page text-muted border border-border"
                      }`}
                    >
                      {q.question_type === "coding" ? `Coding (${q.language})` : q.question_type === "multi_select" ? "Multiple Selection" : "MCQ"}
                    </span>
                  </div>
                  <div className="text-xs text-muted mt-1">
                    {q.marks} mark{q.marks > 1 ? "s" : ""}
                    {q.question_type === "coding" ? ` · ${q.test_cases.length} test case${q.test_cases.length === 1 ? "" : "s"}` : ""}
                  </div>

                  {confirmDeleteId === q.id ? (
                    <div className="mt-2 rounded-lg border border-danger/30 bg-danger/5 px-2.5 py-2">
                      <div className="flex items-center gap-3">
                        <span className="text-xs text-danger font-medium flex-1">Delete this question? This can't be undone.</span>
                        <button
                          type="button"
                          onClick={() => confirmDelete(q.id)}
                          disabled={deletingId === q.id}
                          className="text-xs font-semibold text-danger hover:underline disabled:opacity-60 shrink-0"
                        >
                          {deletingId === q.id ? "Deleting…" : "Yes, delete"}
                        </button>
                        <button
                          type="button"
                          onClick={() => setConfirmDeleteId(null)}
                          disabled={deletingId === q.id}
                          className="text-xs font-semibold text-muted hover:text-ink disabled:opacity-60 shrink-0"
                        >
                          Cancel
                        </button>
                      </div>
                      {deleteError?.id === q.id && <p className="text-xs text-danger mt-1.5">{deleteError.message}</p>}
                    </div>
                  ) : (
                    <div className="mt-2 flex items-center gap-3">
                      <button type="button" onClick={() => startEdit(q.id)} className="text-xs font-semibold text-primary hover:underline">
                        Edit
                      </button>
                      <button type="button" onClick={() => startDeleteConfirm(q.id)} className="text-xs font-semibold text-danger hover:underline">
                        Delete
                      </button>
                    </div>
                  )}
                </div>
              </div>
            ),
          )
        ) : (
          <EmptyState icon="doc" title="No questions yet" description="Add a question below, or bulk-import a list of MCQs." className="py-6" />
        )}
      </div>
      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          onClick={() => {
            setFormOpen((v) => !v);
            setBulkOpen(false);
            setEditingId(null);
          }}
          className={`${btnGhost.replace("px-5 py-3", "px-3 py-1.5")} text-xs`}
        >
          + Add Question
        </button>
        <button
          type="button"
          onClick={() => {
            setBulkOpen((v) => !v);
            setFormOpen(false);
            setEditingId(null);
          }}
          className={`${btnGhost.replace("px-5 py-3", "px-3 py-1.5")} text-xs`}
        >
          + Bulk Import (MCQ)
        </button>
      </div>
      {formOpen && (
        <QuestionForm
          sectionId={section.id}
          onSaved={() => {
            setFormOpen(false);
            onQuestionAdded();
          }}
          onCancel={() => setFormOpen(false)}
        />
      )}
      {bulkOpen && <BulkImportForm sectionId={section.id} onImported={onQuestionAdded} onCancel={() => setBulkOpen(false)} />}
    </div>
  );
}

function AddSectionForm({ examId, onAdded }) {
  const [title, setTitle] = useState("");
  const [error, setError] = useState("");

  async function handleSubmit(e) {
    e.preventDefault();
    setError("");
    try {
      await Api.post(`/exams/${examId}/sections`, { title: title.trim(), order_index: 0 });
      setTitle("");
      onAdded();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Something went wrong.");
    }
  }

  return (
    <div className="rounded-2xl border border-border bg-surface shadow-card p-5 mb-6">
      <div className="font-semibold text-ink mb-3">Add Section</div>
      {error && (
        <div className="mb-3 flex items-start gap-2.5 rounded-xl border border-danger/30 bg-danger/5 px-4 py-3 text-sm text-danger">
          <Icon name="alert" width={16} height={16} className="mt-0.5 shrink-0" />
          <span>{error}</span>
        </div>
      )}
      <form onSubmit={handleSubmit} className="flex gap-2">
        <input
          required
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          placeholder="Section title, e.g. General Aptitude"
          className={`flex-1 ${fieldInput}`}
        />
        <button type="submit" className={btnPrimary.replace("px-5 py-3", "px-4 py-2")}>
          Add Section
        </button>
      </form>
    </div>
  );
}

/* ===================== Exam builder: monitoring panels ===================== */

function AttemptsPanel({ examId }) {
  const [attempts, setAttempts] = useState(null);
  const [resetTargetId, setResetTargetId] = useState(null); // student_id currently showing the reason form
  const [reason, setReason] = useState("");
  const [resetting, setResetting] = useState(false);
  const [resetError, setResetError] = useState(null); // { studentId, message } | null
  const [viewingStudent, setViewingStudent] = useState(null); // { id, name } | null

  function reload() {
    Api.get(`/attempts/exam/${examId}`)
      .then((data) => setAttempts(data))
      .catch(() => setAttempts([]));
  }

  useEffect(() => {
    let cancelled = false;
    Api.get(`/attempts/exam/${examId}`)
      .then((data) => !cancelled && setAttempts(data))
      .catch(() => !cancelled && setAttempts([]));
    return () => {
      cancelled = true;
    };
  }, [examId]);

  function startReset(studentId) {
    setResetTargetId(studentId);
    setReason("");
    setResetError(null);
  }

  async function confirmReset(studentId) {
    if (reason.trim().length < 3) {
      setResetError({ studentId, message: "Please enter a reason (at least 3 characters)." });
      return;
    }
    setResetting(true);
    setResetError(null);
    try {
      await Api.post(`/attempts/exam/${examId}/student/${studentId}/reset`, { reason: reason.trim() });
      setResetTargetId(null);
      setReason("");
      reload();
    } catch (err) {
      setResetError({ studentId, message: err instanceof ApiError ? err.message : "Couldn't reset this student's exam." });
    } finally {
      setResetting(false);
    }
  }

  return (
    <div className="rounded-2xl border border-border bg-surface shadow-card p-5 mt-6">
      <div className="font-semibold text-ink mb-3">Student Attempts</div>
      <div className="overflow-x-auto">
        <table className="min-w-full text-sm">
          <thead>
            <tr className="text-left text-xs text-muted uppercase tracking-wide border-b border-border">
              <th className="py-2 pr-4 font-semibold">Student</th>
              <th className="py-2 pr-4 font-semibold">Status</th>
              <th className="py-2 pr-4 font-semibold">Score</th>
              <th className="py-2 pr-4 font-semibold">Submitted</th>
              <th className="py-2 pr-4 font-semibold">Actions</th>
            </tr>
          </thead>
          <tbody>
            {!attempts ? (
              <tr><td colSpan={5} className="py-3"><span className="skeleton skeleton-line block" /></td></tr>
            ) : attempts.length ? (
              attempts.map((a) => (
                <tr key={a.attempt_id} className="border-b border-border last:border-0 hover:bg-page/60 transition-colors align-top">
                  <td className="py-2 pr-4 font-medium">{a.student_name}</td>
                  <td className="py-2 pr-4 capitalize">{a.status}</td>
                  <td className="py-2 pr-4 tabular-nums">{a.scored_marks !== null ? `${a.scored_marks}/${a.total_marks}` : "-"}</td>
                  <td className="py-2 pr-4 text-muted">{a.submitted_at ? new Date(a.submitted_at).toLocaleString() : "-"}</td>
                  <td className="py-2 pr-4">
                    <div className="flex items-center gap-3 mb-1.5">
                      <button
                        type="button"
                        onClick={() => setViewingStudent({ id: a.student_id, name: a.student_name })}
                        className="text-xs font-semibold text-primary hover:underline"
                      >
                        View ID
                      </button>
                    </div>
                    {resetTargetId === a.student_id ? (
                      <div className="flex flex-col gap-1.5 min-w-[220px]">
                        <input
                          autoFocus
                          value={reason}
                          onChange={(e) => setReason(e.target.value)}
                          placeholder="Reason (e.g. browser crash)"
                          className={`${fieldInputCompact} text-xs`}
                        />
                        <div className="flex items-center gap-3">
                          <button
                            type="button"
                            onClick={() => confirmReset(a.student_id)}
                            disabled={resetting}
                            className="text-xs font-semibold text-danger hover:underline disabled:opacity-60"
                          >
                            {resetting ? "Resetting…" : "Confirm reset"}
                          </button>
                          <button
                            type="button"
                            onClick={() => setResetTargetId(null)}
                            disabled={resetting}
                            className="text-xs font-semibold text-muted hover:text-ink disabled:opacity-60"
                          >
                            Cancel
                          </button>
                        </div>
                        {resetError?.studentId === a.student_id && <p className="text-xs text-danger">{resetError.message}</p>}
                      </div>
                    ) : (
                      <button type="button" onClick={() => startReset(a.student_id)} className="text-xs font-semibold text-primary hover:underline">
                        Reset
                      </button>
                    )}
                  </td>
                </tr>
              ))
            ) : (
              <tr><td colSpan={5} className="py-6 text-muted text-center">No attempts yet.</td></tr>
            )}
          </tbody>
        </table>
      </div>
      <p className="text-xs text-muted mt-3">
        Resetting clears a student's answers and lets them retake the exam from scratch -- use it for connectivity
        problems, browser crashes, power failures, or other technical interruptions. Every reset is logged with your
        name, the time, and the reason you enter.
      </p>
      <IdentityPhotoModal
        studentId={viewingStudent?.id}
        studentName={viewingStudent?.name}
        onClose={() => setViewingStudent(null)}
      />
    </div>
  );
}

function ActivePanel({ examId }) {
  const [active, setActive] = useState(null);

  useEffect(() => {
    let cancelled = false;
    async function refresh() {
      try {
        const attempts = await Api.get(`/attempts/exam/${examId}`);
        if (!cancelled) setActive(attempts.filter((a) => a.status === "in_progress"));
      } catch {
        if (!cancelled) setActive([]);
      }
    }
    refresh();
    const interval = setInterval(refresh, 10000);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [examId]);

  return (
    <div className="rounded-2xl border border-border bg-surface shadow-card p-5 mt-6">
      <div className="flex items-center justify-between mb-3">
        <div className="font-semibold text-ink">
          Active Now <span className="text-xs font-normal text-muted">(auto-refreshes every 10s)</span>
        </div>
        <span className="inline-flex items-center rounded-full px-2.5 py-1 text-xs font-semibold bg-warning/10 text-warning">
          {active ? active.length : 0} in progress
        </span>
      </div>
      <div className="overflow-x-auto">
        <table className="min-w-full text-sm">
          <thead>
            <tr className="text-left text-xs text-muted uppercase tracking-wide border-b border-border">
              <th className="py-2 pr-4 font-semibold">Student</th>
              <th className="py-2 pr-4 font-semibold">Started</th>
              <th className="py-2 pr-4 font-semibold">Elapsed</th>
            </tr>
          </thead>
          <tbody>
            {!active ? (
              <tr><td colSpan={3} className="py-3"><span className="skeleton skeleton-line block" /></td></tr>
            ) : active.length ? (
              active.map((a, i) => {
                const startedAt = a.started_at ? new Date(a.started_at) : null;
                const elapsedMin = startedAt ? Math.max(0, Math.round((Date.now() - startedAt.getTime()) / 60000)) : null;
                return (
                  <tr key={i} className="border-b border-border last:border-0 hover:bg-page/60 transition-colors">
                    <td className="py-2 pr-4 font-medium">{a.student_name}</td>
                    <td className="py-2 pr-4 text-muted">{startedAt ? startedAt.toLocaleTimeString() : "-"}</td>
                    <td className="py-2 pr-4">{elapsedMin !== null ? `${elapsedMin} min` : "-"}</td>
                  </tr>
                );
              })
            ) : (
              <tr><td colSpan={3} className="py-6 text-muted text-center">No one is taking this exam right now.</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function ViolationsPanel({ examId }) {
  const [events, setEvents] = useState(null);

  useEffect(() => {
    let cancelled = false;
    Api.get(`/proctoring/events/exam/${examId}`)
      .then((data) => !cancelled && setEvents(data))
      .catch(() => !cancelled && setEvents([]));
    return () => {
      cancelled = true;
    };
  }, [examId]);

  return (
    <div className="rounded-2xl border border-border bg-surface shadow-card p-5 mt-6">
      <div className="font-semibold text-ink mb-3">Proctoring Violations</div>
      <div className="overflow-x-auto">
        <table className="min-w-full text-sm">
          <thead>
            <tr className="text-left text-xs text-muted uppercase tracking-wide border-b border-border">
              <th className="py-2 pr-4 font-semibold">Attempt ID</th>
              <th className="py-2 pr-4 font-semibold">Type</th>
              <th className="py-2 pr-4 font-semibold">Severity</th>
              <th className="py-2 pr-4 font-semibold">Time</th>
            </tr>
          </thead>
          <tbody>
            {!events ? (
              <tr><td colSpan={4} className="py-3"><span className="skeleton skeleton-line block" /></td></tr>
            ) : events.length ? (
              events.map((ev) => (
                <tr key={ev.id} className="border-b border-border last:border-0 hover:bg-page/60 transition-colors">
                  <td className="py-2 pr-4">{ev.attempt_id}</td>
                  <td className="py-2 pr-4 capitalize">{ev.event_type.replace(/_/g, " ")}</td>
                  <td className="py-2 pr-4">
                    <span className={`inline-flex items-center rounded-full px-2.5 py-1 text-xs font-semibold capitalize ${SEVERITY_STYLES[ev.severity] || "bg-page text-muted border border-border"}`}>
                      {ev.severity}
                    </span>
                  </td>
                  <td className="py-2 pr-4 text-muted">{new Date(ev.created_at).toLocaleString()}</td>
                </tr>
              ))
            ) : (
              <tr><td colSpan={4} className="py-6 text-muted text-center">No violations recorded.</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function AnalyticsPanel({ examId }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const scoreChartRef = useRef(null);
  const severityChartRef = useRef(null);
  const chartInstances = useRef({});

  useEffect(() => {
    let cancelled = false;
    Api.get(`/analytics/exams/${examId}`)
      .then((d) => !cancelled && setData(d))
      .catch((err) => !cancelled && setError(err instanceof ApiError ? err.message : "Couldn't load analytics."));
    return () => {
      cancelled = true;
    };
  }, [examId]);

  useEffect(() => {
    if (!data || !window.Chart) return;
    const styles = getComputedStyle(document.documentElement);
    const textColor = styles.getPropertyValue("--text-muted").trim() || "#64748b";
    const gridColor = styles.getPropertyValue("--border").trim() || "#e2e8f0";

    Object.values(chartInstances.current).forEach((c) => c?.destroy());

    chartInstances.current.score = new window.Chart(scoreChartRef.current, {
      type: "bar",
      data: {
        labels: data.score_distribution.map((b) => b.range),
        datasets: [{ label: "Students", data: data.score_distribution.map((b) => b.count), backgroundColor: "#2563eb" }],
      },
      options: {
        plugins: { legend: { display: false } },
        scales: {
          x: { ticks: { color: textColor }, grid: { color: gridColor } },
          y: { beginAtZero: true, ticks: { color: textColor, precision: 0 }, grid: { color: gridColor } },
        },
      },
    });

    const severityLabels = Object.keys(data.violations_by_severity);
    const severityCounts = Object.values(data.violations_by_severity);
    chartInstances.current.severity = new window.Chart(severityChartRef.current, {
      type: "doughnut",
      data: { labels: severityLabels.length ? severityLabels : ["No violations yet"], datasets: [{ data: severityCounts.length ? severityCounts : [1], backgroundColor: CHART_PALETTE }] },
      options: { plugins: { legend: { labels: { color: textColor } } } },
    });

    return () => Object.values(chartInstances.current).forEach((c) => c?.destroy());
  }, [data]);

  if (error) {
    return (
      <div className="rounded-2xl border border-danger/30 bg-danger/5 p-5 mt-6 text-sm text-danger">{error}</div>
    );
  }

  const totalViolations = data ? Object.values(data.violations_by_type).reduce((a, b) => a + b, 0) : null;

  return (
    <div className="mt-6">
      <div className="grid md:grid-cols-2 gap-4">
        <div className="rounded-2xl border border-border bg-surface shadow-card p-4">
          <div className="font-semibold text-ink mb-3">Score Distribution</div>
          <canvas ref={scoreChartRef} height="220" />
        </div>
        <div className="rounded-2xl border border-border bg-surface shadow-card p-4">
          <div className="font-semibold text-ink mb-3">Violations by Severity</div>
          <canvas ref={severityChartRef} height="220" />
        </div>
      </div>
      <div className="rounded-2xl border border-border bg-surface shadow-card p-4 mt-4">
        <div className="grid grid-cols-3 gap-4 text-center">
          <div>
            <div className="text-2xl font-extrabold text-primary tabular-nums">{data ? data.total_attempts : "-"}</div>
            <div className="text-xs text-muted mt-1">Total Attempts</div>
          </div>
          <div>
            <div className="text-2xl font-extrabold text-primary tabular-nums">{data ? (data.average_percentage !== null ? `${data.average_percentage}%` : "-") : "-"}</div>
            <div className="text-xs text-muted mt-1">Average Score</div>
          </div>
          <div>
            <div className="text-2xl font-extrabold text-danger tabular-nums">{totalViolations !== null ? totalViolations : "-"}</div>
            <div className="text-xs text-muted mt-1">Total Violations</div>
          </div>
        </div>
      </div>
    </div>
  );
}

/* ===================== Exam schedule editing ===================== */

// Formats an ISO datetime string as the local-time value a <input
// type="datetime-local"> expects ("YYYY-MM-DDTHH:mm") -- the inverse of the
// `new Date(value).toISOString()` conversion CreateExamForm uses going the
// other way. Needed here (and not there) because this form, unlike create,
// has to pre-fill from an existing value.
function toDatetimeLocalValue(isoString) {
  if (!isoString) return "";
  const d = new Date(isoString);
  if (Number.isNaN(d.getTime())) return "";
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

// Mirrors exam_service.has_exam_started -- a client-side estimate used only
// to decide which fields to show as editable. The server re-checks the same
// rule authoritatively on every PATCH, so a stale/wrong guess here (e.g. the
// device clock is off) fails safely as a rejected save with a clear message,
// never as a silently-accepted change it shouldn't have allowed.
function hasExamStarted(exam) {
  if (!exam || exam.status !== "published") return false;
  if (!exam.start_time) return true;
  return new Date() >= new Date(exam.start_time);
}

function ExamScheduleCard({ exam, onUpdated }) {
  const [startValue, setStartValue] = useState(() => toDatetimeLocalValue(exam.start_time));
  const [endValue, setEndValue] = useState(() => toDatetimeLocalValue(exam.end_time));
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  // Re-sync local field state when the exam changes underneath us -- after
  // a successful save reloads the exam, or when switching between exams.
  useEffect(() => {
    setStartValue(toDatetimeLocalValue(exam.start_time));
    setEndValue(toDatetimeLocalValue(exam.end_time));
    setError("");
    setSaved(false);
  }, [exam.id, exam.start_time, exam.end_time]);

  const started = hasExamStarted(exam);
  const closed = exam.status === "closed";

  async function handleSubmit(e) {
    e.preventDefault();
    setError("");
    setSaved(false);

    const startDate = startValue ? new Date(startValue) : null;
    const endDate = endValue ? new Date(endValue) : null;
    const now = new Date();

    if (!started && startDate && startDate < now) {
      setError("Start date and time cannot be earlier than the current date and time.");
      return;
    }
    if (endDate) {
      const effectiveStart = startDate || now;
      if (endDate <= effectiveStart) {
        setError("End date and time must be after the start date and time.");
        return;
      }
      if (endDate < now) {
        setError("End date and time cannot be earlier than the current date and time.");
        return;
      }
    }

    setSaving(true);
    try {
      await Api.patch(`/exams/${exam.id}/schedule`, {
        start_time: startDate ? startDate.toISOString() : null,
        end_time: endDate ? endDate.toISOString() : null,
      });
      setSaved(true);
      await onUpdated();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't update the schedule.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="rounded-2xl border border-border bg-surface shadow-card p-5 mb-6">
      <div className="font-semibold text-ink mb-1">Exam Schedule</div>
      <p className="text-xs text-muted mb-4">
        {closed
          ? "This exam is closed, so its schedule can no longer be changed."
          : started
            ? "This exam has already started, so its start date/time is locked -- candidates may already be taking it. You can still adjust the end date/time, e.g. to grant extra time after a technical issue."
            : exam.status === "draft"
              ? "Leave a field blank for no fixed start/end -- students can begin the moment the exam is published."
              : "The exam hasn't started yet, so both the start and end date/time can still be changed."}
      </p>
      <form onSubmit={handleSubmit} className="grid sm:grid-cols-2 gap-4">
        <div>
          <label className={fieldLabelCompact}>Start date &amp; time</label>
          <input
            type="datetime-local"
            value={startValue}
            disabled={closed || started}
            onChange={(e) => setStartValue(e.target.value)}
            className={`${fieldInputCompact} ${closed || started ? "opacity-60 cursor-not-allowed" : ""}`}
          />
        </div>
        <div>
          <label className={fieldLabelCompact}>End date &amp; time</label>
          <input
            type="datetime-local"
            value={endValue}
            disabled={closed}
            onChange={(e) => setEndValue(e.target.value)}
            className={`${fieldInputCompact} ${closed ? "opacity-60 cursor-not-allowed" : ""}`}
          />
        </div>
        {error && (
          <div className="sm:col-span-2 flex items-start gap-2.5 rounded-xl border border-danger/30 bg-danger/5 px-3 py-2.5 text-sm text-danger">
            <Icon name="alert" width={15} height={15} className="mt-0.5 shrink-0" />
            <span>{error}</span>
          </div>
        )}
        {!closed && (
          <div className="sm:col-span-2 flex items-center gap-3">
            <button type="submit" disabled={saving} className={`${btnPrimary.replace("px-5 py-3", "px-4 py-2")} text-sm ${saving ? "opacity-70 pointer-events-none" : ""}`}>
              {saving ? "Saving…" : "Save Schedule"}
            </button>
            {saved && !saving && <span className="text-xs text-success font-medium">Schedule updated.</span>}
          </div>
        )}
      </form>
    </div>
  );
}

/* ===================== Exam details editing (draft-only) ===================== */

// Everything about an exam except its schedule (which has its own card
// above, with its own post-publish rules) and its questions (their own
// tab) -- title, description, candidate instructions, duration, passing
// percentage, and the three randomize/proctoring toggles. Draft-only: once
// published the backend freezes every one of these (see
// exam_service.update_exam_details), so this renders a read-only summary
// instead of a form for a published/closed exam.
function ExamDetailsForm({ exam, onUpdated }) {
  const isDraft = exam.status === "draft";

  const fromExam = useCallback(() => ({
    title: exam.title || "",
    description: exam.description || "",
    instructions: exam.instructions || "",
    duration: String(exam.duration_minutes ?? ""),
    passPercentage: String(exam.pass_percentage ?? 40),
    randomizeQuestions: exam.randomize_questions,
    randomizeOptions: exam.randomize_options,
    proctoring: exam.proctoring_enabled,
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }), [exam.id, exam.title, exam.description, exam.instructions, exam.duration_minutes, exam.pass_percentage, exam.randomize_questions, exam.randomize_options, exam.proctoring_enabled]);

  const [form, setForm] = useState(fromExam);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    setForm(fromExam());
    setError("");
    setSaved(false);
  }, [fromExam]);

  function update(field, value) {
    setForm((f) => ({ ...f, [field]: value }));
  }

  async function handleSubmit(e) {
    e.preventDefault();
    setError("");
    setSaved(false);
    setSaving(true);
    try {
      await Api.put(`/exams/${exam.id}`, {
        title: form.title.trim(),
        description: form.description.trim() || null,
        instructions: form.instructions.trim() || null,
        duration_minutes: parseInt(form.duration, 10),
        pass_percentage: parseInt(form.passPercentage, 10) || 40,
        randomize_questions: form.randomizeQuestions,
        randomize_options: form.randomizeOptions,
        proctoring_enabled: form.proctoring,
      });
      setSaved(true);
      await onUpdated();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't save exam details.");
    } finally {
      setSaving(false);
    }
  }

  if (!isDraft) {
    return (
      <div className="rounded-2xl border border-border bg-surface shadow-card p-5">
        <div className="font-semibold text-ink mb-1">Exam Details</div>
        <p className="text-xs text-muted mb-4">
          These settings are locked now that the exam is published, since changing them could affect candidates
          already taking it. The schedule above is the one exception that can still be adjusted.
        </p>
        <dl className="grid sm:grid-cols-2 gap-x-6 gap-y-3 text-sm">
          <div>
            <dt className="text-xs text-muted">Title</dt>
            <dd className="font-medium text-ink">{exam.title}</dd>
          </div>
          <div>
            <dt className="text-xs text-muted">Duration</dt>
            <dd className="font-medium text-ink">{exam.duration_minutes} minutes</dd>
          </div>
          <div>
            <dt className="text-xs text-muted">Passing percentage</dt>
            <dd className="font-medium text-ink">{exam.pass_percentage}%</dd>
          </div>
          <div>
            <dt className="text-xs text-muted">AI proctoring</dt>
            <dd className="font-medium text-ink">{exam.proctoring_enabled ? "Enabled" : "Disabled"}</dd>
          </div>
          <div>
            <dt className="text-xs text-muted">Randomize question order</dt>
            <dd className="font-medium text-ink">{exam.randomize_questions ? "Yes" : "No"}</dd>
          </div>
          <div>
            <dt className="text-xs text-muted">Randomize answer choices</dt>
            <dd className="font-medium text-ink">{exam.randomize_options ? "Yes" : "No"}</dd>
          </div>
          {exam.description && (
            <div className="sm:col-span-2">
              <dt className="text-xs text-muted">Description</dt>
              <dd className="text-ink whitespace-pre-wrap">{exam.description}</dd>
            </div>
          )}
          {exam.instructions && (
            <div className="sm:col-span-2">
              <dt className="text-xs text-muted">Candidate instructions</dt>
              <dd className="text-ink whitespace-pre-wrap">{exam.instructions}</dd>
            </div>
          )}
        </dl>
      </div>
    );
  }

  return (
    <div className="rounded-2xl border border-border bg-surface shadow-card p-5">
      <div className="font-semibold text-ink mb-1">Exam Details</div>
      <p className="text-xs text-muted mb-4">
        Editable while this exam is a draft. You don't have to finish everything now -- come back and adjust as
        many times as you like, and publish only once it's ready.
      </p>
      <form onSubmit={handleSubmit} className="flex flex-col gap-4">
        {error && (
          <div className="flex items-start gap-2.5 rounded-xl border border-danger/30 bg-danger/5 px-4 py-3 text-sm text-danger">
            <Icon name="alert" width={16} height={16} className="mt-0.5 shrink-0" />
            <span>{error}</span>
          </div>
        )}
        <div className="grid sm:grid-cols-2 gap-4">
          <div>
            <label className={fieldLabel}>Title</label>
            <input required value={form.title} onChange={(e) => update("title", e.target.value)} className={fieldInput} />
          </div>
          <div>
            <label className={fieldLabel}>Duration (minutes)</label>
            <input required type="number" min="1" value={form.duration} onChange={(e) => update("duration", e.target.value)} className={fieldInput} />
          </div>
        </div>
        <div>
          <label className={fieldLabel}>Passing Percentage</label>
          <input required type="number" min="0" max="100" value={form.passPercentage} onChange={(e) => update("passPercentage", e.target.value)} className={fieldInput} />
        </div>
        <div>
          <label className={fieldLabel}>Description</label>
          <textarea rows={2} value={form.description} onChange={(e) => update("description", e.target.value)} className={fieldInput} placeholder="A short summary shown on the exam card." />
        </div>
        <div>
          <label className={fieldLabel}>Instructions (optional -- shown to candidates before they begin)</label>
          <textarea rows={3} value={form.instructions} onChange={(e) => update("instructions", e.target.value)} className={fieldInput} placeholder="Any exam-specific guidance beyond the standard rules..." />
        </div>
        <div className="grid sm:grid-cols-3 gap-4">
          <label className="flex items-center gap-2 text-sm text-ink">
            <input type="checkbox" checked={form.randomizeQuestions} onChange={(e) => update("randomizeQuestions", e.target.checked)} className="w-4 h-4 accent-primary" />
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
        <div className="flex items-center gap-3">
          <button type="submit" disabled={saving} className={`${btnPrimary.replace("px-5 py-3", "px-4 py-2")} text-sm self-start ${saving ? "opacity-70 pointer-events-none" : ""}`}>
            {saving ? "Saving…" : "Save Details"}
          </button>
          {saved && !saving && <span className="text-xs text-success font-medium">Saved.</span>}
        </div>
      </form>
    </div>
  );
}

/* ===================== Exam builder view ===================== */

function ExamBuilderView({ examId, onBack }) {
  const [exam, setExam] = useState(null);
  const [loadError, setLoadError] = useState("");
  const [tab, setTab] = useState("questions");
  const [publishState, setPublishState] = useState(null); // { tone, message }
  const [publishing, setPublishing] = useState(false);

  async function loadExamDetail() {
    try {
      setLoadError("");
      setExam(await Api.get(`/exams/${examId}`));
    } catch (err) {
      setLoadError(err instanceof ApiError ? err.message : "Couldn't load this exam.");
    }
  }

  useEffect(() => {
    setExam(null);
    setTab("questions");
    loadExamDetail();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [examId]);

  async function handleReorder(sectionId, orderedIds) {
    setExam((prev) => {
      if (!prev) return prev;
      const section = prev.sections.find((s) => s.id === sectionId);
      if (!section) return prev;
      const byId = new Map(section.questions.map((q) => [q.id, q]));
      section.questions = orderedIds.map((id) => byId.get(id));
      return { ...prev, sections: [...prev.sections] };
    });
    try {
      await Api.put(`/exams/sections/${sectionId}/questions/reorder`, { question_ids: orderedIds });
    } catch {
      await loadExamDetail();
    }
  }

  async function handlePublish() {
    setPublishing(true);
    setPublishState(null);
    try {
      await Api.post(`/exams/${examId}/publish`);
      setPublishState({ tone: "success", message: "Exam published! Students can now see it." });
      await loadExamDetail();
    } catch (err) {
      setPublishState({ tone: "error", message: err instanceof ApiError ? err.message : "Something went wrong." });
    } finally {
      setPublishing(false);
    }
  }

  const TABS = [
    { id: "questions", label: "Questions" },
    { id: "overview", label: "Overview" },
    { id: "access", label: "Who Can Take This" },
    { id: "attempts", label: "Attempts" },
    { id: "active", label: "Active Now" },
    { id: "violations", label: "Violations" },
    { id: "analytics", label: "Analytics" },
  ];

  return (
    <main className="max-w-6xl mx-auto w-full p-4 sm:p-6">
      {/* Fixed chrome: back link, exam title/publish, and the tab strip all
          stick to the viewport top once scrolled past. Everything tab-specific
          (including the question list, which can run into the thousands) lives
          below, in the single content panel, so switching tabs never requires
          scrolling past unrelated content -- this is the fix for the old
          layout, where the panels were appended below the entire question
          list. */}
      <div className="sticky top-0 z-10 bg-page pb-3">
        <button type="button" onClick={onBack} className="pt-4 sm:pt-6 text-sm font-semibold text-muted hover:text-ink mb-4 inline-flex items-center gap-1">
          <Icon name="chevron-left" width={16} height={16} /> Back to exams
        </button>

        {loadError && (
          <div className="mb-6 flex items-start gap-2.5 rounded-xl border border-danger/30 bg-danger/5 px-4 py-3 text-sm text-danger">
            <Icon name="alert" width={16} height={16} className="mt-0.5 shrink-0" />
            <span>{loadError}</span>
          </div>
        )}

        <div className="rounded-2xl border border-border bg-surface shadow-card p-5">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="font-bold text-lg text-ink">
              {exam ? `${exam.title} (${exam.status})` : <span className="skeleton skeleton-text inline-block" style={{ width: "12ch" }}>-</span>}
            </div>
            <button
              type="button"
              onClick={handlePublish}
              disabled={!exam || exam.status !== "draft" || publishing}
              className={`${btnPrimary.replace("px-5 py-3", "px-3 py-1.5")} text-xs ${!exam || exam.status !== "draft" ? "opacity-60 pointer-events-none" : ""}`}
            >
              {exam && exam.status !== "draft" ? "Published" : publishing ? "Publishing…" : "Publish Exam"}
            </button>
          </div>
          {publishState && (
            <p className={`mt-4 text-sm ${publishState.tone === "success" ? "text-success" : "text-danger"}`}>{publishState.message}</p>
          )}

          <div className="mt-4 -mb-5 border-t border-border">
            <div className="flex gap-1 overflow-x-auto">
              {TABS.map((t) => (
                <button
                  key={t.id}
                  type="button"
                  onClick={() => setTab(t.id)}
                  className={`whitespace-nowrap px-3 py-2.5 text-sm font-semibold border-b-2 -mt-px transition-colors ${
                    tab === t.id ? "border-primary text-primary" : "border-transparent text-muted hover:text-ink hover:border-border"
                  }`}
                >
                  {t.label}
                </button>
              ))}
            </div>
          </div>
        </div>
      </div>

      <div className="pt-5">
        {!exam ? (
          <div className="flex flex-col gap-5">
            <div className="skeleton skeleton-card" style={{ height: 140 }} />
            <div className="skeleton skeleton-card" style={{ height: 140 }} />
          </div>
        ) : (
          <>
            {tab === "questions" && (
              <div className="flex flex-col gap-5">
                <AddSectionForm examId={exam.id} onAdded={loadExamDetail} />
                {exam.sections.length ? (
                  exam.sections.map((section) => (
                    <SectionCard key={section.id} section={section} isDraft={exam.status === "draft"} onQuestionAdded={loadExamDetail} onReordered={handleReorder} />
                  ))
                ) : (
                  <EmptyState icon="layout" title="No sections yet" description="Add a section above to start building your exam's structure." />
                )}
              </div>
            )}
            {tab === "overview" && (
              <div className="flex flex-col gap-5">
                <ExamScheduleCard exam={exam} onUpdated={loadExamDetail} />
                <ExamDetailsForm exam={exam} onUpdated={loadExamDetail} />
              </div>
            )}
            {tab === "access" && <ExamAccessPanel examId={exam.id} />}
            {tab === "attempts" && <AttemptsPanel examId={exam.id} />}
            {tab === "active" && <ActivePanel examId={exam.id} />}
            {tab === "violations" && <ViolationsPanel examId={exam.id} />}
            {tab === "analytics" && <AnalyticsPanel examId={exam.id} />}
          </>
        )}
      </div>
    </main>
  );
}
/* ===================== Per-exam access ===================== */

/**
 * Who can take this specific exam.
 *
 * Two modes, and the default matters: with nobody added the exam is open to
 * the whole organization, which is what most exams want. Adding even one
 * email flips it to an allow-list. Expressing that as "everyone unless you
 * add someone" rather than a separate toggle keeps the common case to zero
 * clicks and makes the restricted case impossible to enable by accident.
 *
 * Entry is by email, not by picking from registered students. An examiner
 * sets an exam up before the cohort has signed up, so a picker that could
 * only show existing accounts was useless for exactly the case it was for.
 */
function ExamAccessPanel({ examId }) {
  const [access, setAccess] = useState(null);
  const [emails, setEmails] = useState("");
  const [saving, setSaving] = useState(false);
  const [feedback, setFeedback] = useState(null);
  const [loadError, setLoadError] = useState("");
  const [busyIds, setBusyIds] = useState(() => new Set());

  const load = useCallback(async () => {
    setLoadError("");
    try {
      setAccess(await Api.get(`/organizations/exams/${examId}/access`));
    } catch (err) {
      setAccess({ participants: [], restricted: false, organization_name: null });
      setLoadError(err instanceof ApiError ? err.message : "Couldn't load exam access.");
    }
  }, [examId]);

  useEffect(() => {
    load();
  }, [load]);

  async function handleAdd(event) {
    event.preventDefault();
    if (!emails.trim()) return;
    setSaving(true);
    setFeedback(null);
    try {
      const result = await Api.post(`/organizations/exams/${examId}/participants`, { emails });
      const parts = [];
      if (result.added.length) parts.push(`${result.added.length} added`);
      if (result.already_present.length) parts.push(`${result.already_present.length} already on the list`);
      const invalid = result.invalid || [];
      if (invalid.length) {
        parts.push(`${invalid.length} skipped (not an email): ${invalid.slice(0, 3).join(", ")}${invalid.length > 3 ? "…" : ""}`);
      }
      setFeedback({
        tone: invalid.length ? "warn" : result.added.length ? "success" : "muted",
        message: parts.join(" · ") || "Nothing to add.",
      });
      if (!invalid.length) setEmails("");
      await load();
    } catch (err) {
      setFeedback({ tone: "error", message: err instanceof ApiError ? err.message : "Couldn't add those students." });
    } finally {
      setSaving(false);
    }
  }

  async function handleRemove(participant) {
    setBusyIds((prev) => new Set(prev).add(participant.id));
    try {
      await Api.del(`/organizations/exams/${examId}/participants/${participant.id}`);
      await load();
    } catch (err) {
      setFeedback({ tone: "error", message: err instanceof ApiError ? err.message : "Couldn't remove that entry." });
    } finally {
      setBusyIds((prev) => {
        const next = new Set(prev);
        next.delete(participant.id);
        return next;
      });
    }
  }

  async function handleClear() {
    setSaving(true);
    setFeedback(null);
    try {
      await Api.del(`/organizations/exams/${examId}/participants`);
      setFeedback({ tone: "success", message: "Reopened to everyone in your organization." });
      await load();
    } catch (err) {
      setFeedback({ tone: "error", message: err instanceof ApiError ? err.message : "Couldn't reopen the exam." });
    } finally {
      setSaving(false);
    }
  }

  const participants = access?.participants || [];

  return (
    <section className="mt-6 rounded-2xl border border-border bg-surface shadow-card p-5">
      <h3 className="font-bold text-ink mb-1">Who can take this exam</h3>
      <p className="text-sm text-muted">
        {participants.length === 0 ? (
          <>Everyone enrolled in <strong className="text-ink">{access?.organization_name || "your organization"}</strong> can take this exam. Add emails below to restrict it.</>
        ) : (
          <>Only the <strong className="text-ink">{participants.length}</strong> listed below can see or start this exam.</>
        )}
      </p>

      {loadError && (
        <div className="mt-4 flex items-start gap-2.5 rounded-xl border border-danger/30 bg-danger/5 px-4 py-3 text-sm text-danger">
          <Icon name="alert" width={16} height={16} className="mt-0.5 shrink-0" />
          <span>{loadError}</span>
        </div>
      )}

      <form onSubmit={handleAdd} className="mt-4">
        <label className={fieldLabelCompact} htmlFor={`exam-emails-${examId}`}>
          Add students to this exam by email
        </label>
        <textarea
          id={`exam-emails-${examId}`}
          value={emails}
          onChange={(e) => setEmails(e.target.value)}
          rows={3}
          placeholder={"anna@college.edu, ben@college.edu\nOr paste a column straight from a spreadsheet"}
          className="w-full rounded-xl border border-border bg-page px-4 py-3 text-sm text-ink placeholder:text-muted/70 focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20 transition"
        />
        <div className="mt-3 flex flex-wrap items-center gap-3">
          <button type="submit" disabled={saving || !emails.trim()}
                  className={`${btnPrimary.replace("px-5 py-3", "px-4 py-2")} text-sm disabled:opacity-60`}>
            {saving ? "Adding…" : "Add to this exam"}
          </button>
          {participants.length > 0 && (
            <button type="button" onClick={handleClear} disabled={saving}
                    className={`${btnGhost.replace("px-5 py-3", "px-4 py-2")} text-sm disabled:opacity-60`}>
              Remove all (open to everyone)
            </button>
          )}
        </div>
        <p className="mt-2 text-xs text-muted">
          Students who haven't registered yet can be added now — they're linked automatically when they sign up.
        </p>
        {feedback && (
          <p className={`mt-3 text-sm ${
            feedback.tone === "error" ? "text-danger"
              : feedback.tone === "warn" ? "text-amber-600 dark:text-amber-400"
              : feedback.tone === "success" ? "text-success" : "text-muted"
          }`}>
            {feedback.message}
          </p>
        )}
      </form>

      {access === null ? (
        <div className="mt-5 flex flex-col gap-2">
          <div className="skeleton skeleton-text" />
          <div className="skeleton skeleton-text" />
        </div>
      ) : participants.length > 0 ? (
        <ul className="mt-5 border-t border-border pt-4 max-h-72 overflow-y-auto flex flex-col divide-y divide-border">
          {participants.map((p) => (
            <li key={p.id} className="flex items-center justify-between gap-3 py-2.5">
              <div className="min-w-0">
                <div className="text-sm font-medium text-ink truncate">{p.student_name || p.email}</div>
                {p.student_name && <div className="text-xs text-muted truncate">{p.email}</div>}
              </div>
              <div className="flex items-center gap-3 shrink-0">
                <span className={`text-[11px] font-semibold px-2 py-0.5 rounded-full ${
                  p.student_id
                    ? "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400"
                    : "bg-amber-500/10 text-amber-600 dark:text-amber-400"
                }`}>
                  {p.student_id ? "Registered" : "Invited"}
                </span>
                <button type="button" onClick={() => handleRemove(p)} disabled={busyIds.has(p.id)}
                        title="Remove from this exam"
                        className="text-muted hover:text-danger transition-colors disabled:opacity-50">
                  <Icon name="x" width={15} height={15} />
                </button>
              </div>
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}

/* ===================== Top level ===================== */

export default function ExaminerDashboard() {
  const [currentExamId, setCurrentExamId] = useState(null);

  return (
    <div className="min-h-screen flex flex-col bg-page text-ink">
      <DashboardHeader title="Examiner Portal" />
      {currentExamId === null ? (
        <ExamsListView onManage={setCurrentExamId} />
      ) : (
        <ExamBuilderView examId={currentExamId} onBack={() => setCurrentExamId(null)} />
      )}
    </div>
  );
}
