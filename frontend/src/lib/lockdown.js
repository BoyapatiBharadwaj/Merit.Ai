/**
 * Exam lockdown controller.
 */
import { Api } from "./api.js";

const BLOCKED_KEYS = new Set(["F12", "PrintScreen", "ContextMenu"]);

/**
 * Must be invoked synchronously inside a user-gesture handler.
 */
export function requestScreenShare() {
  if (!navigator.mediaDevices?.getDisplayMedia) {
    return Promise.reject(new Error("Screen sharing is not supported by this browser."));
  }

  let mediaPromise;
  try {
    // getDisplayMedia() itself is wrapped in try/catch, not just its promise.
    mediaPromise = navigator.mediaDevices.getDisplayMedia({
      video: { displaySurface: "monitor", frameRate: { ideal: 5, max: 10 } },
      audio: false,
      monitorTypeSurfaces: "include",
      selfBrowserSurface: "exclude",
      surfaceSwitching: "exclude",
    });
  } catch (err) {
    return Promise.reject(err instanceof Error ? err : new Error("Couldn't start screen sharing."));
  }

  return mediaPromise.then((stream) => {
    const track = stream.getVideoTracks()[0];
    const settings = track ? track.getSettings() : {};
    // Only reject when the browser actually *reports* the wrong surface. displaySurface is a
    // fairly new addition to getSettings().
    if (settings.displaySurface && settings.displaySurface !== "monitor") {
      stream.getTracks().forEach((t) => t.stop());
      throw new Error("Please share your entire screen, not a window or a browser tab.");
    }
    return stream;
  });
}

