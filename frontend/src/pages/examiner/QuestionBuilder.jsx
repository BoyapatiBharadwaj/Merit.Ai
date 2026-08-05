/* Split out of ExaminerDashboard.jsx -- see examiner/shared.jsx for why. */
import { useEffect, useRef, useState } from "react";
import Icon from "../../components/Icon.jsx";
import EmptyState from "../../components/EmptyState.jsx";
import { Api, ApiError } from "../../lib/api.js";
import { btnPrimary, btnGhost, fieldInput, fieldLabel, fieldInputCompact, fieldLabelCompact } from "../../lib/ui.js";
import {
  MIN_OPTIONS, emptyOptions, emptyTestCases, optionsFromExisting, testCasesFromExisting,
} from "./shared.jsx";

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

  /**
   * Change the question type, reconciling the options to the new type's rules.
   *
   * Plain setType left the options exactly as they were, which produced two
   * questions the server refuses and the form gives no hint about:
   *
   *   * multi-select -> MCQ kept every correct option marked, so a question
   *     with three correct answers became a single-answer question with three,
   *     and Save failed with "Mark exactly one option as correct" over a change
   *     the examiner did not make.
   *   * coding -> MCQ produced a fresh, blank set of options with nothing
   *     marked correct at all.
   *
   * Reconciling here means the form is always in a state that can be saved.
   * Keeping the FIRST correct option (rather than clearing them all) preserves
   * the examiner's most likely intent; they can move it in one click.
   */
  function changeType(nextType) {
    setType(nextType);
    setError("");

    if (nextType === "mcq") {
      setOptions((opts) => {
        const rows = opts.length >= MIN_OPTIONS ? opts : emptyOptions();
        const firstCorrect = rows.findIndex((o) => o.isCorrect);
        const keep = firstCorrect === -1 ? 0 : firstCorrect;
        return rows.map((o, idx) => ({ ...o, isCorrect: idx === keep }));
      });
    } else if (nextType === "multi_select") {
      setOptions((opts) => {
        const rows = opts.length >= MIN_OPTIONS ? opts : emptyOptions();
        // At least one correct is required; if the previous type left none,
        // mark the first rather than letting Save fail on it.
        return rows.some((o) => o.isCorrect)
          ? rows
          : rows.map((o, idx) => ({ ...o, isCorrect: idx === 0 }));
      });
    } else {
      // Coding needs at least one test case AND at least one sample among them.
      setTestCases((cases) => (cases.length ? cases : emptyTestCases()));
    }
  }

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
    // A coding question needs at least one test case AND at least one of them
    // visible to the candidate. Both could be deleted here, leaving a form that
    // looked fine and was guaranteed to be rejected on Save -- so the examiner
    // discovered the rule only by hitting it. Refusing the last of each keeps
    // the form in a saveable state, matching the option rows above.
    setTestCases((rows) => {
      if (rows.length <= 1) return rows;
      const next = rows.filter((_, idx) => idx !== i);
      return next.some((row) => row.is_sample)
        ? next
        : next.map((row, idx) => (idx === 0 ? { ...row, is_sample: true } : row));
    });
  }
  function setSampleFlag(i, checked) {
    setTestCases((rows) => {
      const next = rows.map((r, idx) => (idx === i ? { ...r, is_sample: checked } : r));
      // Unticking the only sample would guarantee a rejected save.
      return next.some((row) => row.is_sample) ? next : rows;
    });
  }

  async function handleSubmit(e) {
    e.preventDefault();
    setError("");
    setSaving(true);
    try {
      // null on create means "append", which the server resolves. Sending 0 --
      // which every create used to do -- gave every question in a section the
      // same order_index, so their display order was whatever the database
      // returned. An edit keeps the position it already had.
      let payload = {
        text: text.trim(), marks: parseInt(marks, 10) || 1,
        order_index: question ? question.order_index : null,
        question_type: type, explanation: explanation.trim() || null,
      };
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
          <select value={type} onChange={(e) => changeType(e.target.value)} className={inputCls}>
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
                      <input type="checkbox" checked={row.is_sample} onChange={(e) => setSampleFlag(i, e.target.checked)} className="accent-primary" />
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
/**
 * Parses one bulk-import row.
 *
 *   Question | Opt1 | Opt2 | ... | Correct | Marks | Explanation
 *
 * `Correct` accepts either a single option number (`2`) or several separated by
 * commas or spaces (`2,4` / `2 4`). One correct answer produces an `mcq`; two or
 * more produce a `multi_select`, so the question type is inferred from the data
 * rather than needing its own column — an examiner pasting a mixed list should
 * not have to declare the type twice per row.
 *
 * Returns `{ question }` on success or `{ error }` describing what was wrong,
 * instead of a bare null. The old version returned null for every failure, so a
 * hundred-row paste with one typo reported "some rows had a bad format" and left
 * the examiner to find it by eye.
 */
function parseBulkLine(line) {
  const parts = line.split("|").map((p) => p.trim());
  if (parts.length < 6) {
    return { error: "needs at least: question, 2 options, correct, marks, explanation (keep every pipe)" };
  }

  const text = parts[0];
  const explanation = parts[parts.length - 1];
  const marksStr = parts[parts.length - 2];
  const correctStr = parts[parts.length - 3];
  const optionTexts = parts.slice(1, parts.length - 3).filter(Boolean);

  if (!text) return { error: "the question text is empty" };
  if (optionTexts.length < 2) return { error: `only ${optionTexts.length} option(s) — at least 2 are needed` };

  // Split on commas and/or whitespace so "2,4", "2, 4" and "2 4" all work --
  // the difference between them is invisible in a spreadsheet paste.
  const correctNumbers = correctStr.split(/[,\s]+/).filter(Boolean).map((n) => parseInt(n, 10));
  if (!correctNumbers.length || correctNumbers.some(Number.isNaN)) {
    return { error: `"${correctStr}" is not an option number (use e.g. 2, or 2,4 for multiple)` };
  }

  const correctIdx = [...new Set(correctNumbers.map((n) => n - 1))];
  const outOfRange = correctIdx.filter((i) => i < 0 || i >= optionTexts.length);
  if (outOfRange.length) {
    return { error: `correct answer ${outOfRange.map((i) => i + 1).join(", ")} is outside the ${optionTexts.length} options given` };
  }
  // Every option correct is almost certainly a miscount rather than intent, and
  // it would grade as "select all" — worth refusing rather than silently importing.
  if (correctIdx.length === optionTexts.length) {
    return { error: "every option is marked correct — check the numbering" };
  }

  const options = optionTexts.map((t, i) => ({ text: t, is_correct: correctIdx.includes(i) }));

  return {
    question: {
      text,
      marks: parseInt(marksStr, 10) || 1,
      order_index: 0,
      // The whole point of this change: more than one correct answer is a
      // different question type, graded as an exact set match server-side (see
      // attempt_service._grade_multi_select_answer).
      question_type: correctIdx.length > 1 ? "multi_select" : "mcq",
      options,
      explanation: explanation || null,
    },
  };
}

function BulkImportForm({ sectionId, onImported, onCancel }) {
  const [text, setText] = useState("");
  const [status, setStatus] = useState("");
  const [importing, setImporting] = useState(false);

  async function handleImport() {
    setStatus("");
    const lines = text.split("\n").map((l) => l.trim()).filter(Boolean);

    // Line numbers are tracked so a rejection can name the row it came from.
    // Telling someone "3 rows were invalid" in a 100-row paste is barely more
    // useful than saying nothing.
    const results = lines.map((line, i) => ({ lineNo: i + 1, ...parseBulkLine(line) }));
    const badRows = results.filter((r) => r.error);
    const valid = results.filter((r) => r.question);

    if (!valid.length) {
      setStatus(
        "No valid rows found. Format: Question | Option 1 | Option 2 | ... | Correct | Marks | Explanation\n" +
        (badRows.length ? `Line ${badRows[0].lineNo}: ${badRows[0].error}` : "")
      );
      return;
    }

    setImporting(true);

    // ONE request for the whole import, not one per question.
    //
    // The loop this replaces sent a separate POST per row and counted the
    // successes. Any failure part-way -- a dropped connection at question 40 of
    // 60, one row the server disliked -- left the exam holding whatever had
    // already landed, with the remainder silently absent. The examiner's only
    // recovery was to work out by eye where it stopped, or delete everything and
    // start again. A paper quietly missing its last twenty questions is not
    // something anyone notices until candidates are sitting it.
    //
    // The server now validates every row before writing any, so this is
    // all-or-nothing and reports every problem at once rather than one per
    // attempt.
    let response;
    try {
      response = await Api.post(`/exams/sections/${sectionId}/questions/bulk`, {
        questions: valid.map((row) => row.question),
      });
    } catch (err) {
      setImporting(false);
      const detail = err instanceof ApiError ? err.detail : null;
      const problems = detail && typeof detail === "object" ? detail.problems || [] : [];
      const shown = problems.slice(0, 8);
      const extra = problems.length - shown.length;
      setStatus(
        (detail?.message || err?.message || "The import was rejected.") +
        "\nNothing was saved — fix these and paste again:\n" +
        [...badRows.slice(0, 5).map((r) => `Line ${r.lineNo}: ${r.error}`), ...shown].join("\n") +
        (extra > 0 ? `\n…and ${extra} more` : "")
      );
      return;
    }
    setImporting(false);
    onImported();

    const multiCount = valid.filter((r) => r.question.question_type === "multi_select").length;
    if (badRows.length) {
      const details = badRows.slice(0, 5).map((r) => `Line ${r.lineNo}: ${r.error}`);
      const extra = badRows.length - details.length;
      setStatus(
        `Imported ${response.imported} question(s)${multiCount ? ` (${multiCount} multi-answer)` : ""}. ` +
        `${badRows.length} unreadable line(s) skipped:\n` +
        details.join("\n") + (extra > 0 ? `\n…and ${extra} more` : "")
      );
    } else {
      setText("");
      setStatus("");
    }
  }

  return (
    <div className="mt-4 border-t border-border pt-4 flex flex-col gap-3">
      <label className="block text-xs font-semibold text-muted">
        Paste one question per line:{" "}
        <code className="text-ink">Question text | Option 1 | Option 2 | ... | Correct | Marks | Explanation</code>
      </label>
      <p className="text-[11px] text-muted -mt-1">
        Any number of options (2 or more — True/False just needs two). For{" "}
        <strong className="text-ink">multiple correct answers</strong>, separate the numbers with commas:{" "}
        <code className="text-ink">2,4</code> — that creates a select-all-that-apply question, graded as an exact match.
        Marks and Explanation can be left blank, but keep their pipes:{" "}
        <code className="text-ink">... | 2 | | </code>
      </p>
      <textarea
        rows={7}
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder={
          "2 + 2 = ? | 3 | 4 | 5 | 6 | 2 | 1 | Basic addition\n" +
          "Is the sky blue? | True | False | 1 | 1 |\n" +
          "Which are prime? | 2 | 4 | 7 | 9 | 1,3 | 2 | 2 and 7 are prime"
        }
        className={`${fieldInputCompact} font-mono`}
      />
      {status && (
        <div className="flex items-start gap-2.5 rounded-xl border border-danger/30 bg-danger/5 px-3 py-2.5 text-sm text-danger">
          <Icon name="alert" width={15} height={15} className="mt-0.5 shrink-0" />
          {/* whitespace-pre-line: the status now names the offending line
              numbers one per row, which is the whole point of it. */}
          <span className="whitespace-pre-line">{status}</span>
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
  const [confirmSectionDelete, setConfirmSectionDelete] = useState(false);
  const [deletingSection, setDeletingSection] = useState(false);
  const [sectionDeleteError, setSectionDeleteError] = useState("");

  async function confirmDeleteSection() {
    setDeletingSection(true);
    setSectionDeleteError("");
    try {
      await Api.del(`/exams/sections/${section.id}`);
      // No local state reset needed -- this card is about to unmount, and the
      // parent reload is what removes it.
      onQuestionAdded();
    } catch (err) {
      setSectionDeleteError(err instanceof ApiError ? err.message : "Couldn't delete this section.");
      setDeletingSection(false);
    }
  }

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

  /** Move one question up or down. The keyboard path to the same reorder. */
  function moveQuestion(index, delta) {
    const target = index + delta;
    if (target < 0 || target >= section.questions.length) return;
    const questions = [...section.questions];
    const [moved] = questions.splice(index, 1);
    questions.splice(target, 0, moved);
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
            <>
              <button type="button" onClick={() => { setRenaming(true); setTitleDraft(section.title); setTitleError(""); }}
                      className="text-xs font-semibold text-muted hover:text-primary transition-colors">
                Rename
              </button>
              {/* Draft-only, mirroring the backend's own gate
                  (exam_service.delete_section) rather than relying on the
                  button being hidden. Once published, a candidate may be
                  mid-attempt against this exact paper. */}
              <button type="button" onClick={() => { setConfirmSectionDelete(true); setSectionDeleteError(""); }}
                      className="text-xs font-semibold text-muted hover:text-danger transition-colors ml-auto">
                Delete section
              </button>
            </>
          )}
        </div>
      )}

      {/* Inline confirm rather than window.confirm: it can state exactly how
          much is about to be destroyed, which a native dialog cannot. */}
      {confirmSectionDelete && (
        <div className="mb-3 rounded-xl border border-danger/30 bg-danger/5 px-4 py-3">
          <p className="text-sm text-ink">
            Delete <strong>{section.title}</strong>
            {section.questions.length > 0 && (
              <> and its <strong>{section.questions.length}</strong> question{section.questions.length === 1 ? "" : "s"}</>
            )}?
          </p>
          <p className="text-xs text-muted mt-1">This can't be undone.</p>
          {sectionDeleteError && <p className="text-xs text-danger mt-2">{sectionDeleteError}</p>}
          <div className="flex gap-2 mt-3">
            <button type="button" disabled={deletingSection} onClick={confirmDeleteSection}
                    className="text-xs font-semibold rounded-lg bg-danger text-white px-3 py-1.5 disabled:opacity-60">
              {deletingSection ? "Deleting…" : "Delete section"}
            </button>
            <button type="button" disabled={deletingSection} onClick={() => setConfirmSectionDelete(false)}
                    className="text-xs font-semibold text-muted hover:text-ink px-2 py-1.5">
              Cancel
            </button>
          </div>
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
                draggable={isDraft && !interactionLocked}
                onDragStart={() => setDraggedId(q.id)}
                onDragOver={(e) => {
                  e.preventDefault();
                  setDragOverId(q.id);
                }}
                onDragLeave={() => setDragOverId((id) => (id === q.id ? null : id))}
                onDrop={(e) => {
                  e.preventDefault();
                  if (isDraft) handleDrop(q.id);
                }}
                onDragEnd={() => {
                  setDraggedId(null);
                  setDragOverId(null);
                }}
                className={`flex items-start gap-2 rounded-xl border bg-page/60 px-3 py-2.5 transition-colors ${
                  !isDraft || interactionLocked ? "" : "cursor-grab"
                } ${draggedId === q.id ? "opacity-40" : ""} ${dragOverId === q.id ? "border-primary" : "border-border"}`}
              >
                {/* Keyboard alternative to dragging.
                    Reordering was drag-and-drop only, which is unusable without
                    a mouse -- and this is an accessibility platform for exams,
                    so "you need a mouse to build a paper" is a poor answer.
                    Two buttons cover the same operation for keyboard and
                    screen-reader users, and are simply faster for moving one
                    question a single position. */}
                {isDraft ? (
                  <div className="flex flex-col mt-0.5 shrink-0">
                    <button
                      type="button"
                      disabled={i === 0 || interactionLocked}
                      onClick={() => moveQuestion(i, -1)}
                      aria-label={`Move question ${i + 1} up`}
                      className="text-muted hover:text-primary disabled:opacity-30 disabled:hover:text-muted leading-none p-0.5"
                    >
                      <Icon name="chevron-down" width={13} height={13} className="rotate-180" />
                    </button>
                    <button
                      type="button"
                      disabled={i === section.questions.length - 1 || interactionLocked}
                      onClick={() => moveQuestion(i, 1)}
                      aria-label={`Move question ${i + 1} down`}
                      className="text-muted hover:text-primary disabled:opacity-30 disabled:hover:text-muted leading-none p-0.5"
                    >
                      <Icon name="chevron-down" width={13} height={13} />
                    </button>
                  </div>
                ) : (
                  <Icon name="grip" width={16} height={16} className="text-muted mt-0.5 shrink-0" />
                )}
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
                  ) : isDraft ? (
                    /* Draft-only, mirroring exam_service._get_editable_exam.
                       The backend already refused these on a published exam, so
                       showing them was an invitation to click something that
                       could only fail -- and it implied a published paper was
                       still editable, which is exactly the wrong thing to
                       suggest about an exam candidates may be sitting. */
                    <div className="mt-2 flex items-center gap-3">
                      <button type="button" onClick={() => startEdit(q.id)} className="text-xs font-semibold text-primary hover:underline">
                        Edit
                      </button>
                      <button type="button" onClick={() => startDeleteConfirm(q.id)} className="text-xs font-semibold text-danger hover:underline">
                        Delete
                      </button>
                    </div>
                  ) : null}
                </div>
              </div>
            ),
          )
        ) : (
          <EmptyState icon="doc" title="No questions yet" description="Add a question below, or bulk-import a list of MCQs." className="py-6" />
        )}
      </div>
      {isDraft ? (
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
      ) : (
        /* Say WHY rather than silently removing the controls, or the examiner
           is left wondering whether the buttons failed to render. */
        <p className="text-xs text-muted">
          This exam is published, so its questions are locked — candidates may be sitting this exact
          paper. Close the exam and create a new draft if the questions need to change.
        </p>
      )}
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
      await Api.post(`/exams/${examId}/sections`, { title: title.trim() });
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


export { QuestionForm, BulkImportForm, SectionCard, AddSectionForm, parseBulkLine };
