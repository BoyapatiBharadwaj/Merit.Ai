import { useState, useEffect, useMemo, useRef, useCallback } from "react";
import { useParams, useNavigate, Navigate, Link } from "react-router-dom";
import RedirectToLogin from "../components/RedirectToLogin.jsx";
import { Api, ApiError, setAttemptToken } from "../lib/api.js";
import { createAutosaveQueue, SaveState } from "../lib/autosave.js";
import { isLoggedIn, getName, getRole } from "../lib/auth.js";
import { createProctoring } from "../lib/proctoring.js";
import { createLockdown, requestScreenShare } from "../lib/lockdown.js";
import { createEventLogger } from "../lib/eventLogger.js";
import Icon from "../components/Icon.jsx";
import CodeEditor from "../components/CodeEditor.jsx";
import { btnPrimary, btnGhost } from "../lib/ui.js";

/** Port of frontend/exam.html + js/exam.js + js/proctoring.js: the real
 * exam-taking interface -- timer, question navigator, MCQ/coding answering
 * with autosave + retry, mark-for-review, submit, and live AI proctoring. */

const SIGNAL_LABELS = {
  face: "Face verification",
  objects: "Object detection",
  pose: "Head pose",
  gaze: "Gaze tracking",
  audio: "Microphone",
  monitor: "External monitor",
};
const RETRY_DELAYS_MS = [2000, 4000, 8000, 12000, 20000];

/**
 * A downscaled JPEG data URL of the current video frame.
 */
function captureVideoFrame(video, maxEdge = 640, quality = 0.8) {
  const sourceW = video.videoWidth || 320;
  const sourceH = video.videoHeight || 240;
  const scale = Math.min(1, maxEdge / Math.max(sourceW, sourceH));
  const canvas = document.createElement("canvas");
  canvas.width = Math.max(1, Math.round(sourceW * scale));
  canvas.height = Math.max(1, Math.round(sourceH * scale));
  const ctx = canvas.getContext("2d", { alpha: false });
  ctx.imageSmoothingEnabled = true;
  ctx.imageSmoothingQuality = "high";
  ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
  return canvas.toDataURL("image/jpeg", quality);
}
const RESYNC_INTERVAL_MS = 90000;
const CODE_SAVE_DEBOUNCE_MS = 1200;

/**
 * Keyed on the ATTEMPT, not the exam.
 */
function markStorageKey(attemptId) {
  return `aep_marked_attempt_${attemptId}`;
}
function loadMarked(attemptId) {
  try {
    const raw = localStorage.getItem(markStorageKey(attemptId));
    return raw ? new Set(JSON.parse(raw)) : new Set();
  } catch {
    return new Set();
  }
}
function persistMarked(attemptId, set) {
  try {
    localStorage.setItem(markStorageKey(attemptId), JSON.stringify([...set]));
  } catch {
    // best-effort
  }
}