export function createLockdown() {
  let attemptId = null;
  let stopped = false;
  let breached = false;
  let terminated = false;

  let onBreach = null;
  let onRestored = null;
  let onTerminated = null;
  let onStrikeUpdate = null;
  let onToast = null;

  let strikes = 0;
  let strikeLimit = 3;
  let resumeListenersAttached = false;
  let resumeInFlight = null;
  let initialFullscreenCheckTimer = null;
  let screenStream = null;
  // Which breach is currently up, so resume() knows whether it needs to re-acquire a
  // screen-share stream (fullscreen exits don't) before clearing the overlay.
  let lastBreachEventType = null;

  /* ------------------------------------------------------------------ *
   * Screen sharing                                                      *
   * ------------------------------------------------------------------ */

  /**
   * Wires "the shared screen stopped" to the same breach/strike path as leaving fullscreen.
   */
  function watchScreenShare(stream) {
    if (!stream || stream === screenStream) return;
    unwatchScreenShare();
    screenStream = stream;
    const track = stream.getVideoTracks()[0];
    if (track) track.addEventListener("ended", handleScreenShareEnded);
  }

  function handleScreenShareEnded() {
    if (stopped || terminated) return;
    reportBreach("screen_share_stopped", "Screen sharing was stopped or the shared screen/window was closed.");
  }

  function unwatchScreenShare() {
    if (!screenStream) return;
    const track = screenStream.getVideoTracks()[0];
    if (track) track.removeEventListener("ended", handleScreenShareEnded);
    screenStream = null;
  }

  /* ------------------------------------------------------------------ *
   * Fullscreen                                                          *
   * ------------------------------------------------------------------ */

  function isFullscreen() {
    return Boolean(document.fullscreenElement || document.webkitFullscreenElement);
  }

  /** Must be invoked synchronously inside a user-gesture handler -- browsers
   * reject requestFullscreen() once the activation has been consumed by an
   * await. Both the "Begin Exam" button and the overlay's "Return to Exam"
   * button call this directly for that reason. */
  function requestFullscreen() {
    const el = document.documentElement;
    const request = el.requestFullscreen || el.webkitRequestFullscreen;
    if (!request) return Promise.reject(new Error("Fullscreen is not supported by this browser."));
    try {
      const result = request.call(el, { navigationUI: "hide" });
      return result && typeof result.then === "function" ? result : Promise.resolve();
    } catch (err) {
      return Promise.reject(err);
    }
  }

  /**
   * Auto-return to fullscreen the instant the student does *anything* while breached.
   */
  function handleResumeGesture() {
    if (!breached || terminated) return;
    resume();
  }

  function attachResumeListeners() {
    if (resumeListenersAttached) return;
    resumeListenersAttached = true;
    document.addEventListener("click", handleResumeGesture, true);
    document.addEventListener("keydown", handleResumeGesture, true);
  }

  function detachResumeListeners() {
    if (!resumeListenersAttached) return;
    resumeListenersAttached = false;
    document.removeEventListener("click", handleResumeGesture, true);
    document.removeEventListener("keydown", handleResumeGesture, true);
  }

  /* ------------------------------------------------------------------ *
   * Breach handling                                                     *
   * ------------------------------------------------------------------ */

  /**
   * A single physical action (Alt-Tab) fires blur +
   * visibilitychange + fullscreenchange in quick succession.
   */
  async function reportBreach(eventType, description) {
    if (stopped || terminated || breached) return;
    breached = true;
    lastBreachEventType = eventType;
    attachResumeListeners();
    if (onBreach) onBreach({ reason: description, eventType });

    if (!attemptId) return;
    try {
      const res = await Api.exam.post("/proctoring/lockdown/strike", {
        attempt_id: attemptId,
        event_type: eventType,
        description,
      });
      strikes = res.strikes ?? strikes + 1;
      strikeLimit = res.limit ?? strikeLimit;
      if (onStrikeUpdate) onStrikeUpdate({ strikes, limit: strikeLimit, remaining: res.remaining ?? 0 });

      if (res.terminated) {
        terminated = true;
        detachResumeListeners(); // exam is over; no gesture should re-trigger resume()
        if (onTerminated) onTerminated({ strikes, limit: strikeLimit });
      }
    } catch {
      // A failed strike report must not unblock the exam. The overlay stays up regardless.
      strikes += 1;
      if (onStrikeUpdate) onStrikeUpdate({ strikes, limit: strikeLimit, remaining: Math.max(0, strikeLimit - strikes) });
    }
  }

  /**
   * Called by the overlay's "Return to Exam" button (a real user gesture).
   */
  async function resume() {
    if (terminated) return false;
    // Deduped, and this is the normal path rather than a rare race.
    if (resumeInFlight) return resumeInFlight;
    resumeInFlight = _resume().finally(() => {
      resumeInFlight = null;
    });
    return resumeInFlight;
  }

  async function _resume() {
    const fullscreenPromise = requestFullscreen();
    // A screen_share_stopped breach left the old stream dead.
    const screenSharePromise = lastBreachEventType === "screen_share_stopped" ? requestScreenShare() : null;
    const statusPromise = fetchAuthoritativeStatus();

    let fullscreenOk = true;
    try {
      await fullscreenPromise;
    } catch {
      fullscreenOk = false;
    }

    let screenShareOk = true;
    if (screenSharePromise) {
      try {
        watchScreenShare(await screenSharePromise);
      } catch {
        screenShareOk = false;
      }
    }

    const status = await statusPromise;
    if (terminated) return false; // fetchAuthoritativeStatus just discovered we're over the limit
    if (stopped) return false;

    // Only conditions the student can actually DO something about keep the overlay up.
    if (!fullscreenOk && isFullscreen()) fullscreenOk = true;

    if (!fullscreenOk) {
      if (onToast) onToast("Your browser blocked fullscreen. Allow it and try again.");
      return false;
    }
    if (!screenShareOk) {
      if (onToast) onToast("Please re-share your entire screen to continue the exam.");
      return false;
    }
    if (!status.ok && onToast) {
      onToast("Couldn't refresh your warning count -- it may be out of date until your connection recovers.");
    }
    // Re-check state *after* the awaits. Between the status call starting and finishing, the
    // student can have resumed, Alt-Tabbed again, and taken a fresh strike.
    if (terminated) return false;
    breached = false;
    lastBreachEventType = null;
    detachResumeListeners();
    if (onRestored) onRestored();
    return true;
  }

  /* ------------------------------------------------------------------ *
   * Listeners                                                           *
   * ------------------------------------------------------------------ */

  function handleFullscreenChange() {
    if (stopped || terminated) return;
    if (!isFullscreen()) {
      reportBreach("fullscreen_exit", "Student left fullscreen mode.");
    }
  }

  function handleVisibilityChange() {
    if (stopped || terminated) return;
    if (document.hidden) {
      reportBreach("tab_switch", "Exam tab was hidden (tab switch or window minimised).");
    }
  }

  function handleBlur() {
    if (stopped || terminated) return;
    reportBreach("tab_switch", "Exam window lost focus.");
  }

  let logEvent = null;

  function block(e, eventType, message) {
    e.preventDefault();
    e.stopPropagation();
    if (onToast) onToast(message);
    logViolation(eventType, message);
    return false;
  }

  const handleContextMenu = (e) => block(e, "right_click_attempt", "Right-click is disabled during the exam.");
  const handleCopy = (e) => block(e, "copy_paste_attempt", "Copying is disabled during the exam.");
  const handlePaste = (e) => block(e, "copy_paste_attempt", "Pasting is disabled during the exam.");
  const handleCut = (e) => block(e, "copy_paste_attempt", "Cutting is disabled during the exam.");
  const handleDragStart = (e) => {
    e.preventDefault();
  };
  const handleSelectStart = (e) => {
    // Inputs and the code editor must stay usable; only block selection of
    // the question text itself, which is what a copy attempt would target.
    const tag = (e.target?.tagName || "").toLowerCase();
    const editable = tag === "input" || tag === "textarea" || e.target?.isContentEditable;
    if (!editable) e.preventDefault();
  };

  /**
   * PrintScreen leaves the captured image on the OS clipboard.
   */
  async function wipeClipboard() {
    try {
      await navigator.clipboard.writeText("");
    } catch {
      /* permission denied or unfocused document -- nothing more we can do */
    }
  }

  function handleKeydown(e) {
    const key = e.key || "";
    const lower = key.toLowerCase();
    const ctrlOrMeta = e.ctrlKey || e.metaKey;

    // Screen-capture shortcuts: PrintScreen, Win+Shift+S
    // (Windows Snip), Cmd+Shift+3/4/5 (macOS).
    const isPrintScreen = key === "PrintScreen";
    const isSnip = (e.metaKey && e.shiftKey && ["s", "3", "4", "5"].includes(lower));
    if (isPrintScreen || isSnip) {
      e.preventDefault();
      wipeClipboard();
      logViolation("screenshot_attempt", `Screen-capture shortcut pressed (${key}).`);
      if (onToast) onToast("Screenshots are not allowed. This attempt has been recorded.");
      return;
    }

    // Devtools / view-source / save / print.
    const devtools =
      key === "F12" ||
      (ctrlOrMeta && e.shiftKey && ["i", "j", "c"].includes(lower)) ||
      (ctrlOrMeta && ["u", "s", "p"].includes(lower));
    if (devtools) {
      return void block(e, "copy_paste_attempt", "That shortcut is disabled during the exam.");
    }

    // Clipboard shortcuts (the copy/paste events cover the menu path).
    if (ctrlOrMeta && ["c", "v", "x", "a"].includes(lower)) {
      const tag = (e.target?.tagName || "").toLowerCase();
      // The code editor legitimately needs select-all and clipboard use for
      // the student's OWN code, so leave editable targets alone.
      if (tag === "input" || tag === "textarea" || e.target?.isContentEditable) return;
      return void block(e, "copy_paste_attempt", "Clipboard shortcuts are disabled during the exam.");
    }

    // Alt+Tab / Ctrl+W / Ctrl+T / Alt+F4 cannot be intercepted
    // reliably -- the OS or browser chrome consumes them first.
    if (BLOCKED_KEYS.has(key) && key !== "PrintScreen") e.preventDefault();
  }

  function handleBeforeUnload(e) {
    if (stopped || terminated) return;
    // Browsers show their own generic wording; returnValue just opts in.
    e.preventDefault();
    e.returnValue = "";
    return "";
  }

  function logViolation(eventType, description) {
    if (!attemptId) return;
    // No camera access here (lockdown.js doesn't own the video stream -- proctoring.js does),
    // so these violations carry no screenshot.
    if (logEvent) {
      logEvent(eventType, description, null);
      return;
    }
    Api.exam.post("/proctoring/events", { attempt_id: attemptId, event_type: eventType, description }).catch(() => {
      /* best-effort logging; never cascade */
    });
  }

  /* ------------------------------------------------------------------ *
   * Lifecycle                                                           *
   * ------------------------------------------------------------------ */

  /**
   * Single source of truth for "what does the server think our strike state is".
   */
  async function fetchAuthoritativeStatus() {
    if (!attemptId) return { ok: false };
    try {
      const res = await Api.exam.get(`/proctoring/lockdown/status/${attemptId}`);
      strikes = res.strikes ?? 0;
      strikeLimit = res.limit ?? strikeLimit;
      if (onStrikeUpdate) onStrikeUpdate({ strikes, limit: strikeLimit, remaining: res.remaining ?? strikeLimit });
      if (res.terminated) {
        terminated = true;
        detachResumeListeners(); // exam is over; no gesture should re-trigger resume()
        if (onTerminated) onTerminated({ strikes, limit: strikeLimit });
      }
      return { ok: true, terminated: Boolean(res.terminated) };
    } catch {
      /* leave defaults; the caller decides how to treat an unverifiable status */
      return { ok: false };
    }
  }

  function init({
    attemptIdVal,
    breachCallback,
    restoredCallback,
    terminatedCallback,
    strikeCallback,
    toastCallback,
    logEventCallback,
  }) {
    attemptId = attemptIdVal;
    onBreach = breachCallback || null;
    onRestored = restoredCallback || null;
    onTerminated = terminatedCallback || null;
    onStrikeUpdate = strikeCallback || null;
    onToast = toastCallback || null;
    logEvent = logEventCallback || null;

    document.addEventListener("fullscreenchange", handleFullscreenChange);
    document.addEventListener("webkitfullscreenchange", handleFullscreenChange);
    document.addEventListener("visibilitychange", handleVisibilityChange);
    window.addEventListener("blur", handleBlur);
    document.addEventListener("contextmenu", handleContextMenu, true);
    document.addEventListener("copy", handleCopy, true);
    document.addEventListener("paste", handlePaste, true);
    document.addEventListener("cut", handleCut, true);
    document.addEventListener("dragstart", handleDragStart, true);
    document.addEventListener("selectstart", handleSelectStart, true);
    document.addEventListener("keydown", handleKeydown, true);
    window.addEventListener("beforeunload", handleBeforeUnload);
    document.body.classList.add("exam-locked");

    // Resume case: a reload mid-exam must show the real remaining budget
    // rather than pretending the student has a fresh three strikes.
    fetchAuthoritativeStatus();

    // If the page loaded outside fullscreen (e.g. after a refresh), treat it as a breach so the
    // overlay appears and the student has to re-enter.
    initialFullscreenCheckTimer = setTimeout(() => {
      initialFullscreenCheckTimer = null;
      if (stopped || terminated || breached) return;
      if (!isFullscreen()) {
        breached = true;
        attachResumeListeners();
        if (onBreach) onBreach({ reason: "Fullscreen is required to continue.", eventType: null });
      }
    }, 1000);
  }

  /** Tear down. Order matters: unhook the fullscreenchange listener BEFORE
   * exiting fullscreen, or our own cleanup would register as a breach and
   * charge the student a strike on a legitimate submit. */
  function stop() {
    stopped = true;
    if (initialFullscreenCheckTimer) {
      clearTimeout(initialFullscreenCheckTimer);
      initialFullscreenCheckTimer = null;
    }
    detachResumeListeners();
    document.removeEventListener("fullscreenchange", handleFullscreenChange);
    document.removeEventListener("webkitfullscreenchange", handleFullscreenChange);
    document.removeEventListener("visibilitychange", handleVisibilityChange);
    window.removeEventListener("blur", handleBlur);
    document.removeEventListener("contextmenu", handleContextMenu, true);
    document.removeEventListener("copy", handleCopy, true);
    document.removeEventListener("paste", handlePaste, true);
    document.removeEventListener("cut", handleCut, true);
    document.removeEventListener("dragstart", handleDragStart, true);
    document.removeEventListener("selectstart", handleSelectStart, true);
    document.removeEventListener("keydown", handleKeydown, true);
    window.removeEventListener("beforeunload", handleBeforeUnload);
    document.body.classList.remove("exam-locked");
    // `stopped = true` above means handleScreenShareEnded's guard clause has
    // already turned into a no-op, so stopping the track here (a legitimate,
    // exam-is-over stop) can't be mistaken for a mid-exam breach.
    if (screenStream) {
      const tracks = screenStream.getTracks();
      unwatchScreenShare();
      tracks.forEach((t) => t.stop());
    }
    if (isFullscreen() && document.exitFullscreen) document.exitFullscreen().catch(() => {});
  }

  return {
    init,
    stop,
    resume,
    requestFullscreen,
    watchScreenShare,
    isFullscreen,
    isBreached: () => breached,
    isTerminated: () => terminated,
    getStrikes: () => ({ strikes, limit: strikeLimit }),
  };
}
