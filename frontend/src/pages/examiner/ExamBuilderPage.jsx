/* Split out of ExaminerDashboard.jsx -- see examiner/shared.jsx for why. */
import { useCallback, useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import Icon from "../../components/Icon.jsx";
import EmptyState from "../../components/EmptyState.jsx";
import { Api, ApiError } from "../../lib/api.js";
import {
  btnPrimary, btnGhost, fieldInput, fieldLabel, fieldInputCompact, fieldLabelCompact,
} from "../../lib/ui.js";
import { ExamStatusBadge, toDatetimeLocalValue, hasExamStarted, ErrorState } from "./shared.jsx";
import { SectionCard, AddSectionForm } from "./QuestionBuilder.jsx";
import {
  AttemptsPanel, ActivePanel, AnalyticsPanel, ExamAccessPanel, ResetHistoryPanel,
} from "./panels.jsx";

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

// Everything about an exam except its schedule (which has its own card above, with its own
// post-publish rules) and its questions (their own tab).
function ExamDetailsForm({ exam, onUpdated }) {
  const isDraft = exam.status === "draft";

  const fromExam = useCallback(() => ({
    title: exam.title || "",
    description: exam.description || "",
    instructions: exam.instructions || "",
    notifyEmail: exam.notify_email || "",
    requireCamera: exam.require_camera,
    requireMicrophone: exam.require_microphone,
    requireScreenShare: exam.require_screen_share,
    requireFullscreen: exam.require_fullscreen,
    releaseResultsAt: toDatetimeLocalValue(exam.release_results_at),
    showResults: exam.show_results ?? true,
    resultsReleaseMode: exam.results_release_mode || "immediate",
    duration: String(exam.duration_minutes ?? ""),
    passPercentage: String(exam.pass_percentage ?? 40),
    randomizeQuestions: exam.randomize_questions,
    randomizeOptions: exam.randomize_options,
    proctoring: exam.proctoring_enabled,
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }), [exam.id, exam.title, exam.description, exam.instructions, exam.notify_email, exam.duration_minutes, exam.pass_percentage, exam.randomize_questions, exam.randomize_options, exam.proctoring_enabled, exam.show_results, exam.results_release_mode]);

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
        notify_email: form.notifyEmail.trim() || null,
        require_camera: form.requireCamera,
        require_microphone: form.requireMicrophone,
        require_screen_share: form.requireScreenShare,
        require_fullscreen: form.requireFullscreen,
        release_results_at: form.releaseResultsAt ? new Date(form.releaseResultsAt).toISOString() : null,
        show_results: form.showResults,
        results_release_mode: form.resultsReleaseMode,
        duration_minutes: parseInt(form.duration, 10),
        // parseInt(..) || 40 turned a valid 0 into 40, because 0 is falsy.
        pass_percentage: Number.isFinite(parseInt(form.passPercentage, 10))
          ? parseInt(form.passPercentage, 10)
          : 40,
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
          <div>
            <dt className="text-xs text-muted">Results shown to candidates</dt>
            <dd className="font-medium text-ink">
              {exam.show_results === false
                ? "Never"
                : exam.results_release_mode === "after_end_time"
                  ? "After the exam's end date/time"
                  : "As soon as each candidate submits"}
            </dd>
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
        {/* notify_email had a column, a schema field, reminder scheduling and
            two email templates behind it, and no field anywhere in the UI to
            set it -- so the whole feature was unreachable from the product. */}
        <div>
          <label className={fieldLabel}>Notification email (optional)</label>
          <input
            type="email" maxLength={150} value={form.notifyEmail}
            onChange={(e) => update("notifyEmail", e.target.value)}
            className={fieldInput}
            placeholder="invigilators@college.edu"
          />
          <p className="mt-1.5 text-xs text-muted">
            Told when this exam is published, and again five minutes before it starts. A department
            list or an invigilator rota works — it does not need a Merit.Ai account.
          </p>
        </div>
        {/* What this exam actually asks a candidate for.
            proctoring_enabled gated the AI signals, but the exam page demanded
            camera, microphone, screen sharing AND fullscreen from everyone
            regardless -- and then told them the exam was not proctored. These
            default to "follow AI proctoring", which is what every existing exam
            does, so nothing changes unless an examiner says otherwise. */}
        <fieldset className="rounded-xl border border-border p-4">
          <legend className="px-2 text-xs font-semibold text-muted uppercase tracking-wide">
            What candidates must allow
          </legend>
          <div className="grid sm:grid-cols-2 gap-3">
            {[
              ["requireCamera", "Camera"],
              ["requireMicrophone", "Microphone"],
              ["requireScreenShare", "Screen sharing"],
              ["requireFullscreen", "Fullscreen"],
            ].map(([key, label]) => (
              <label key={key} className="flex items-center justify-between gap-3 text-sm text-ink">
                <span>{label}</span>
                <select
                  value={form[key] === null || form[key] === undefined ? "" : String(form[key])}
                  onChange={(e) => update(key, e.target.value === "" ? null : e.target.value === "true")}
                  className={`${fieldInputCompact} !mb-0 max-w-[11rem]`}
                >
                  <option value="">Follow AI proctoring</option>
                  <option value="true">Always required</option>
                  <option value="false">Never required</option>
                </select>
              </label>
            ))}
          </div>
          <p className="mt-3 text-xs text-muted leading-relaxed">
            An unproctored exam that still demands a webcam and a shared screen is asking candidates
            for something it does not use.
          </p>
        </fieldset>

        <fieldset className="rounded-xl border border-border p-4">
          <legend className="px-2 text-xs font-semibold text-muted uppercase tracking-wide">
            Results visibility
          </legend>
          <label className="flex items-center gap-2 text-sm text-ink mb-3">
            <input type="checkbox" checked={form.showResults}
                   onChange={(e) => update("showResults", e.target.checked)} className="w-4 h-4 accent-primary" />
            Show candidates their score and pass/fail outcome
          </label>
          {form.showResults ? (
            <>
              <label className={fieldLabel}>When</label>
              <select value={form.resultsReleaseMode} onChange={(e) => update("resultsReleaseMode", e.target.value)} className={fieldInput}>
                <option value="immediate">As soon as each candidate submits</option>
                <option value="after_end_time">Only after the exam's end date/time</option>
              </select>
              {form.resultsReleaseMode === "after_end_time" && !exam.end_time && (
                <p className="mt-1.5 text-xs text-warning leading-relaxed">
                  This exam has no end date/time set yet (see the schedule above), so results will stay
                  held back from every candidate until you set one.
                </p>
              )}
              {form.resultsReleaseMode === "immediate" && (
                <div className="mt-3">
                  <label className={fieldLabel}>Also delay until (optional)</label>
                  <input type="datetime-local" value={form.releaseResultsAt}
                         onChange={(e) => update("releaseResultsAt", e.target.value)} className={fieldInput} />
                  <p className="mt-1.5 text-xs text-muted leading-relaxed">
                    Leave blank to release each candidate's result the moment they submit.
                  </p>
                </div>
              )}
              <p className="mt-3 text-xs text-muted leading-relaxed">
                "As soon as each candidate submits" means the first person to finish can, in
                principle, hand around their result (and, unless disabled below, the answer key)
                while everyone else is still writing. Choose "after the exam's end date/time" to
                prevent that.
              </p>
            </>
          ) : (
            <p className="text-xs text-muted leading-relaxed">
              Candidates will never see their score or pass/fail outcome for this exam. You and any
              admin can always see it.
            </p>
          )}
        </fieldset>

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
  const [publishState, setPublishState] = useState(null); // { tone, message }
  const [publishing, setPublishing] = useState(false);
  const [lifecycleBusy, setLifecycleBusy] = useState(false);
  const [confirmClose, setConfirmClose] = useState(null); // null | { message }

  // The tab is a query parameter, not component state. As state it was lost on every refresh.
  const [searchParams, setSearchParams] = useSearchParams();
  const tab = searchParams.get("tab") || "questions";
  const setTab = (next) => setSearchParams({ tab: next }, { replace: true });

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
    loadExamDetail();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [examId]);

  async function runLifecycle(action, { force = false } = {}) {
    setLifecycleBusy(true);
    setPublishState(null);
    try {
      const suffix = action === "close" && force ? "?force=true" : "";
      await Api.post(`/exams/${examId}/${action}${suffix}`);
      setConfirmClose(null);
      await loadExamDetail();
    } catch (err) {
      // 409 means candidates are still writing. That is a question, not a
      // failure -- closing is still available, it just needs saying twice.
      if (err instanceof ApiError && err.status === 409) {
        setConfirmClose({ message: err.message });
      } else {
        setPublishState({ tone: "error", message: err instanceof ApiError ? err.message : "Something went wrong." });
      }
    } finally {
      setLifecycleBusy(false);
    }
  }

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
    { id: "analytics", label: "Analytics" },
    { id: "resets", label: "Reset History" },
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
            {/* Publish, then Close, then Reopen. ExamStatus.CLOSED existed in
                the database from the beginning with no button anywhere, so a
                published exam stayed on every candidate's list forever and an
                examiner who wanted one taken down had no way to do it. */}
            <div className="flex items-center gap-2">
              {exam?.status === "draft" && (
                <button
                  type="button"
                  onClick={handlePublish}
                  disabled={publishing}
                  className={`${btnPrimary.replace("px-5 py-3", "px-3 py-1.5")} text-xs`}
                >
                  {publishing ? "Publishing…" : "Publish Exam"}
                </button>
              )}
              {exam?.status === "published" && (
                <button
                  type="button"
                  onClick={() => runLifecycle("close")}
                  disabled={lifecycleBusy}
                  className={`${btnGhost} px-3 py-1.5 text-xs`}
                >
                  {lifecycleBusy ? "Closing…" : "Close Exam"}
                </button>
              )}
              {exam?.status === "closed" && (
                <button
                  type="button"
                  onClick={() => runLifecycle("reopen")}
                  disabled={lifecycleBusy}
                  className={`${btnPrimary.replace("px-5 py-3", "px-3 py-1.5")} text-xs`}
                >
                  {lifecycleBusy ? "Reopening…" : "Reopen Exam"}
                </button>
              )}
            </div>
          </div>
          {publishState && (
            <p className={`mt-4 text-sm ${publishState.tone === "success" ? "text-success" : "text-danger"}`}>{publishState.message}</p>
          )}
          {confirmClose && (
            <div role="alert" className="mt-4 rounded-xl border border-warning/30 bg-warning/5 px-4 py-3">
              <p className="text-sm text-ink mb-3">{confirmClose.message}</p>
              <div className="flex gap-2">
                <button type="button" onClick={() => runLifecycle("close", { force: true })}
                        disabled={lifecycleBusy}
                        className={`${btnPrimary.replace("px-5 py-3", "px-3 py-1.5")} text-xs`}>
                  Close anyway
                </button>
                <button type="button" onClick={() => setConfirmClose(null)}
                        className={`${btnGhost} px-3 py-1.5 text-xs`}>
                  Keep it open
                </button>
              </div>
            </div>
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
                {/* Adding a section is a structural edit, refused by the
                    backend once the exam is published. Showing the form
                    anyway invited a submission that could only fail. */}
                {exam.status === "draft" && <AddSectionForm examId={exam.id} onAdded={loadExamDetail} />}
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
            {tab === "analytics" && <AnalyticsPanel examId={exam.id} />}
            {tab === "resets" && <ResetHistoryPanel examId={exam.id} />}
          </>
        )}
      </div>
    </main>
  );
}
/* ===================== Per-exam access ===================== */

/**
 * Who can take this specific exam.
 */

export { ExamScheduleCard, ExamDetailsForm, ExamBuilderView };