function formatClock(totalSeconds) {
  const s = Math.max(0, Math.floor(totalSeconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  const pad = (n) => String(n).padStart(2, "0");
  return h > 0 ? `${h}:${pad(m)}:${pad(sec)}` : `${pad(m)}:${pad(sec)}`;
}

export default function Exam() {
  const { examId } = useParams();
  const navigate = useNavigate();

  // The auth guard used to live here, before any hooks.

  const [phase, setPhase] = useState("precheck"); // precheck | active
  const [beginning, setBeginning] = useState(false);
  const [precheckError, setPrecheckError] = useState("");

  const [attemptId, setAttemptId] = useState(null);
  // Mirrors attemptId, but read by callbacks that run inside long-lived
  // setInterval/setTimeout closures (the exam timer's auto-submit, retry
  // loops) which captured `attemptId` from the render they were created in.
  const attemptIdRef = useRef(null);
  const [examTitle, setExamTitle] = useState("");
  const [questionIds, setQuestionIds] = useState([]);
  const [proctoringEnabled, setProctoringEnabled] = useState(false);

  const [answeredMap, setAnsweredMap] = useState({});
  const [markedSet, setMarkedSet] = useState(() => new Set());

  const [currentIndex, setCurrentIndex] = useState(0);
  const [currentQuestion, setCurrentQuestion] = useState(null);
  // Mirrors currentQuestion for the same reason attemptIdRef mirrors attemptId.
  const currentQuestionRef = useRef(null);
  const [questionLoading, setQuestionLoading] = useState(false);
  const [questionError, setQuestionError] = useState("");

  const [timerText, setTimerText] = useState("--:--");
  const [timerDanger, setTimerDanger] = useState(false);
  const secondsRemainingRef = useRef(0);
  const timerIntervalRef = useRef(null);
  const resyncIntervalRef = useRef(null);
  const submittingRef = useRef(false);

  const [fullscreenActive, setFullscreenActive] = useState(!!document.fullscreenElement);
  const [connectionBanner, setConnectionBanner] = useState(null);
  // Both stay `null` -- and therefore hidden in the header -- on any browser that doesn't
  // expose the underlying API (Firefox, Safari).
  const [batteryStatus, setBatteryStatus] = useState(null); // { level: 0-1, charging: bool } | null
  const [networkQuality, setNetworkQuality] = useState(null); // { label: "Good"|"Fair"|"Poor" } | null

  // ---------- autosave ----------
  // All persistence goes through lib/autosave.js.
  const [saveSummary, setSaveSummary] = useState({
    pending: 0, failed: 0, lastSavedAt: null, state: SaveState.SAVED,
  });
  const expiredRef = useRef(false);
  const autosaveRef = useRef(null);
  if (!autosaveRef.current) {
    autosaveRef.current = createAutosaveQueue({
      transports: {
        mcq: (questionId, optionId, envelope) =>
          Api.exam.put(`/attempts/${attemptIdRef.current}/answer`, {
            question_id: questionId, selected_option_id: optionId, ...envelope,
          }),
        multi: (questionId, optionIds, envelope) =>
          Api.exam.put(`/attempts/${attemptIdRef.current}/multi-answer`, {
            question_id: questionId, selected_option_ids: optionIds || [], ...envelope,
          }),
        code: (questionId, sourceCode, envelope) =>
          Api.exam.put(`/attempts/${attemptIdRef.current}/code-answer`, {
            question_id: questionId, source_code: sourceCode ?? "", ...envelope,
          }),
      },
      onChange: setSaveSummary,
      onExpired: (expiredAttemptId) => redirectAfterAutoSubmit(expiredAttemptId),
    });
  }
  const autosave = autosaveRef.current;

  const [toasts, setToasts] = useState([]);
  const [signalStates, setSignalStates] = useState({});
  const [statusText, setStatusText] = useState({ text: "Initializing…", kind: "muted" });
  const [violationCount, setViolationCount] = useState(0);
  const videoRef = useRef(null);
  const proctoringRef = useRef(null);

  // Lockdown state. `lockdownBreach` holding a string is what renders the blocking overlay.
  const lockdownRef = useRef(null);
  const [lockdownBreach, setLockdownBreach] = useState(null);
  const [lockdownTerminated, setLockdownTerminated] = useState(false);
  const [strikeState, setStrikeState] = useState({ strikes: 0, limit: 3 });

  // Screen-share state.
  const screenStreamRef = useRef(null);
  const unmountedRef = useRef(false);
  const [screenShareActive, setScreenShareActive] = useState(false);
  // Set by breachCallback right before the overlay appears, read by restoredCallback once the
  // lockdown controller's own resume() has finished re-sharing.
  const lastBreachWasScreenShareRef = useRef(false);

  // ---------- system check (precheck screen) ----------
  // A separate, throwaway camera/mic stream for the live preview shown while reviewing the
  // checklist.
  const previewVideoRef = useRef(null);
  const previewStreamRef = useRef(null);
  const micAudioCtxRef = useRef(null);
  const micMeterIntervalRef = useRef(null);
  // Incremented on every testCameraMic() call and captured per-call as `myToken`.
  const cameraMicTestTokenRef = useRef(0);
  const cameraMicTimeoutRef = useRef(null);
  const [cameraMicStatus, setCameraMicStatus] = useState("idle"); // idle | testing | ok | error
  const [cameraMicError, setCameraMicError] = useState("");
  const [micLevel, setMicLevel] = useState(0); // 0-100, drives the live level bar
  // Window Management API check for a second display.
  const [externalMonitorStatus, setExternalMonitorStatus] = useState("checking"); // checking | ok | warning | unavailable
  // Persistent, inline failure reasons for the two gesture-driven checks.
  const [screenShareError, setScreenShareError] = useState("");
  const [fullscreenError, setFullscreenError] = useState("");
  const [externalMonitorCount, setExternalMonitorCount] = useState(1);

  // Exam metadata + identity-verification status for the pre-exam overview.
  const [examMeta, setExamMeta] = useState(null);
  // What THIS exam actually asks for, resolved by the server.
  const requires = examMeta?.requires ?? {
    camera: true, microphone: true, screen_share: true, fullscreen: true,
  };
  const [identityStatus, setIdentityStatus] = useState(null);
  // Live face-match check run once the camera preview is working,
  // comparing the live frame against the registered face profile via the
  // same /proctoring/face/verify endpoint the in-exam monitor polls.
  const [faceMatchStatus, setFaceMatchStatus] = useState("idle"); // idle | checking | ok | mismatch | error
  const [faceMatchError, setFaceMatchError] = useState("");
  const faceMatchTokenRef = useRef(0);

  const [showSubmitConfirm, setShowSubmitConfirm] = useState(false);
  const [submitError, setSubmitError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [submitReason, setSubmitReason] = useState("manual"); // "manual" | "auto" -- drives the lock overlay's message

  const codeValueRef = useRef("");
  const [codeEditorKey, setCodeEditorKey] = useState(0);
  const codeSaveTimeoutRef = useRef(null);
  const [runResult, setRunResult] = useState(null);
  const [running, setRunning] = useState(false);

  // ---------- expired-attempt auto-submit redirect ----------
  // The server is the source of truth for the deadline (see
  // attempt_service._expired_auto_submit_error / finalize_if_expired).
  function isExpiredAutoSubmitError(err) {
    return err instanceof ApiError && err.status === 400 && err.detail && typeof err.detail === "object" && err.detail.code === "attempt_expired_auto_submitted";
  }
  function redirectAfterAutoSubmit(expiredAttemptId) {
    stopTimer();
    if (resyncIntervalRef.current) clearInterval(resyncIntervalRef.current);
    if (proctoringRef.current) proctoringRef.current.stop();
    if (lockdownRef.current) lockdownRef.current.stop();
    navigate(`/results/${expiredAttemptId}`, { replace: true, state: { autoSubmitted: true } });
  }

  // ---------- toasts ----------
  const pushToast = useCallback((message) => {
    const id = Date.now() + Math.random();
    setToasts((prev) => [...prev.slice(-3), { id, message }]);
    setTimeout(() => setToasts((prev) => prev.filter((t) => t.id !== id)), 4500);
  }, []);

  // ---------- flush the debounced code save ----------
  // The code editor debounces by ~1.2s, so at any moment the candidate's most recent keystrokes
  // may exist only in the browser.
  function flushCodeSave() {
    if (!codeSaveTimeoutRef.current) return;
    clearTimeout(codeSaveTimeoutRef.current);
    codeSaveTimeoutRef.current = null;
    // Reads the ref, not the `currentQuestion` state, for the same reason attemptIdRef exists
    // (see the comment at the top of this component).
    const question = currentQuestionRef.current;
    if (question?.question_type === "coding") {
      autosave.save("code", question.question_id, codeValueRef.current);
    }
  }

  // ---------- load a question by index ----------
  // Every load takes a ticket.
  const questionRequestRef = useRef(0);
  const loadQuestion = useCallback(
    async (index, ids = questionIds, attemptIdVal = attemptIdRef.current) => {
      const questionId = ids[index];
      if (!questionId) return;
      const ticket = ++questionRequestRef.current;
      setQuestionLoading(true);
      setQuestionError("");
      setRunResult(null);
      try {
        const q = await Api.exam.get(`/attempts/${attemptIdVal}/question/${questionId}`);
        if (ticket !== questionRequestRef.current) return; // a newer load won
        // Continue the server's version sequence for this question instead of restarting at 1.
        const kind = q.question_type === "coding" ? "code"
          : q.question_type === "multi_select" ? "multi" : "mcq";
        autosave.seed(kind, q.question_id, q.answer_version);
        setCurrentQuestion(q);
        if (q.question_type === "coding") {
          codeValueRef.current = q.source_code || "";
          setCodeEditorKey((k) => k + 1);
        }
      } catch (err) {
        if (ticket !== questionRequestRef.current) return;
        if (isExpiredAutoSubmitError(err)) {
          redirectAfterAutoSubmit(err.detail.attempt_id);
          return;
        }
        setQuestionError(err.message || "Couldn't load this question.");
      } finally {
        if (ticket === questionRequestRef.current) setQuestionLoading(false);
      }
    },
    [questionIds, autosave]
  );

  function goToIndex(nextIndex) {
    flushCodeSave();
    const clamped = Math.max(0, Math.min(questionIds.length - 1, nextIndex));
    setCurrentIndex(clamped);
    loadQuestion(clamped);
  }

  // ---------- timer ----------
  function stopTimer() {
    if (timerIntervalRef.current) clearInterval(timerIntervalRef.current);
    timerIntervalRef.current = null;
  }
  function startTimer() {
    stopTimer();
    updateTimerDisplay();
    timerIntervalRef.current = setInterval(() => {
      secondsRemainingRef.current = Math.max(0, secondsRemainingRef.current - 1);
      updateTimerDisplay();
      if (secondsRemainingRef.current <= 0) {
        stopTimer();
        // One-way door. Everything below checks expiredRef before accepting a change, and
        // nothing ever sets it back to false.
        expiredRef.current = true;
        setExpiredPendingSubmission(true);
        doSubmit(true);
      }
    }, 1000);
  }
  function updateTimerDisplay() {
    setTimerText(formatClock(secondsRemainingRef.current));
    setTimerDanger(secondsRemainingRef.current <= 60);
  }

  // ---------- begin exam ----------
  async function beginExam() {
    setBeginning(true);
    setPrecheckError("");
    try {
      const res = await Api.post(`/attempts/start/${examId}`);
      // Valid until this attempt's deadline plus a grace period, so a three-hour exam is no
      // longer ended by a two-hour session token expiring.
      setAttemptToken(res.attempt_token);
      setAttemptId(res.attempt_id);
      attemptIdRef.current = res.attempt_id;
      setExamTitle(res.exam_title);
      setQuestionIds(res.question_ids_in_order);
      setProctoringEnabled(res.proctoring_enabled);
      secondsRemainingRef.current = res.remaining_seconds;

      // Recover previously-saved answers so the navigator repaints correctly
      // after a reload/reconnect.
      try {
        const answers = await Api.exam.get(`/attempts/${res.attempt_id}/answers`);
        setAnsweredMap(answers || {});
      } catch {
        // non-fatal
      }
      setMarkedSet(loadMarked(res.attempt_id));

      setPhase("active");
      startTimer();
      resyncIntervalRef.current = setInterval(() => resyncRemainingTime(), RESYNC_INTERVAL_MS);

      // AI proctoring itself is started from a useEffect keyed on `phase` (below), not here.

      await loadQuestion(0, res.question_ids_in_order, res.attempt_id);
    } catch (err) {
      // Resuming an attempt whose deadline already passed while the student was away.
      if (isExpiredAutoSubmitError(err)) {
        redirectAfterAutoSubmit(err.detail.attempt_id);
        return;
      }
      setPrecheckError(err.message || "Couldn't start this exam.");
    } finally {
      setBeginning(false);
    }
  }

  async function resyncRemainingTime() {
    // A read-only status call, not another POST /attempts/start.
    try {
      const res = await Api.exam.get(`/attempts/${attemptIdRef.current}/status`);
      // The server may have finalised this attempt while the tab was asleep or
      // offline. Learning that from the server beats the local timer guessing.
      if (res.status !== "in_progress") {
        redirectAfterAutoSubmit(res.attempt_id);
        return;
      }
      secondsRemainingRef.current = res.remaining_seconds;
      updateTimerDisplay();
    } catch (err) {
      if (isExpiredAutoSubmitError(err)) {
        redirectAfterAutoSubmit(err.detail.attempt_id);
        return;
      }
      // best-effort resync; keep counting locally
    }
  }

  // ---------- submit ----------
  // The snapshot is built ONCE, before the first attempt, and every retry re-sends the
  // identical payload.
  const finalizePayloadRef = useRef(null);
  const [expiredPendingSubmission, setExpiredPendingSubmission] = useState(false);

  async function attemptSubmit(auto, retryAttempt = 0) {
    try {
      if (!finalizePayloadRef.current) {
        finalizePayloadRef.current = { final_answers: autosave.snapshot() };
      }
      // Answers travel WITH the submission.
      const res = await Api.exam.post(
        `/attempts/${attemptIdRef.current}/finalize`, finalizePayloadRef.current,
      );
      stopTimer();
      if (resyncIntervalRef.current) clearInterval(resyncIntervalRef.current);
      if (proctoringRef.current) proctoringRef.current.stop();
      autosave.stop();
      setAttemptToken(null);
      navigate(`/results/${res.attempt_id}`, { replace: true, state: { autoSubmitted: auto } });
    } catch (err) {
      // The server already finalised this attempt -- a retry whose predecessor actually landed,
      // or the expiry sweep beating us to it.
      if (isExpiredAutoSubmitError(err)) {
        redirectAfterAutoSubmit(err.detail.attempt_id);
        return;
      }

      const status = err instanceof ApiError ? err.status : null;
      const retryable = status === 0 || status === 408 || status === 429 || status >= 500;

      // Past the deadline, retrying is the ONLY acceptable behaviour.
      if (expiredRef.current) {
        const delay = RETRY_DELAYS_MS[Math.min(retryAttempt, RETRY_DELAYS_MS.length - 1)];
        setConnectionBanner("Your time is up. Still trying to submit your exam — keep this page open.");
        setTimeout(() => attemptSubmit(auto, retryAttempt + 1), delay);
        return;
      }

      if (retryable && retryAttempt < RETRY_DELAYS_MS.length) {
        setConnectionBanner("Connection lost. Retrying submission…");
        setTimeout(() => attemptSubmit(auto, retryAttempt + 1), RETRY_DELAYS_MS[retryAttempt]);
        return;
      }

      // A manual submit that genuinely failed: hand control back so they can
      // try again, and throw away the snapshot so the next attempt rebuilds it
      // from whatever they have changed in the meantime.
      finalizePayloadRef.current = null;
      submittingRef.current = false;
      setSubmitting(false);
      setSubmitError(err.message || "Submission failed. Please try again.");
    }
  }

  async function doSubmit(auto) {
    if (submittingRef.current) return;
    submittingRef.current = true;
    setSubmitReason(auto ? "auto" : "manual");
    setSubmitting(true);
    setSubmitError("");

    // Push out the debounced code save, then wait for everything outstanding.
    flushCodeSave();
    await autosave.waitForIdle({ timeoutMs: 8000 });
    attemptSubmit(auto);
  }

  // ---------- mark for review ----------
  function toggleMark() {
    if (!currentQuestion) return;
    setMarkedSet((prev) => {
      const next = new Set(prev);
      if (next.has(currentQuestion.question_id)) next.delete(currentQuestion.question_id);
      else next.add(currentQuestion.question_id);
      persistMarked(attemptIdRef.current, next);
      return next;
    });
  }

  /**
   * Arrow keys inside a radio group, which is what a radio group is for.
   */
  function handleOptionKeys(event, index, options, choose) {
    const keys = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 };
    let next = null;
    if (event.key in keys) next = (index + keys[event.key] + options.length) % options.length;
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = options.length - 1;
    if (next === null) return;
    event.preventDefault();
    choose(options[next].id);
    // Move focus with the selection, or the visual focus ring and the checked
    // option drift apart.
    const group = event.currentTarget.parentElement;
    group?.children?.[next]?.focus?.();
  }

  // ---------- answering ----------
  // Every one of these refuses to record a change once the deadline has passed.
  function canAnswer() {
    return Boolean(currentQuestion) && !expiredRef.current && !submittingRef.current;
  }

  // ---------- MCQ select ----------
  function selectOption(optionId) {
    if (!canAnswer()) return;
    setCurrentQuestion((prev) => ({ ...prev, selected_option_id: optionId }));
    setAnsweredMap((prev) => ({ ...prev, [currentQuestion.question_id]: optionId }));
    autosave.save("mcq", currentQuestion.question_id, optionId);
  }
  function clearResponse() {
    if (!canAnswer() || currentQuestion.question_type !== "mcq") return;
    setCurrentQuestion((prev) => ({ ...prev, selected_option_id: null }));
    setAnsweredMap((prev) => ({ ...prev, [currentQuestion.question_id]: null }));
    autosave.save("mcq", currentQuestion.question_id, null);
  }

  // ---------- multi-select toggle ----------
  function toggleMultiOption(optionId) {
    if (!canAnswer()) return;
    const current = currentQuestion.selected_option_ids || [];
    const next = current.includes(optionId) ? current.filter((id) => id !== optionId) : [...current, optionId];
    setCurrentQuestion((prev) => ({ ...prev, selected_option_ids: next }));
    setAnsweredMap((prev) => ({ ...prev, [currentQuestion.question_id]: next.length ? next : null }));
    autosave.save("multi", currentQuestion.question_id, next);
  }
  function clearMultiResponse() {
    if (!canAnswer() || currentQuestion.question_type !== "multi_select") return;
    setCurrentQuestion((prev) => ({ ...prev, selected_option_ids: [] }));
    setAnsweredMap((prev) => ({ ...prev, [currentQuestion.question_id]: null }));
    autosave.save("multi", currentQuestion.question_id, []);
  }

  // ---------- coding ----------
  function onCodeChange(newValue) {
    if (!canAnswer()) return;
    codeValueRef.current = newValue;
    if (codeSaveTimeoutRef.current) clearTimeout(codeSaveTimeoutRef.current);
    codeSaveTimeoutRef.current = setTimeout(() => {
      codeSaveTimeoutRef.current = null;
      if (currentQuestionRef.current) {
        autosave.save("code", currentQuestionRef.current.question_id, codeValueRef.current);
      }
    }, CODE_SAVE_DEBOUNCE_MS);
  }
  function resetCode() {
    if (!canAnswer()) return;
    // Confirm first: this replaces work the candidate may have spent the whole
    // exam on, and the editor has no undo across a remount.
    const current = (codeValueRef.current || "").trim();
    const starter = (currentQuestion.starter_code || "").trim();
    if (current && current !== starter) {
      const ok = window.confirm(
        "Reset your code to the starter template? Everything you have written for this question will be discarded."
      );
      if (!ok) return;
    }
    codeValueRef.current = currentQuestion.starter_code || "";
    setCodeEditorKey((k) => k + 1);
    autosave.save("code", currentQuestion.question_id, codeValueRef.current);
  }
  async function runSample() {
    if (!currentQuestion) return;
    setRunning(true);
    setRunResult(null);
    try {
      const res = await Api.exam.post(`/attempts/${attemptIdRef.current}/code-answer/run`, {
        question_id: currentQuestion.question_id,
        source_code: codeValueRef.current,
      });
      setRunResult(res);
    } catch (err) {
      setRunResult({ available: true, results: [], all_passed: false, message: err.message || "Run failed." });
    } finally {
      setRunning(false);
    }
  }

  useEffect(() => {
    currentQuestionRef.current = currentQuestion;
  }, [currentQuestion]);

  // ---------- fullscreen badge ----------
  // Display only. The *enforcement* half (breach detection, strikes, auto-submit) lives in the
  // lockdown controller below.
  useEffect(() => {
    function onFsChange() {
      const active = !!document.fullscreenElement;
      setFullscreenActive(active);
      if (active) setFullscreenError("");
    }
    document.addEventListener("fullscreenchange", onFsChange);
    return () => document.removeEventListener("fullscreenchange", onFsChange);
  }, []);

  // ---------- battery indicator ----------
  // Battery Status API.
  useEffect(() => {
    if (typeof navigator.getBattery !== "function") return;
    let battery = null;
    let cancelled = false;
    const update = () => {
      if (battery) setBatteryStatus({ level: battery.level, charging: battery.charging });
    };
    navigator.getBattery().then((b) => {
      if (cancelled) return; // component unmounted before the promise settled
      battery = b;
      update();
      b.addEventListener("levelchange", update);
      b.addEventListener("chargingchange", update);
    });
    return () => {
      cancelled = true;
      if (battery) {
        battery.removeEventListener("levelchange", update);
        battery.removeEventListener("chargingchange", update);
      }
    };
  }, []);

  // ---------- connection-quality indicator ----------
  // "WiFi strength" (signal bars) is not something any website can read.
  useEffect(() => {
    const connection = navigator.connection || navigator.mozConnection || navigator.webkitConnection;
    if (!connection) return;
    const LABELS = { "slow-2g": "Poor", "2g": "Poor", "3g": "Fair", "4g": "Good" };
    const update = () => setNetworkQuality({ label: LABELS[connection.effectiveType] || "Fair" });
    update();
    connection.addEventListener("change", update);
    return () => connection.removeEventListener("change", update);
  }, []);

  // ---------- centralized event-batching logger ----------
  // Shared by both the lockdown controller (copy/paste, right-click, screenshot attempts) and
  // the AI proctoring controller (face/pose/object/ audio violations) below, so several
  // violations flagged within the same few seconds become one batched request instead of one
  // round trip each.
  const eventLoggerRef = useRef(null);
  useEffect(() => {
    if (phase !== "active" || !attemptId) return;
    const logger = createEventLogger();
    eventLoggerRef.current = logger;
    logger.init({ attemptIdVal: attemptId });
    return () => {
      logger.stop();
      eventLoggerRef.current = null;
    };
  }, [phase, attemptId]);

  // ---------- exam lockdown ----------
  // Runs for every attempt, proctored or not.
  useEffect(() => {
    if (phase !== "active" || !attemptId) return;
    const controller = createLockdown();
    lockdownRef.current = controller;
    controller.init({
      attemptIdVal: attemptId,
      logEventCallback: (eventType, description, screenshot) => {
        if (eventLoggerRef.current) eventLoggerRef.current.log(eventType, description, screenshot);
      },
      breachCallback: ({ reason, eventType }) => {
        setLockdownBreach(reason || "You left the exam window.");
        if (eventType === "screen_share_stopped") {
          setScreenShareActive(false);
          lastBreachWasScreenShareRef.current = true;
        }
      },
      restoredCallback: () => {
        setLockdownBreach(null);
        // The controller's own resume() has already re-acquired and re-armed
        // a fresh stream by the time this fires (see requestScreenShare/
        // watchScreenShare inside lockdown.js's _resume()).
        if (lastBreachWasScreenShareRef.current) {
          setScreenShareActive(true);
          lastBreachWasScreenShareRef.current = false;
        }
      },
      strikeCallback: ({ strikes, limit }) => setStrikeState({ strikes, limit }),
      terminatedCallback: ({ strikes, limit }) => {
        setStrikeState({ strikes, limit });
        setLockdownTerminated(true);
        setLockdownBreach(null);
        // The server has already force-submitted at this point; stop every
        // local loop so nothing races the redirect, then show the result.
        stopTimer();
        if (resyncIntervalRef.current) clearInterval(resyncIntervalRef.current);
        if (proctoringRef.current) proctoringRef.current.stop();
        submittingRef.current = true;
      },
      toastCallback: pushToast,
    });
    // The initial getDisplayMedia() request (fired from the Begin Exam click) usually resolves
    // after this effect has already mounted the controller.
    if (screenStreamRef.current) controller.watchScreenShare(screenStreamRef.current);
    return () => {
      controller.stop();
      lockdownRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [phase, attemptId]);

  // ---------- AI proctoring ----------
  // Started only once the active-phase DOM (and its <video> element) has actually painted:
  // videoRef.current is still null immediately after setPhase("active"), so a stream attached
  // there would have nowhere to go and every face/pose/object check would silently no-op.
  useEffect(() => {
    if (phase !== "active" || !proctoringEnabled || !attemptId) return;
    const controller = createProctoring();
    proctoringRef.current = controller;
    controller.init({
      attemptIdVal: attemptId,
      videoElement: videoRef.current,
      statusUpdateCallback: (name, state) => {
        if (name === "__status__") {
          setStatusText(state);
        } else {
          setSignalStates((prev) => ({ ...prev, [name]: state }));
        }
      },
      violationCallback: (eventType, description, count) => {
        // tab_switch no longer arrives here -- lockdown.js owns it and feeds
        // setTabSwitchCount the authoritative server-side strike count.
        setViolationCount(count);
      },
      toastCallback: pushToast,
      logEventCallback: (eventType, description, screenshot) => {
        if (eventLoggerRef.current) eventLoggerRef.current.log(eventType, description, screenshot);
      },
    });
    return () => {
      controller.stop();
      proctoringRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [phase, proctoringEnabled, attemptId]);

  // ---------- fullscreen entry ----------
  // requestFullscreen() only succeeds when called synchronously inside a user-gesture event
  // handler (a click).
  function requestFullscreenNow() {
    setFullscreenError("");
    try {
      const el = document.documentElement;
      if (!el.requestFullscreen) {
        setFullscreenError("Fullscreen is not supported by this browser.");
        return;
      }
      el.requestFullscreen().catch((err) => {
        setFullscreenError(err?.message || "Your browser blocked fullscreen. Please try again.");
      });
    } catch (err) {
      // A synchronous throw here (e.g. called outside a real user gesture, or from within a
      // restrictive iframe) must never propagate.
      setFullscreenError(err?.message || "Your browser blocked fullscreen. Please try again.");
    }
  }

  // ---------- screen sharing ----------
  // Same user-activation constraint as requestFullscreenNow, so this is called directly from
  // the Begin Exam button's onClick, not awaited on.
  function beginScreenShare() {
    setScreenShareError("");
    let sharePromise;
    try {
      sharePromise = requestScreenShare();
    } catch (err) {
      // Belt-and-suspenders: requestScreenShare() itself is written to never throw
      // synchronously (see lockdown.js), but this call sits in the same synchronous chain as
      // beginExam() below, so nothing here is allowed to risk stopping that from running.
      setScreenShareActive(false);
      setScreenShareError(err?.message || "Screen sharing is required for this exam. Please allow it and try again.");
      pushToast(err?.message || "Screen sharing is required for this exam. Please allow it and try again.");
      return;
    }
    sharePromise
      .then((stream) => {
        if (unmountedRef.current) {
          stream.getTracks().forEach((t) => t.stop());
          return;
        }
        screenStreamRef.current = stream;
        setScreenShareActive(true);
        setScreenShareError("");
        if (lockdownRef.current) lockdownRef.current.watchScreenShare(stream);
      })
      .catch((err) => {
        setScreenShareActive(false);
        setScreenShareError(err?.message || "Screen sharing is required for this exam. Please allow it and try again.");
        pushToast(err?.message || "Screen sharing is required for this exam. Please allow it and try again.");
      });
  }

  // ---------- system check: camera & microphone preview ----------
  // Unlike fullscreen/getDisplayMedia, getUserMedia() does not require a fresh synchronous
  // gesture in practice on the browsers this app targets, so this is safe to call (and re-call,
  // for a "Test Again" retry) from a plain button click without the same activation
  // constraints.
  function stopCameraMicPreview() {
    if (cameraMicTimeoutRef.current) {
      clearTimeout(cameraMicTimeoutRef.current);
      cameraMicTimeoutRef.current = null;
    }
    if (micMeterIntervalRef.current) {
      clearInterval(micMeterIntervalRef.current);
      micMeterIntervalRef.current = null;
    }
    if (micAudioCtxRef.current) {
      micAudioCtxRef.current.close().catch(() => {});
      micAudioCtxRef.current = null;
    }
    if (previewStreamRef.current) {
      previewStreamRef.current.getTracks().forEach((t) => t.stop());
      previewStreamRef.current = null;
    }
    if (previewVideoRef.current) previewVideoRef.current.srcObject = null;
    setMicLevel(0);
  }

  // How long to wait for getUserMedia() before giving up and surfacing an error instead of
  // leaving the row stuck on "Requesting access..." forever.
  const CAMERA_MIC_TIMEOUT_MS = 15000;

  async function testCameraMic() {
    stopCameraMicPreview();
    setCameraMicStatus("testing");
    setCameraMicError("");

    const myToken = ++cameraMicTestTokenRef.current;

    if (!window.isSecureContext) {
      setCameraMicStatus("error");
      setCameraMicError("Camera and microphone need a secure connection (HTTPS). Check the exam's URL.");
      return;
    }
    if (!navigator.mediaDevices?.getUserMedia) {
      setCameraMicStatus("error");
      setCameraMicError("Camera and microphone access is not supported by this browser.");
      return;
    }

    // Cheap upfront check that doesn't itself require permission (device *labels* stay blank
    // without it, but the list of kinds is still reported).
    try {
      const devices = await navigator.mediaDevices.enumerateDevices();
      if (cameraMicTestTokenRef.current !== myToken) return;
      const hasVideo = devices.some((d) => d.kind === "videoinput");
      const hasAudio = devices.some((d) => d.kind === "audioinput");
      if (!hasVideo && !hasAudio) {
        setCameraMicStatus("error");
        setCameraMicError("No camera or microphone was detected on this device.");
        return;
      }
    } catch {
      // enumerateDevices() itself failing is unusual and not fatal here --
      // fall through and let the real getUserMedia() attempt below decide.
    }

    cameraMicTimeoutRef.current = setTimeout(() => {
      if (cameraMicTestTokenRef.current !== myToken) return;
      cameraMicTimeoutRef.current = null;
      setCameraMicStatus("error");
      // Covers both realistic causes of a genuine hang (as opposed to a quick reject, which
      // lands in the catch block below instead).
      setCameraMicError(
        "This is taking too long. If your browser is showing a permission prompt, look for it (it can appear " +
          "outside this window) and allow access. If camera/microphone access for this site is already allowed " +
          "in your browser, check your operating system's privacy settings (Windows: Settings > Privacy & " +
          "security > Camera/Microphone; macOS: System Settings > Privacy & Security) to make sure browsers are " +
          "allowed to use them. If it still hangs, fully reload this page -- a stuck request can hold onto the " +
          "camera in a way that a retry on the same page can't clear."
      );
    }, CAMERA_MIC_TIMEOUT_MS);

    try {
      const stream = await navigator.mediaDevices.getUserMedia({ video: true, audio: true });
      // A stale result: either a newer testCameraMic() call has since
      // started (myToken no longer current), the watchdog above already
      // gave up and moved the row to "error", or the page is gone.
      if (cameraMicTestTokenRef.current !== myToken || unmountedRef.current) {
        stream.getTracks().forEach((t) => t.stop());
        return;
      }
      clearTimeout(cameraMicTimeoutRef.current);
      cameraMicTimeoutRef.current = null;
      previewStreamRef.current = stream;
      // NOT attached to the <video> here -- that element is rendered conditionally on
      // cameraMicStatus === "ok", so at this exact moment (status is still "testing") it
      // doesn't exist and previewVideoRef is null.
      setCameraMicStatus("ok");

      // Live level bar so the student can see the mic is actually
      // picking up sound, not just that permission was granted.
      const audioCtx = new (window.AudioContext || window.webkitAudioContext)();
      micAudioCtxRef.current = audioCtx;
      const source = audioCtx.createMediaStreamSource(stream);
      const analyser = audioCtx.createAnalyser();
      analyser.fftSize = 512;
      source.connect(analyser);
      const data = new Uint8Array(analyser.frequencyBinCount);
      micMeterIntervalRef.current = setInterval(() => {
        analyser.getByteFrequencyData(data);
        const avg = data.reduce((sum, v) => sum + v, 0) / data.length;
        setMicLevel(Math.min(100, Math.round((avg / 160) * 100)));
      }, 120);
    } catch (err) {
      if (cameraMicTestTokenRef.current !== myToken) return; // superseded by a newer attempt or the watchdog
      if (cameraMicTimeoutRef.current) {
        clearTimeout(cameraMicTimeoutRef.current);
        cameraMicTimeoutRef.current = null;
      }
      setCameraMicStatus("error");
      setCameraMicError(err?.message || "Camera and microphone access was denied or unavailable.");
    }
  }

  // ---------- live face verification (pre-exam) ----------
  // Captures one frame from the working camera preview and checks it against the student's
  // registered face profile.
  async function verifyFaceNow() {
    const video = previewVideoRef.current;
    if (!video || !video.videoWidth) {
      setFaceMatchStatus("error");
      setFaceMatchError("Camera preview isn't ready yet. Wait a moment and try again.");
      return;
    }
    const myToken = ++faceMatchTokenRef.current;
    setFaceMatchStatus("checking");
    setFaceMatchError("");
    try {
      // Downscaled for the same reason lib/proctoring.js does it.
      const frame = captureVideoFrame(video, 640, 0.8);
      const result = await Api.post("/proctoring/face/verify", { image_base64: frame });
      if (faceMatchTokenRef.current !== myToken) return; // superseded by a newer attempt
      // Signal switched off or unavailable -- see FACE_MATCHING_ENABLED.
      if (result.available === false) {
        setFaceMatchStatus("ok");
        setFaceMatchError("");
        return;
      }
      if (result.spoof_suspected) {
        setFaceMatchStatus("mismatch");
        setFaceMatchError("This looks like a photo or screen rather than a live camera feed. Use your own live camera.");
      } else if (result.face_count === 0) {
        setFaceMatchStatus("mismatch");
        setFaceMatchError("No face detected. Make sure your face is clearly visible and well lit.");
      } else if (result.face_count > 1) {
        setFaceMatchStatus("mismatch");
        setFaceMatchError("More than one face detected. Only you should be in frame.");
      } else if (result.match === true) {
        setFaceMatchStatus("ok");
      } else if (result.match === false) {
        setFaceMatchStatus("mismatch");
        setFaceMatchError("This face doesn't match your registered profile. If this is you, try better lighting; otherwise this account's exam cannot proceed.");
      } else {
        // match is null/undefined: the check was inconclusive (e.g. no usable
        // stored profile), not a confirmed pass -- never treat that as "ok".
        setFaceMatchStatus("error");
        setFaceMatchError(result.message || "Couldn't verify your face from this frame. Try again.");
      }
    } catch (err) {
      if (faceMatchTokenRef.current !== myToken) return;
      setFaceMatchStatus("error");
      setFaceMatchError(err?.message || "Face verification failed. Try again.");
    }
  }

  // Attach the preview stream once the <video> element it
  // belongs to has actually been committed to the DOM.
  useEffect(() => {
    if (cameraMicStatus !== "ok") return;
    if (previewVideoRef.current && previewStreamRef.current) {
      previewVideoRef.current.srcObject = previewStreamRef.current;
    }
  }, [cameraMicStatus]);

  // Auto-run the live face-match check the moment the camera is confirmed working and we know
  // this exam actually requires it.
  useEffect(() => {
    if (cameraMicStatus !== "ok") return;
    if (examMeta?.proctoring_enabled === false) return;
    if (!identityStatus || !identityStatus.exam_ready) return;
    if (faceMatchStatus !== "idle") return;
    const timeoutId = setTimeout(() => verifyFaceNow(), 400); // let the <video> paint a frame first
    return () => clearTimeout(timeoutId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cameraMicStatus, examMeta, identityStatus, faceMatchStatus]);

  // ---------- system check: external monitor ----------
  // Same Window Management API check proctoring.js polls every 30s during the exam itself (see
  // watchExternalMonitor there) -- run once here so a second display is flagged before the
  // student even starts, not the first time it costs them a violation mid-exam.
  async function checkExternalMonitor() {
    if (typeof window.getScreenDetails !== "function") {
      setExternalMonitorStatus("unavailable");
      return;
    }
    try {
      if (navigator.permissions?.query) {
        const status = await navigator.permissions.query({ name: "window-management" });
        if (status.state === "denied") {
          setExternalMonitorStatus("unavailable");
          return;
        }
      }
      const screenDetails = await window.getScreenDetails();
      const count = screenDetails.screens?.length || 1;
      setExternalMonitorCount(count);
      setExternalMonitorStatus(count > 1 ? "warning" : "ok");
    } catch {
      setExternalMonitorStatus("unavailable");
    }
  }

  // Runs once on arrival at the system-check screen.
  useEffect(() => {
    if (phase !== "precheck") return;
    checkExternalMonitor();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [phase]);

  // Exam overview (title, subject/description, duration, question count, marks, schedule, pass
  // criteria) and identity-verification status for the redesigned pre-exam page.
  useEffect(() => {
    if (phase !== "precheck") return;
    let cancelled = false;
    (async () => {
      try {
        const exams = await Api.get("/exams/available");
        if (cancelled) return;
        const match = exams.find((e) => String(e.id) === String(examId));
        if (match) setExamMeta(match);
      } catch {
        // non-fatal -- the overview panel just renders without exam details
      }
    })();
    Api.get("/proctoring/identity/status")
      .then((res) => {
        if (!cancelled) setIdentityStatus(res);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [phase, examId]);

  // The AI proctoring controller requests its own camera/mic stream once the exam is actually
  // active (see the effect above that creates it).
  useEffect(() => {
    if (phase === "active") stopCameraMicPreview();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [phase]);

  // Overlay's "Return to Exam" button. Must stay synchronous with the click
  // for the same user-activation reason as requestFullscreenNow.
  function resumeFromBreach() {
    if (lockdownRef.current) lockdownRef.current.resume();
    else {
      requestFullscreenNow();
      setLockdownBreach(null);
    }
  }

  // beforeunload is registered by the lockdown controller for the active
  // phase, so there is no separate handler here anymore.

  // ---------- cleanup on unmount ----------
  useEffect(() => {
    // Load-bearing, and the cause of a genuinely nasty bug when it was missing.
    unmountedRef.current = false;
    return () => {
      // Set before anything else: beginScreenShare()'s getDisplayMedia promise
      // can still be pending (waiting on the native share picker) when the
      // student navigates away or the exam auto-submits, and its .then() checks
      // this to stop a stream that arrived too late instead of leaking it.
      unmountedRef.current = true;
      stopTimer();
      if (resyncIntervalRef.current) clearInterval(resyncIntervalRef.current);
      if (codeSaveTimeoutRef.current) clearTimeout(codeSaveTimeoutRef.current);
      autosave.stop();
      // The attempt token is worth nothing once this page is gone, and a token
      // for a live exam is exactly what should not linger in a module variable
      // on a shared examination-hall machine.
      setAttemptToken(null);
      if (proctoringRef.current) proctoringRef.current.stop();
      // Stops the screen-share stream's tracks too (see lockdown.js's stop()).
      if (lockdownRef.current) lockdownRef.current.stop();
      // Drains any still-queued violations rather than losing them on
      // navigation away from the exam page.
      if (eventLoggerRef.current) eventLoggerRef.current.stop();
      // The system-check screen's own camera/mic preview, if the student
      // left before ever reaching the active phase.
      stopCameraMicPreview();
      // A screen-share stream granted during the system check is normally handed off to (and
      // stopped by) the lockdown controller once the exam goes active.
      if (screenStreamRef.current) {
        screenStreamRef.current.getTracks().forEach((t) => t.stop());
        screenStreamRef.current = null;
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ==================================================================

  // auth guard, evaluated after all hooks -- see the comment at the top of
  // this component for why it can't run before them.
  if (!isLoggedIn()) return <RedirectToLogin />;
  if (getRole() !== "student") return <Navigate to="/dashboard" replace />;

  if (phase === "precheck") {
    // Screen sharing must be granted (or declined) BEFORE fullscreen is ever requested, not the
    // other way around and not both at once.
    const identityOk = examMeta?.proctoring_enabled === false || !identityStatus || identityStatus.exam_ready;
    // Once a profile exists, actually starting still requires the
    // live face check above to have come back a confirmed match.
    const faceVerifiedOk = examMeta?.proctoring_enabled === false || !identityStatus || !identityStatus.exam_ready || faceMatchStatus === "ok";
    // Only what this exam asks for. Requiring all four unconditionally meant a candidate could
    // not start an unproctored quiz without sharing their screen.
    const cameraOk = !(requires.camera || requires.microphone) || cameraMicStatus === "ok";
    const screenOk = !requires.screen_share || screenShareActive;
    const fullscreenOk = !requires.fullscreen || fullscreenActive;
    const canBegin = cameraOk && screenOk && fullscreenOk && identityOk && faceVerifiedOk;
    const isChromiumBased = /Chrome\/|Edg\//.test(navigator.userAgent) && !/Firefox|OPR\//.test(navigator.userAgent);

    const durationLabel = examMeta ? `${examMeta.duration_minutes} min` : null;
    const windowLabel = examMeta?.start_time || examMeta?.end_time
      ? `${examMeta.start_time ? new Date(examMeta.start_time).toLocaleString() : "Anytime"} — ${examMeta.end_time ? new Date(examMeta.end_time).toLocaleString() : "No fixed end"}`
      : "No fixed schedule";

    return (
      <div className="min-h-screen flex items-center justify-center bg-page text-ink px-5 py-10">
        <div className="w-full max-w-3xl rounded-2xl border border-border bg-card p-6 sm:p-8 animate-fade-in shadow-sm">
          <div className="text-center">
            <span className="inline-flex items-center justify-center w-14 h-14 rounded-2xl bg-primary/10 text-primary mb-5">
              <Icon name="shield" width={24} height={24} />
            </span>
            <h1 className="text-xl font-extrabold tracking-tight mb-1">{examMeta?.title || "Pre-Exam Overview"}</h1>
            {examMeta?.description && <p className="text-sm text-muted leading-relaxed mb-3 max-w-lg mx-auto">{examMeta.description}</p>}
            <p className="text-sm text-muted leading-relaxed mb-6 max-w-md mx-auto">
              Review the details below, then work through the system check. Once every required item is confirmed,
              click Begin Exam -- the timer starts immediately at that point.
            </p>
          </div>

          {/* ---------- exam overview ---------- */}
          <div className="grid sm:grid-cols-2 gap-3 mb-6">
            <OverviewFact icon="doc" label="Candidate" value={getName() || "-"} />
            <OverviewFact icon="clock" label="Duration" value={durationLabel || "-"} />
            <OverviewFact icon="layout" label="Questions" value={examMeta ? String(examMeta.question_count) : "-"} />
            <OverviewFact icon="chart" label="Total marks" value={examMeta ? String(examMeta.exam_total_marks) : "-"} />
            <OverviewFact icon="check" label="Passing criteria" value={examMeta ? `${examMeta.pass_percentage}% or higher` : "-"} />
            <OverviewFact icon="wifi" label="Exam window" value={windowLabel} small />
          </div>

          {/* The examiner's OWN instructions for this exam.
              exam.instructions has a column, an edit field, and is carried all
              the way through /exams/available -- and this page showed only the
              generic platform rules below, so anything an examiner wrote for
              their candidates was stored and never delivered. Shown first, and
              visually distinct, because it is the part that is specific to the
              paper in front of them. */}
          {examMeta?.instructions && (
            <div className="mb-4 rounded-xl border border-primary/25 bg-primary/5 p-4 text-left">
              <div className="text-xs font-semibold text-ink mb-1.5 inline-flex items-center gap-1.5">
                <Icon name="alert" width={13} height={13} className="text-primary" />
                From your examiner
              </div>
              <p className="text-sm text-ink leading-relaxed whitespace-pre-wrap">{examMeta.instructions}</p>
            </div>
          )}

          <div className="mb-6 rounded-xl border border-border bg-page/60 p-4 text-left grid sm:grid-cols-2 gap-4">
            <div>
              <div className="text-xs font-semibold text-ink mb-2">Instructions &amp; exam rules</div>
              <ul className="space-y-1.5 text-xs text-muted leading-relaxed list-disc list-inside">
                <li>Stay in fullscreen with your entire screen shared for the whole exam.</li>
                <li>Don&apos;t switch tabs, minimise the window, or open other applications.</li>
                <li>Only you should be visible and audible -- no other people, phones, books, or notes in frame.</li>
                <li>Leaving fullscreen, switching tabs, or stopping screen sharing counts as a warning; enough
                  warnings end the exam automatically and submit what you have so far.</li>
                <li>Once submitted (manually or automatically when time expires), answers cannot be changed.</li>
              </ul>
              <div className="text-xs font-semibold text-ink mt-3 mb-1.5">Allowed / prohibited</div>
              <ul className="space-y-1.5 text-xs text-muted leading-relaxed list-disc list-inside">
                <li>Allowed: a quiet, well-lit room and (for coding questions) the built-in code editor and sample-test &quot;Run&quot; button.</li>
                <li>Prohibited: other people in frame, phones or smart devices, books or notes, a second monitor, and any external help.</li>
              </ul>
            </div>
            <div>
              <div className="text-xs font-semibold text-ink mb-2">AI proctoring &amp; privacy</div>
              <ul className="space-y-1.5 text-xs text-muted leading-relaxed list-disc list-inside">
                <li>{examMeta?.proctoring_enabled === false ? "AI proctoring is not enabled for this exam." : "Your camera, microphone, and screen are monitored throughout for face presence, gaze, audio, and screen-share/fullscreen compliance."}</li>
                <li>Flagged moments are logged as violations attached to your attempt, visible to your examiner.</li>
                <li>Recordings and captures are used solely for exam-integrity review and are not shared outside your institution.</li>
                <li>Enough violations auto-submit your exam early -- see &quot;Instructions&quot; for the warning limit.</li>
              </ul>
            </div>
          </div>

          {precheckError && (
            <div className="mb-5 rounded-xl border border-red-500/30 bg-red-500/10 text-red-500 text-sm font-medium px-4 py-3 text-left">
              {precheckError}
              {/* Matches the wording of identity_service.require_exam_ready,
                  which names the outstanding step(s) rather than a generic
                  "face profile" message. */}
              {/verification|register your face|verify your id/i.test(precheckError) && (
                <>
                  {" "}
                  <Link to="/profile" className="underline font-semibold">
                    Go to Profile
                  </Link>
                </>
              )}
            </div>
          )}

          <div className="space-y-3 mb-6">
            <SystemCheckRow
              icon="wifi"
              label="Browser"
              description="Chrome or Edge give the most reliable screen-sharing and lockdown behaviour."
              status={isChromiumBased ? "ok" : "warning"}
              statusText={isChromiumBased ? "Supported" : "Not fully supported"}
            />

            <SystemCheckRow
              icon="wifi"
              label="Internet connection"
              description={networkQuality ? `Connection quality: ${networkQuality.label}.` : undefined}
              status={!navigator.onLine ? "error" : networkQuality?.label === "Poor" ? "warning" : "ok"}
              statusText={!navigator.onLine ? "Offline" : networkQuality?.label === "Poor" ? "Weak signal" : "Connected"}
            />

            <SystemCheckRow
              icon="shield-check"
              label="Face verification"
              description={
                examMeta?.proctoring_enabled === false
                  ? "This exam does not require AI proctoring."
                  : !identityStatus || !identityStatus.exam_ready
                    ? "Complete one-time face registration and ID verification on your Profile page first."
                    : cameraMicStatus !== "ok"
                      ? "Get the camera check above working, then your live face is checked against your registered profile."
                      : "Confirms the face in front of the camera right now matches your registered profile -- not just that a profile exists."
              }
              status={
                examMeta?.proctoring_enabled === false
                  ? "ok"
                  : !identityStatus
                    ? "idle"
                    : !identityStatus.exam_ready
                      ? "error"
                      : cameraMicStatus !== "ok"
                        ? "idle"
                        : { idle: "idle", checking: "testing", ok: "ok", mismatch: "error", error: "warning" }[faceMatchStatus]
              }
              statusText={
                examMeta?.proctoring_enabled === false
                  ? "Not required"
                  : !identityStatus
                    ? "Checking…"
                    : !identityStatus.exam_ready
                      ? "Action needed"
                      : cameraMicStatus !== "ok"
                        ? "Waiting for camera"
                        : { idle: "Not verified yet", checking: "Verifying…", ok: "Face matched", mismatch: "Mismatch", error: "Couldn't verify" }[faceMatchStatus]
              }
              action={
                examMeta?.proctoring_enabled !== false && identityStatus && !identityStatus.exam_ready ? (
                  <Link to="/profile" className={btnGhost.replace("px-5 py-3", "px-4 py-2")}>
                    <Icon name="shield-check" width={14} height={14} />
                    Verify Now
                  </Link>
                ) : examMeta?.proctoring_enabled !== false && identityStatus?.exam_ready && cameraMicStatus === "ok" ? (
                  <button
                    type="button"
                    onClick={verifyFaceNow}
                    disabled={faceMatchStatus === "checking"}
                    className={`${btnGhost.replace("px-5 py-3", "px-4 py-2")} disabled:opacity-60`}
                  >
                    <Icon name="shield-check" width={14} height={14} />
                    {faceMatchStatus === "checking" ? "Verifying…" : faceMatchStatus === "ok" ? "Verify again" : "Verify my face"}
                  </button>
                ) : undefined
              }
            >
              {(faceMatchStatus === "mismatch" || faceMatchStatus === "error") && faceMatchError && (
                <p className="text-xs text-red-500 leading-relaxed">{faceMatchError}</p>
              )}
            </SystemCheckRow>

            {(requires.camera || requires.microphone) && (
            <SystemCheckRow
              icon="camera"
              label={requires.microphone ? "Camera & microphone" : "Camera"}
              description="Used to verify it's you throughout the exam. Required to continue."
              status={cameraMicStatus}
              statusText={
                { idle: "Not tested", testing: "Requesting access…", ok: "Working", error: "Failed" }[cameraMicStatus]
              }
              action={
                <button type="button" onClick={testCameraMic} disabled={cameraMicStatus === "testing"} className={`${btnGhost.replace("px-5 py-3", "px-4 py-2")} disabled:opacity-60`}>
                  <Icon name="camera" width={14} height={14} />
                  {cameraMicStatus === "testing" ? "Requesting…" : cameraMicStatus === "ok" ? "Test again" : "Test camera & mic"}
                </button>
              }
            >
              {cameraMicStatus === "ok" && (
                <div className="flex items-center gap-3">
                  <video
                    ref={previewVideoRef}
                    autoPlay
                    muted
                    playsInline
                    className="w-24 h-16 rounded-lg bg-black object-cover border border-border"
                  />
                  <div className="flex-1">
                    <div className="text-[11px] text-muted mb-1">Microphone level</div>
                    <div className="h-2 rounded-full bg-border overflow-hidden">
                      <div
                        className="h-full bg-emerald-500 transition-[width] duration-100"
                        style={{ width: `${micLevel}%` }}
                      />
                    </div>
                  </div>
                </div>
              )}
              {cameraMicStatus === "error" && cameraMicError && (
                <p className="text-xs text-red-500 leading-relaxed">{cameraMicError}</p>
              )}
            </SystemCheckRow>
            )}

            <SystemCheckRow
              icon="monitor"
              label="External displays"
              description="For exams that require a single display."
              status={externalMonitorStatus}
              statusText={
                {
                  checking: "Checking…",
                  ok: "Single display",
                  warning: `${externalMonitorCount} displays detected`,
                  unavailable: "Couldn't check",
                }[externalMonitorStatus]
              }
            />

            {/* Each row appears only if this exam asks for it. Showing an
                unskippable "share your entire screen" step on an unproctored
                quiz is an intrusion the exam never called for. */}
            {requires.screen_share && (
            <SystemCheckRow
              icon="monitor"
              label="Screen sharing"
              description="Share your entire screen, not a single window or browser tab. Required to continue."
              status={screenShareActive ? "ok" : screenShareError ? "error" : "idle"}
              statusText={screenShareActive ? "Sharing" : screenShareError ? "Failed" : "Not started"}
              action={
                <button type="button" onClick={beginScreenShare} className={btnGhost.replace("px-5 py-3", "px-4 py-2")}>
                  <Icon name="monitor" width={14} height={14} />
                  {screenShareActive ? "Re-share screen" : screenShareError ? "Try again" : "Share entire screen"}
                </button>
              }
            >
              {screenShareError && <p className="text-xs text-red-500 leading-relaxed">{screenShareError}</p>}
            </SystemCheckRow>
            )}

            {requires.fullscreen && (
            <SystemCheckRow
              icon="maximize"
              label="Fullscreen"
              description={
                !requires.screen_share || screenShareActive
                  ? "Required to start the exam."
                  : "Share your screen first -- entering fullscreen before that can knock you back out of it."
              }
              status={fullscreenActive ? "ok" : fullscreenError ? "error" : "idle"}
              statusText={fullscreenActive ? "Active" : fullscreenError ? "Failed" : "Not entered"}
              action={
                <button
                  type="button"
                  onClick={requestFullscreenNow}
                  disabled={requires.screen_share && !screenShareActive}
                  className={`${btnGhost.replace("px-5 py-3", "px-4 py-2")} disabled:opacity-50`}
                >
                  <Icon name="maximize" width={14} height={14} />
                  Enter fullscreen
                </button>
              }
            >
              {fullscreenError && <p className="text-xs text-red-500 leading-relaxed">{fullscreenError}</p>}
            </SystemCheckRow>
            )}
          </div>

          <button
            onClick={() => beginExam()}
            disabled={beginning || !canBegin}
            className={`${btnPrimary} w-full justify-center disabled:opacity-60`}
          >
            {beginning ? "Starting…" : "Begin Exam"}
          </button>
          {!canBegin && !beginning && (
            <p className="mt-2 text-xs text-muted text-center">
              {!identityOk
                ? "Complete face & ID verification from your Profile page to continue."
                : !faceVerifiedOk
                  ? "Your live face verification must come back as a match before you can begin."
                  : "Complete the camera & microphone, screen sharing, and fullscreen checks above to continue."}
            </p>
          )}
          <div className="text-center">
            <button onClick={() => navigate("/dashboard")} className="mt-3 text-sm text-muted hover:text-ink transition-colors">
              Back to Dashboard
            </button>
          </div>
        </div>
      </div>
    );
  }

  const overlayActive = Boolean(lockdownBreach || lockdownTerminated);
  // Once a submit (manual or auto-expiry) is in flight,
  // the exam is over in every way that matters.
  const contentInert = overlayActive || submitting;

  return (
    <>
      {/* Lockdown overlay is a sibling of the exam content, not a child of
          it, so it sits outside whatever `inert` is applied to below. That
          keeps it focusable/interactive while genuinely blocking the exam:
          `inert` on the content div stops Tab and screen-reader virtual
          cursors from reaching it, and the overlay's own focus trap (see
          LockdownOverlay) stops Tab from ever leaving the overlay in the
          first place. Kept mounted (not unmounted) so no in-progress answer
          is lost. */}
      {overlayActive && (
        <LockdownOverlay
          terminated={lockdownTerminated}
          reason={lockdownBreach}
          strikes={strikeState.strikes}
          limit={strikeState.limit}
          onResume={resumeFromBreach}
          onViewResult={() => navigate(`/results/${attemptIdRef.current}`, { replace: true })}
        />
      )}
      {/* `expiredPendingSubmission` keeps this up even if `submitting` were
          somehow cleared. Past the deadline the overlay must never come down
          on its own -- that is what let a candidate carry on answering once
          the submission's retries were exhausted. */}
      {!overlayActive && (submitting || expiredPendingSubmission) && (
        <SubmittingOverlay auto={submitReason === "auto"} stillRetrying={Boolean(connectionBanner) && expiredPendingSubmission} />
      )}
      <div
        {...(contentInert ? { inert: "" } : {})}
        className="min-h-screen flex flex-col bg-page text-ink"
      >
      {connectionBanner && (
        <div className="sticky top-0 z-40 flex items-center justify-center gap-2 bg-amber-500 text-white text-xs font-semibold py-2 px-4">
          <Icon name="alert" width={14} height={14} />
          <span>{connectionBanner}</span>
        </div>
      )}

      <ToastStack toasts={toasts} bannerActive={Boolean(connectionBanner)} />

      {/* header */}
      {/*
        flex-wrap, and the timer/Submit group can no longer shrink.

        Everything sat in one non-wrapping row: title, candidate name,
        fullscreen state, network and battery readouts, save status, timer and
        the Submit button. On a phone that meant the two things a candidate
        actually needs -- how long is left, and how to hand in -- were the ones
        being squeezed, because they are last in the source order. Wrapping puts
        them on their own line instead of compressing them.
      */}
      <header className="sticky top-0 z-30 flex flex-wrap items-center justify-between gap-x-3 gap-y-2 border-b border-border bg-surface/95 backdrop-blur px-4 sm:px-6 py-3 shadow-sm">
        <div className="flex items-center gap-3 min-w-0">
          <span className="hidden sm:flex h-9 w-9 flex-none items-center justify-center rounded-lg bg-primary/10 text-primary font-bold text-sm">
            AI
          </span>
          <div className="min-w-0">
            <div className="font-semibold text-sm sm:text-base truncate max-w-[38vw] sm:max-w-xs">{examTitle}</div>
            <div className="flex items-center gap-2 text-[11px] sm:text-xs text-muted">
              <span>{getName()}</span>
              <span className="text-border">&bull;</span>
              {fullscreenActive ? (
                <span className="inline-flex items-center gap-1 font-medium text-emerald-500">
                  <Icon name="maximize" width={12} height={12} />
                  Fullscreen
                </span>
              ) : (
                // The browser can reject the automatic fullscreen request for reasons outside
                // our control (a slow start-attempt call, a permissions policy, etc.), so this
                // stays a real button the student can click themselves.
                <button
                  type="button"
                  onClick={requestFullscreenNow}
                  className="inline-flex items-center gap-1 font-medium text-amber-500 hover:text-amber-600 underline decoration-dotted underline-offset-2"
                >
                  <Icon name="maximize" width={12} height={12} />
                  Fullscreen pending — click to enable
                </button>
              )}
              <span className="text-border">&bull;</span>
              {screenShareActive ? (
                <span className="inline-flex items-center gap-1 font-medium text-emerald-500">
                  <Icon name="monitor" width={12} height={12} />
                  Screen sharing
                </span>
              ) : (
                // Same reasoning as the fullscreen retry above: getDisplayMedia()
                // also needs a fresh user gesture, so this stays a real button
                // rather than something retried automatically in the background.
                <button
                  type="button"
                  onClick={beginScreenShare}
                  className="inline-flex items-center gap-1 font-medium text-amber-500 hover:text-amber-600 underline decoration-dotted underline-offset-2"
                >
                  <Icon name="monitor" width={12} height={12} />
                  Screen sharing off — click to enable
                </button>
              )}
              {/* Strike budget is always visible, not just when the overlay
                  is up -- a student should never be surprised by how close
                  they are to an automatic termination. */}
              {strikeState.strikes > 0 && (
                <>
                  <span className="text-border">&bull;</span>
                  <span
                    className={`inline-flex items-center gap-1 font-semibold ${
                      strikeState.limit - strikeState.strikes <= 1 ? "text-red-500" : "text-amber-500"
                    }`}
                  >
                    <Icon name="alert" width={12} height={12} />
                    Warning {strikeState.strikes}/{strikeState.limit}
                  </span>
                </>
              )}
              {proctoringEnabled && (
                <>
                  <span className="text-border">&bull;</span>
                  <span className="text-muted">
                    {violationCount} flag{violationCount === 1 ? "" : "s"}
                  </span>
                </>
              )}
            </div>
          </div>
        </div>
        <div className="flex items-center gap-3 sm:gap-4 shrink-0">
          {/* Hidden below sm: on a narrow phone screen the timer and Submit
              button are the only things a student truly needs at a glance,
              same reasoning as the AI logo badge above being sm-and-up only.
              Each badge independently no-ops to null on Firefox/Safari, so
              this whole group silently disappears there rather than showing
              a half-populated row. */}
          {(networkQuality || batteryStatus) && (
            <div className="hidden sm:flex items-center gap-3 pr-3 mr-1 border-r border-border text-xs">
              {networkQuality && (
                <span
                  className={`inline-flex items-center gap-1 font-medium ${
                    networkQuality.label === "Good"
                      ? "text-emerald-500"
                      : networkQuality.label === "Fair"
                        ? "text-amber-500"
                        : "text-red-500"
                  }`}
                  title={`Connection quality: ${networkQuality.label}`}
                >
                  <Icon name="wifi" width={14} height={14} />
                  {networkQuality.label}
                </span>
              )}
              {batteryStatus && (
                <span
                  className={`inline-flex items-center gap-1 font-medium ${
                    batteryStatus.charging || batteryStatus.level > 0.5
                      ? "text-emerald-500"
                      : batteryStatus.level > 0.2
                        ? "text-amber-500"
                        : "text-red-500"
                  }`}
                  title={batteryStatus.charging ? "Battery charging" : "Battery level"}
                >
                  <Icon name={batteryStatus.charging ? "zap" : "battery"} width={14} height={14} />
                  {Math.round(batteryStatus.level * 100)}%
                </span>
              )}
            </div>
          )}
          <SaveStatus summary={saveSummary} onRetry={() => autosave.retryAll()} />
          <span className={`inline-flex items-center gap-1.5 font-mono font-bold text-base sm:text-lg shrink-0 tabular-nums ${timerDanger ? "text-red-500 exam-timer-danger" : "text-ink"}`}>
            <Icon name="clock" width={17} height={17} />
            {timerText}
          </span>
          <button
            onClick={() => setShowSubmitConfirm(true)}
            disabled={expiredPendingSubmission || submitting}
            className={`${btnPrimary.replace("px-6 py-3.5", "px-4 py-2.5")} shrink-0 whitespace-nowrap disabled:opacity-50 disabled:cursor-not-allowed`}
          >
            {/* "Submit" alone on a phone. The word "Exam" adds nothing a
                candidate sitting one does not already know, and keeping it was
                costing the button width it needed more. (No `xs:` breakpoint --
                Tailwind's defaults start at `sm`, and inventing one for a single
                word is not worth a config change.) */}
            Submit<span className="hidden sm:inline"> Exam</span>
          </button>
        </div>
      </header>

      {/* body */}
      <main className="flex-1 grid grid-cols-1 lg:grid-cols-[220px_1fr_300px] gap-5 max-w-[1600px] w-full mx-auto px-4 sm:px-6 py-5">
        {/* question navigator */}
        <QuestionNavigator
          questionIds={questionIds}
          currentIndex={currentIndex}
          answeredMap={answeredMap}
          markedSet={markedSet}
          onGo={goToIndex}
        />

        {/* question content */}
        <section className="order-1 lg:order-2 rounded-2xl border border-border bg-card p-5 sm:p-6 min-h-[420px] flex flex-col">
          {questionLoading && <QuestionSkeleton />}
          {!questionLoading && questionError && (
            /* A message and nothing else left the candidate stuck on a blank
               panel mid-exam with their clock running. Both ways out are here:
               try this question again, or go back to one that loaded. */
            <div className="m-auto text-center" role="alert">
              <p className="text-sm text-red-500 font-medium mb-3">
                Couldn&apos;t load question {currentIndex + 1}. {questionError}
              </p>
              <div className="flex items-center justify-center gap-2">
                <button type="button" onClick={() => loadQuestion(currentIndex)}
                        className={`${btnPrimary.replace("px-6 py-3.5", "px-4 py-2")} text-sm`}>
                  Try again
                </button>
                {currentIndex > 0 && (
                  <button type="button" onClick={() => goToIndex(currentIndex - 1)}
                          className={`${btnGhost} px-4 py-2 text-sm`}>
                    Back to question {currentIndex}
                  </button>
                )}
              </div>
            </div>
          )}
          {!questionLoading && !questionError && currentQuestion && (
            <>
              <div className="flex items-start justify-between gap-3 mb-5">
                <div>
                  <span className="text-xs font-bold uppercase tracking-wide text-primary">
                    Question {currentIndex + 1} of {questionIds.length}
                  </span>
                  <p className="text-base sm:text-lg font-semibold text-ink mt-1 leading-relaxed whitespace-pre-wrap">{currentQuestion.text}</p>
                </div>
                <span className="shrink-0 text-xs font-semibold text-muted bg-page border border-border rounded-full px-3 py-1">
                  {currentQuestion.marks} mark{currentQuestion.marks === 1 ? "" : "s"}
                </span>
              </div>

              {currentQuestion.question_type === "mcq" ? (
                /* role="radiogroup" + role="radio", not plain buttons. */
                <div className="space-y-2.5 flex-1" role="radiogroup"
                     aria-label={`Answer choices for question ${currentIndex + 1}`}>
                  {currentQuestion.options.map((opt, i) => {
                    const selected = currentQuestion.selected_option_id === opt.id;
                    return (
                      <button
                        key={opt.id}
                        role="radio"
                        aria-checked={selected}
                        // Only the selected option (or the first, when
                        // nothing is chosen) is in the tab order.
                        tabIndex={selected || (currentQuestion.selected_option_id == null && i === 0) ? 0 : -1}
                        onKeyDown={(e) => handleOptionKeys(e, i, currentQuestion.options, selectOption)}
                        onClick={() => selectOption(opt.id)}
                        className={`w-full text-left flex items-center gap-3 rounded-xl border px-4 py-3.5 text-sm transition-colors ${
                          selected ? "border-primary bg-primary/5 font-semibold text-ink" : "border-border hover:border-primary/50 text-ink"
                        }`}
                      >
                        <span
                          className={`shrink-0 inline-flex items-center justify-center w-6 h-6 rounded-full border text-[11px] font-bold ${
                            selected ? "border-primary bg-primary text-white" : "border-border text-muted"
                          }`}
                        >
                          {String.fromCharCode(65 + i)}
                        </span>
                        {opt.text}
                      </button>
                    );
                  })}
                </div>
              ) : currentQuestion.question_type === "multi_select" ? (
                <div className="space-y-2.5 flex-1">
                  <p className="text-xs font-medium text-muted -mt-1 mb-1">Select all options that apply.</p>
                  {currentQuestion.options.map((opt, i) => {
                    const selected = (currentQuestion.selected_option_ids || []).includes(opt.id);
                    return (
                      <button
                        key={opt.id}
                        onClick={() => toggleMultiOption(opt.id)}
                        className={`w-full text-left flex items-center gap-3 rounded-xl border px-4 py-3.5 text-sm transition-colors ${
                          selected ? "border-primary bg-primary/5 font-semibold text-ink" : "border-border hover:border-primary/50 text-ink"
                        }`}
                      >
                        <span
                          className={`shrink-0 inline-flex items-center justify-center w-6 h-6 rounded border text-[11px] font-bold ${
                            selected ? "border-primary bg-primary text-white" : "border-border text-muted"
                          }`}
                        >
                          {selected ? <Icon name="check" width={13} height={13} /> : String.fromCharCode(65 + i)}
                        </span>
                        {opt.text}
                      </button>
                    );
                  })}
                </div>
              ) : (
                <CodingPanel
                  question={currentQuestion}
                  editorKey={codeEditorKey}
                  initialValue={codeValueRef.current}
                  onChange={onCodeChange}
                  onRun={runSample}
                  onReset={resetCode}
                  running={running}
                  runResult={runResult}
                />
              )}

              <div className="flex items-center justify-between gap-3 mt-6 pt-5 border-t border-border">
                <button onClick={() => goToIndex(currentIndex - 1)} disabled={currentIndex === 0} className={`${btnGhost.replace("px-5 py-3", "px-4 py-2.5")} disabled:opacity-40`}>
                  <Icon name="chevron-left" width={16} height={16} />
                  Previous
                </button>
                <div className="flex items-center gap-2">
                  {currentQuestion.question_type === "mcq" && (
                    <button onClick={clearResponse} className={`${btnGhost.replace("px-5 py-3", "px-4 py-2.5")}`}>
                      Clear
                    </button>
                  )}
                  {currentQuestion.question_type === "multi_select" && (
                    <button onClick={clearMultiResponse} className={`${btnGhost.replace("px-5 py-3", "px-4 py-2.5")}`}>
                      Clear
                    </button>
                  )}
                  <button
                    onClick={toggleMark}
                    className={`inline-flex items-center gap-1.5 rounded-full px-4 py-2.5 text-sm font-semibold border transition-colors ${
                      markedSet.has(currentQuestion.question_id)
                        ? "border-amber-500 text-amber-500 bg-amber-500/10"
                        : "border-border text-muted hover:text-ink"
                    }`}
                  >
                    <Icon name="flag" width={15} height={15} />
                    {markedSet.has(currentQuestion.question_id) ? "Marked" : "Mark for review"}
                  </button>
                </div>
                <button
                  onClick={() => goToIndex(currentIndex + 1)}
                  disabled={currentIndex === questionIds.length - 1}
                  className={`${btnPrimary.replace("px-6 py-3.5", "px-4 py-2.5")} disabled:opacity-40`}
                >
                  Next
                </button>
              </div>
            </>
          )}
        </section>

        {/* AI proctoring panel */}
        <aside className="order-3 rounded-2xl border border-border bg-card p-4 h-fit">
          <h2 className="text-xs font-bold uppercase tracking-wide text-muted mb-3">AI Proctoring</h2>
          {proctoringEnabled ? (
            <>
              <div className="relative rounded-xl overflow-hidden bg-black aspect-video mb-3">
                <video ref={videoRef} autoPlay muted playsInline className="w-full h-full object-cover -scale-x-100" />
              </div>
              <p className={`text-xs font-semibold mb-3 ${statusText.kind === "warn" ? "text-amber-500" : statusText.kind === "ok" ? "text-emerald-500" : "text-muted"}`}>
                {statusText.text}
              </p>
              <div className="space-y-2">
                {Object.entries(SIGNAL_LABELS).map(([key, label]) => (
                  <SignalRow key={key} label={label} state={signalStates[key] || "pending"} />
                ))}
              </div>
            </>
          ) : (
            <p className="text-sm text-muted leading-relaxed">This exam is not proctored.</p>
          )}
        </aside>
      </main>

      {/* mobile submit */}
      <div className="lg:hidden sticky bottom-0 z-30 border-t border-border bg-surface px-4 py-3">
        <button
          onClick={() => setShowSubmitConfirm(true)}
          disabled={expiredPendingSubmission || submitting}
          className={`${btnPrimary} w-full justify-center disabled:opacity-50 disabled:cursor-not-allowed`}
        >
          Submit Exam
        </button>
      </div>

      {showSubmitConfirm && (
        <SubmitConfirmModal
          answeredCount={Object.values(answeredMap).filter((v) => v !== null && v !== undefined).length}
          totalCount={questionIds.length}
          flaggedCount={markedSet.size}
          saveSummary={saveSummary}
          submitting={submitting}
          error={submitError}
          onCancel={() => setShowSubmitConfirm(false)}
          onConfirm={() => doSubmit(false)}
        />
      )}
      </div>
    </>
  );
}

/**
 * "Is my work saved?" — answered continuously, next to the clock.
 */
function SaveStatus({ summary, onRetry }) {
  const { pending = 0, failed = 0, lastSavedAt } = summary || {};

  if (failed > 0) {
    return (
      <span className="inline-flex items-center gap-1.5 text-xs font-semibold text-red-500" role="status" aria-live="polite">
        <Icon name="alert" width={14} height={14} />
        <span className="hidden sm:inline">{failed} not saved</span>
        <button onClick={onRetry} className="underline underline-offset-2 hover:no-underline">Retry</button>
      </span>
    );
  }
  if (pending > 0) {
    return (
      <span className="inline-flex items-center gap-1.5 text-xs font-semibold text-amber-500" role="status" aria-live="polite">
        <span className="w-1.5 h-1.5 rounded-full bg-amber-500 animate-pulse" aria-hidden="true" />
        <span className="hidden sm:inline">Saving{pending > 1 ? ` ${pending}` : ""}…</span>
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1.5 text-xs font-semibold text-success" role="status" aria-live="polite">
      <Icon name="check" width={14} height={14} />
      <span className="hidden sm:inline">
        {lastSavedAt
          ? `Saved ${lastSavedAt.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}`
          : "All answers saved"}
      </span>
    </span>
  );
}

/**
 * The question grid, with a filter and a mobile layout.
 */
function QuestionNavigator({ questionIds, currentIndex, answeredMap, markedSet, onGo }) {
  const [filter, setFilter] = useState("all");
  const [open, setOpen] = useState(false);

  const counts = useMemo(() => {
    let answered = 0;
    let marked = 0;
    questionIds.forEach((qid) => {
      const value = answeredMap[qid];
      if (value !== null && value !== undefined) answered += 1;
      if (markedSet.has(qid)) marked += 1;
    });
    return { all: questionIds.length, answered, marked, unanswered: questionIds.length - answered };
  }, [questionIds, answeredMap, markedSet]);

  function matches(qid, idx) {
    if (idx === currentIndex) return true;
    const value = answeredMap[qid];
    const isAnswered = value !== null && value !== undefined;
    if (filter === "answered") return isAnswered;
    if (filter === "unanswered") return !isAnswered;
    if (filter === "marked") return markedSet.has(qid);
    return true;
  }

  const FILTERS = [
    ["all", "All", counts.all],
    ["unanswered", "Unanswered", counts.unanswered],
    ["marked", "Marked", counts.marked],
  ];

  const visible = questionIds.map((qid, idx) => ({ qid, idx })).filter(({ qid, idx }) => matches(qid, idx));

  return (
    <aside className="order-2 lg:order-1 rounded-2xl border border-border bg-card p-4 h-fit">
      <div className="flex items-center justify-between gap-2">
        <h2 className="text-xs font-bold uppercase tracking-wide text-muted">Questions</h2>
        <span className="text-[11px] text-muted tabular-nums">
          {counts.answered}/{counts.all} answered
        </span>
      </div>

      {/* Collapsed by default on small screens only. `lg:hidden` on the toggle
          and `lg:block` on the panel means the desktop column is never hidden
          behind a click it does not need. */}
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-controls="question-navigator-panel"
        className="lg:hidden mt-3 w-full inline-flex items-center justify-center gap-1.5 rounded-lg border border-border px-3 py-2 text-xs font-semibold text-ink"
      >
        {open ? "Hide questions" : `Show all ${counts.all} questions`}
        <Icon name="chevron-down" width={13} height={13}
              className={`transition-transform ${open ? "rotate-180" : ""}`} />
      </button>

      <div id="question-navigator-panel" className={`${open ? "block" : "hidden"} lg:block`}>
        <div role="group" aria-label="Filter questions" className="flex flex-wrap gap-1.5 mt-3 mb-3">
          {FILTERS.map(([key, label, count]) => (
            <button
              key={key}
              type="button"
              onClick={() => setFilter(key)}
              aria-pressed={filter === key}
              className={`rounded-full px-2.5 py-1 text-[11px] font-semibold border transition-colors ${
                filter === key
                  ? "border-primary bg-primary text-white"
                  : "border-border text-muted hover:border-primary/50"
              }`}
            >
              {label} <span className="tabular-nums">{count}</span>
            </button>
          ))}
        </div>

        {visible.length === 0 ? (
          <p className="py-3 text-[11px] leading-snug text-muted">
            {filter === "unanswered"
              ? "Every question has an answer saved."
              : "Nothing matches this filter."}
          </p>
        ) : (
          <div className="grid grid-cols-6 lg:grid-cols-4 gap-2">
            {visible.map(({ qid, idx }) => (
              <button
                key={qid}
                onClick={() => { onGo(idx); setOpen(false); }}
                className={dotClasses(idx, qid, currentIndex, answeredMap, markedSet)}
                title={`Question ${idx + 1}`}
                aria-current={idx === currentIndex ? "true" : undefined}
              >
                {idx + 1}
              </button>
            ))}
          </div>
        )}

        <div className="mt-4 space-y-1.5 text-[11px] text-muted">
          <LegendRow swatchClass="bg-primary text-primary" label="Answered" />
          <LegendRow swatchClass="bg-amber-500 text-amber-500" label="Marked for review" />
          <LegendRow swatchClass="border border-border text-muted" label="Unanswered" outline />
        </div>
      </div>
    </aside>
  );
}


function dotClasses(index, questionId, currentIndex, answeredMap, markedSet) {
  const base = "h-9 rounded-lg text-xs font-bold flex items-center justify-center transition-colors border";
  const isCurrent = index === currentIndex;
  const isMarked = markedSet.has(questionId);
  const answerVal = answeredMap[questionId];
  const isAnswered = answerVal !== null && answerVal !== undefined;
  let tone = "border-border text-muted hover:border-primary/50";
  if (isAnswered) tone = "border-primary bg-primary text-white";
  if (isMarked) tone = "border-amber-500 bg-amber-500/10 text-amber-500";
  const ring = isCurrent ? "ring-2 ring-offset-2 ring-primary ring-offset-card" : "";
  return `${base} ${tone} ${ring}`;
}

// Shaped like the real question layout below (label + title + marks badge, then a handful of
// option-sized rows) rather than one flat rectangle, so a slow fetch reads unmistakably as
// "loading" instead of "blank/broken".
function QuestionSkeleton() {
  return (
    <div className="flex-1 flex flex-col" aria-live="polite" aria-busy="true">
      <span className="sr-only">Loading question…</span>
      <div className="flex items-start justify-between gap-3 mb-5">
        <div className="flex-1 space-y-2.5">
          <div className="skeleton h-3 w-32 rounded-full" />
          <div className="skeleton h-5 w-3/4 rounded-md" />
        </div>
        <div className="skeleton h-6 w-16 rounded-full shrink-0" />
      </div>
      <div className="space-y-2.5 flex-1">
        {[0, 1, 2, 3].map((i) => (
          <div key={i} className="skeleton h-12 w-full rounded-xl" />
        ))}
      </div>
    </div>
  );
}

function LegendRow({ swatchClass, label, outline }) {
  return (
    <div className="flex items-center gap-2">
      <span className={`inline-block w-3 h-3 rounded ${outline ? swatchClass : swatchClass.split(" ")[0]}`} />
      <span>{label}</span>
    </div>
  );
}

function SignalRow({ label, state }) {
  // Status was previously color-only (a dot with no text), which is unreadable for colorblind
  // users and invisible to screen readers.
  const meta = {
    ok: { dot: "bg-emerald-500", text: "OK", textClass: "text-emerald-600 dark:text-emerald-400" },
    warn: { dot: "bg-amber-500", text: "Warning", textClass: "text-amber-600 dark:text-amber-400" },
    unavailable: { dot: "bg-slate-400", text: "Unavailable", textClass: "text-muted" },
    pending: { dot: "bg-slate-300 animate-pulse", text: "Checking…", textClass: "text-muted" },
  }[state] || { dot: "bg-slate-300", text: "Unknown", textClass: "text-muted" };

  return (
    <div className="flex items-center justify-between gap-2 text-xs">
      <span className="text-muted">{label}</span>
      <span className="flex items-center gap-1.5">
        <span className={`font-medium ${meta.textClass}`}>{meta.text}</span>
        <span aria-hidden="true" className={`inline-block w-2 h-2 rounded-full ${meta.dot}`} />
      </span>
    </div>
  );
}

/** One fact tile in the pre-exam overview grid (candidate, duration,
 * question count, marks, passing criteria, schedule window). Purely
 * informational -- unlike SystemCheckRow below, nothing here gates Begin
 * Exam, so there is no status/action affordance, just a label and a value. */
function OverviewFact({ icon, label, value, small }) {
  return (
    <div className="flex items-center gap-3 rounded-xl border border-border bg-page/60 px-3.5 py-2.5">
      <span className="inline-flex items-center justify-center w-8 h-8 rounded-lg bg-primary/10 text-primary shrink-0">
        <Icon name={icon} width={14} height={14} />
      </span>
      <div className="min-w-0">
        <div className="text-[11px] text-muted uppercase tracking-wide">{label}</div>
        <div className={`font-semibold text-ink truncate ${small ? "text-xs" : "text-sm"}`}>{value}</div>
      </div>
    </div>
  );
}

/**
 * One row on the precheck "System check" screen.
 */
function SystemCheckRow({ icon, label, description, status, statusText, action, children }) {
  const meta = {
    ok: { badge: "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400", dotIcon: "check" },
    warning: { badge: "bg-amber-500/10 text-amber-600 dark:text-amber-400", dotIcon: "alert" },
    error: { badge: "bg-red-500/10 text-red-500", dotIcon: "x" },
    testing: { badge: "bg-slate-400/10 text-muted", dotIcon: "spinner" },
    checking: { badge: "bg-slate-400/10 text-muted", dotIcon: "spinner" },
    unavailable: { badge: "bg-slate-400/10 text-muted", dotIcon: null },
    idle: { badge: "bg-slate-400/10 text-muted", dotIcon: null },
  }[status] || { badge: "bg-slate-400/10 text-muted", dotIcon: null };
  const spinning = status === "testing" || status === "checking";

  return (
    <div className="rounded-xl border border-border bg-page/60 p-4 text-left">
      <div className="flex items-start justify-between gap-3 flex-wrap">
        <div className="flex items-start gap-3 min-w-0">
          <span className="mt-0.5 inline-flex items-center justify-center w-9 h-9 rounded-lg bg-primary/10 text-primary shrink-0">
            <Icon name={icon} width={16} height={16} />
          </span>
          <div className="min-w-0">
            <div className="text-sm font-semibold text-ink">{label}</div>
            {description && <div className="text-xs text-muted mt-0.5">{description}</div>}
          </div>
        </div>
        <span className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-semibold shrink-0 ${meta.badge}`}>
          {meta.dotIcon && <Icon name={meta.dotIcon} width={12} height={12} className={spinning ? "animate-spin" : ""} />}
          {statusText}
        </span>
      </div>
      {children && <div className="mt-3">{children}</div>}
      {action && <div className="mt-3">{action}</div>}
    </div>
  );
}

function ToastStack({ toasts, bannerActive }) {
  if (!toasts.length) return null;
  // `top-4` used to sit right on top of the sticky exam header (timer / submit button), and
  // even further under the connection-lost banner when both are showing.
  return (
    <div className={`fixed ${bannerActive ? "top-32" : "top-20"} right-4 z-50 space-y-2 w-[min(320px,90vw)] transition-[top] duration-200`}>
      {toasts.map((t) => (
        <div key={t.id} className="exam-toast rounded-xl border border-amber-500/30 bg-amber-500/10 text-amber-600 text-xs font-semibold px-4 py-3 shadow-sm">
          {t.message}
        </div>
      ))}
    </div>
  );
}

function CodingPanel({ question, editorKey, initialValue, onChange, onRun, onReset, running, runResult }) {
  return (
    <div className="flex-1 flex flex-col">
      <div className="flex items-center justify-between gap-3 mb-2">
        <span className="text-xs font-semibold uppercase tracking-wide text-muted bg-page border border-border rounded-full px-3 py-1">
          {question.language}
        </span>
        {question.time_limit_seconds && (
          <span className="text-xs text-muted">Time limit: {question.time_limit_seconds}s per test case</span>
        )}
        <div className="flex items-center gap-2 ml-auto">
          <button onClick={onReset} className={btnGhost.replace("px-5 py-3", "px-3 py-1.5 text-xs")}>
            Reset
          </button>
          <button onClick={onRun} disabled={running} className={btnPrimary.replace("px-6 py-3.5", "px-3 py-1.5 text-xs") + " disabled:opacity-60"}>
            {running ? "Running…" : "Run Sample"}
          </button>
        </div>
      </div>
      <CodeEditor key={editorKey} value={initialValue} language={question.language} onChange={onChange} />
      {question.sample_test_cases?.length > 0 && (
        <div className="mt-4 rounded-xl border border-border bg-page p-3 text-xs space-y-1">
          <p className="font-semibold text-muted mb-1">Sample test cases</p>
          {question.sample_test_cases.map((tc, i) => (
            <div key={i} className="font-mono text-[11px] text-muted">
              in: <span className="text-ink">{tc.input || "(empty)"}</span> → out: <span className="text-ink">{tc.expected_output}</span>
            </div>
          ))}
        </div>
      )}
      {runResult && (
        <div className="mt-4 rounded-xl border border-border p-3 text-xs">
          {!runResult.available ? (
            <p className="text-muted">{runResult.message || "Code execution is unavailable."}</p>
          ) : runResult.message ? (
            <p className="text-muted">{runResult.message}</p>
          ) : (
            <div className="space-y-2">
              <p className={`font-semibold ${runResult.all_passed ? "text-emerald-500" : "text-amber-500"}`}>
                {runResult.results.filter((r) => r.passed).length} / {runResult.results.length} sample cases passed
              </p>
              {runResult.results.map((r, i) => (
                <div key={i} className={`rounded-lg border px-3 py-2 font-mono text-[11px] ${r.passed ? "border-emerald-500/30 bg-emerald-500/5" : "border-red-500/30 bg-red-500/5"}`}>
                  <div className="flex items-center gap-2 mb-1 font-sans font-semibold">
                    <Icon name={r.passed ? "check" : "x"} width={13} height={13} className={r.passed ? "text-emerald-500" : "text-red-500"} />
                    Case {i + 1} · {r.time_ms}ms
                  </div>
                  <div>expected: {r.expected_output}</div>
                  <div>actual: {r.actual_output || "(none)"}</div>
                  {r.error && <div className="text-red-500">{r.error}</div>}
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/**
 * Final submit confirmation.
 */
function SubmitConfirmModal({ answeredCount, totalCount, flaggedCount = 0, saveSummary,
                             submitting, error, onCancel, onConfirm }) {
  const unanswered = Math.max(totalCount - answeredCount, 0);
  const allDone = unanswered === 0;
  const unsynced = (saveSummary?.pending || 0) + (saveSummary?.failed || 0);

  useEffect(() => {
    // Escape cancels, and focus is trapped to the dialog's own buttons by autoFocus below.
    function onKey(e) {
      if (e.key === "Escape" && !submitting) onCancel();
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onCancel, submitting]);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-ink/60 backdrop-blur-sm px-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby="submit-title"
    >
      <div className="w-full max-w-md rounded-2xl border border-border bg-surface shadow-2xl overflow-hidden animate-fade-in">

        <div className="px-7 pt-7 pb-5">
          <span
            className={`inline-flex items-center justify-center w-12 h-12 rounded-2xl mb-4 ${
              allDone ? "bg-success/10 text-success" : "bg-amber-500/10 text-amber-500"
            }`}
          >
            <Icon name={allDone ? "check" : "alert"} width={22} height={22} />
          </span>

          <h2 id="submit-title" className="text-xl font-extrabold tracking-tight text-ink mb-1.5">
            Submit your exam?
          </h2>
          <p className="text-sm text-muted leading-relaxed">
            {allDone
              ? "You've answered every question. Once you submit, your answers are final."
              : "Once you submit, your answers are final and can't be changed."}
          </p>
        </div>

        <div className="mx-7 mb-6 grid grid-cols-3 rounded-xl border border-border bg-page overflow-hidden">
          <div className="px-3 py-3.5 text-center">
            <div className="text-xl font-extrabold text-ink tabular-nums">{answeredCount}</div>
            <div className="text-[11px] font-semibold uppercase tracking-wide text-muted mt-0.5">Answered</div>
          </div>
          <div className="px-3 py-3.5 text-center border-x border-border">
            <div className={`text-xl font-extrabold tabular-nums ${unanswered > 0 ? "text-amber-500" : "text-ink"}`}>
              {unanswered}
            </div>
            <div className="text-[11px] font-semibold uppercase tracking-wide text-muted mt-0.5">Unanswered</div>
          </div>
          <div className="px-3 py-3.5 text-center">
            <div className="text-xl font-extrabold text-ink tabular-nums">{flaggedCount}</div>
            <div className="text-[11px] font-semibold uppercase tracking-wide text-muted mt-0.5">Flagged</div>
          </div>
        </div>

        {/* Told before submitting, not discovered afterwards. These answers do
            go to the server with the submission (see doSubmit / snapshot), so
            this is a "wait a moment" rather than a warning about loss. */}
        {unsynced > 0 && (
          <div className="mx-7 mb-5 flex items-start gap-2.5 rounded-xl border border-amber-500/25 bg-amber-500/5 px-4 py-3">
            <span className="text-amber-500 mt-0.5 shrink-0"><Icon name="alert" width={15} height={15} /></span>
            <p className="text-sm text-ink leading-relaxed">
              {unsynced === 1 ? "1 answer is" : `${unsynced} answers are`} still syncing. They&apos;ll be
              sent with your submission — submitting now is safe, but staying connected is safer.
            </p>
          </div>
        )}

        {unanswered > 0 && (
          <div className="mx-7 mb-5 flex items-start gap-2.5 rounded-xl border border-amber-500/25 bg-amber-500/5 px-4 py-3">
            <span className="text-amber-500 mt-0.5 shrink-0"><Icon name="alert" width={15} height={15} /></span>
            <p className="text-sm text-ink leading-relaxed">
              {unanswered} question{unanswered === 1 ? "" : "s"} will be submitted unanswered and scored as zero.
            </p>
          </div>
        )}

        {error && (
          <div className="mx-7 mb-5 flex items-start gap-2.5 rounded-xl border border-danger/25 bg-danger/5 px-4 py-3">
            <span className="text-danger mt-0.5 shrink-0"><Icon name="alert" width={15} height={15} /></span>
            <p className="text-sm text-danger leading-relaxed">{error}</p>
          </div>
        )}

        <div className="flex items-center gap-3 border-t border-border bg-page/60 px-7 py-5">
          <button
            type="button"
            autoFocus
            onClick={onCancel}
            disabled={submitting}
            className={`${btnGhost} flex-1 justify-center disabled:opacity-60`}
          >
            Keep working
          </button>
          <button
            type="button"
            onClick={onConfirm}
            disabled={submitting}
            className={`${btnPrimary} flex-1 justify-center disabled:opacity-60`}
          >
            {submitting && <Icon name="spinner" width={15} height={15} className="animate-spin" />}
            {submitting ? "Submitting…" : "Submit exam"}
          </button>
        </div>
      </div>
    </div>
  );
}

/**
 * Full-screen blocking layer shown the moment the student
 * leaves fullscreen, switches tabs, or minimises the window.
 */
/**
 * Full-screen, non-interactive lock shown the instant
 * a submit (manual or timer-expiry) is in flight.
 */
function SubmittingOverlay({ auto, stillRetrying = false }) {
  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center bg-page/95 backdrop-blur-xl px-5" role="status" aria-live="polite">
      <div className="w-full max-w-sm rounded-2xl border border-border bg-card p-8 text-center shadow-xl animate-fade-in">
        <span className="inline-flex items-center justify-center w-14 h-14 rounded-2xl bg-primary/10 text-primary mb-5 animate-pulse">
          <Icon name="clock" width={24} height={24} />
        </span>
        <h2 className="text-lg font-extrabold tracking-tight mb-2">
          {auto ? "Time's up — submitting your exam" : "Submitting your exam"}
        </h2>
        <p className="text-sm text-muted leading-relaxed">
          {stillRetrying
            ? "Your connection dropped. Your answers are held here and we're still trying to send them — keep this page open. Nothing is lost while this screen is up."
            : auto
              ? "Your allotted time expired, so your exam is being submitted automatically. Answered questions are saved and unanswered ones are recorded as unattempted."
              : "Please wait — your answers are being saved and your exam is being submitted. This only takes a moment."}
        </p>
      </div>
    </div>
  );
}

function LockdownOverlay({ terminated, reason, strikes, limit, onResume, onViewResult }) {
  const remaining = Math.max(0, limit - strikes);
  const dialogRef = useRef(null);
  const primaryActionRef = useRef(null);

  // Move focus into the overlay the moment it appears (or its content changes, e.g. breach ->
  // terminated), and keep it trapped there via handleKeyDown below.
  useEffect(() => {
    primaryActionRef.current?.focus();
  }, [terminated]);

  function handleKeyDown(e) {
    if (e.key !== "Tab") return;
    const focusable = dialogRef.current?.querySelectorAll(
      'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
    );
    if (!focusable || focusable.length === 0) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (e.shiftKey && document.activeElement === first) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault();
      first.focus();
    }
  }

  return (
    <div
      ref={dialogRef}
      role="alertdialog"
      aria-modal="true"
      aria-labelledby="lockdown-title"
      onKeyDown={handleKeyDown}
      className="fixed inset-0 z-[100] flex items-center justify-center bg-page/95 backdrop-blur-xl px-5"
    >
      <div className="w-full max-w-md rounded-2xl border border-border bg-card p-8 text-center shadow-2xl animate-fade-in">
        <span
          className={`inline-flex items-center justify-center w-14 h-14 rounded-2xl mb-5 ${
            terminated ? "bg-red-500/10 text-red-500" : "bg-amber-500/10 text-amber-500"
          }`}
        >
          <Icon name={terminated ? "x" : "alert"} width={24} height={24} />
        </span>

        {terminated ? (
          <>
            <h2 id="lockdown-title" className="text-xl font-extrabold tracking-tight mb-2">
              Exam terminated
            </h2>
            <p className="text-sm text-muted leading-relaxed mb-6">
              You left the exam window {strikes} times, which exceeds the limit of {limit}. Your exam has been
              submitted automatically and the answers saved up to this point have been graded.
            </p>
            <button onClick={onViewResult} className={`${btnPrimary} w-full justify-center`}>
              View Result
            </button>
          </>
        ) : (
          <>
            {/* "Exam paused" was not true: the timer keeps running while this
                overlay is up, so a candidate who read it as a pause and took a
                moment to sort out their screen share lost that time believing
                they had not. Saying what is actually happening is worth more
                than the reassurance. */}
            <h2 id="lockdown-title" className="text-xl font-extrabold tracking-tight mb-2">
              Exam locked — your timer is still running
            </h2>
            <p className="text-sm text-muted leading-relaxed mb-4">{reason}</p>

            <div className="mb-6 rounded-xl border border-amber-500/30 bg-amber-500/10 px-4 py-3 text-left">
              <p className="text-sm font-semibold text-amber-600 dark:text-amber-400 mb-1">
                Warning {strikes} of {limit}
              </p>
              <p className="text-xs text-muted leading-relaxed">
                {remaining > 0
                  ? `${remaining} more ${remaining === 1 ? "violation" : "violations"} will end your exam automatically. The timer is still running.`
                  : "Any further violation will end your exam automatically."}
              </p>
            </div>

            <button onClick={onResume} className={`${btnPrimary} w-full justify-center`}>
              <Icon name="maximize" width={16} height={16} />
              Return to Exam
            </button>
            <p className="mt-3 text-xs text-muted">
              Any click or key press also returns you to fullscreen automatically. Stay in fullscreen and do not
              switch tabs, minimise, or open other applications.
            </p>
          </>
        )}
      </div>
    </div>
  );
}
