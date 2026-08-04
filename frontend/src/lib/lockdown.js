/**
 * Exam lockdown controller.
 *
 * Owns everything about keeping the student inside the exam window:
 * fullscreen enforcement, tab-switch / focus-loss detection, the strike
 * budget, and the input deterrents (copy/paste, right-click, PrintScreen,
 * devtools shortcuts, text selection).
 *
 * Split out of proctoring.js deliberately. Proctoring answers "is the right
 * person alone in frame?" and degrades gracefully when the AI worker is down.
 * Lockdown answers "is the student still in the exam?" and must never
 * degrade -- it has no external dependencies and keeps working even if every
 * AI service is offline.
 *
 * ---------------------------------------------------------------------------
 * What this can and cannot do -- worth being precise about:
 *
 *   ENFORCED   fullscreen required to interact; leaving it pauses the exam
 *              behind a blocking overlay and costs a strike
 *   ENFORCED   tab switch / window minimise / focus loss -> same treatment
 *   ENFORCED   copy, paste, cut, right-click, text selection, drag
 *   ENFORCED   devtools and view-source keyboard shortcuts
 *   ENFORCED   strike limit -> server force-submits the attempt
 *   ENFORCED   entire-screen sharing required to start; ending the share
 *              mid-exam gets the same breach/strike treatment as leaving
 *              fullscreen (see requestScreenShare/watchScreenShare below)
 *
 *   DETERRED   screenshots. A web page cannot block an OS screen capture.
 *              We intercept the PrintScreen key, wipe the clipboard, and log
 *              the attempt -- a determined student with a phone camera or a
 *              second machine is out of reach of any browser-based system.
 *   NOT POSSIBLE  blocking screen recording, guaranteeing every attached
 *              monitor is inside the shared surface, and suppressing
 *              OS-level notifications from other apps. Real prevention
 *              requires a lockdown browser (Safe Exam Browser) or a
 *              supervised physical environment.
 *
 * The strike count is authoritative on the SERVER (see lockdown_service.py).
 * This module reports breaches and renders whatever the server says; it never
 * decides termination on its own, because client state is trivially forged.
 * ---------------------------------------------------------------------------
 */
import { Api } from "./api.js";

const BLOCKED_KEYS = new Set(["F12", "PrintScreen", "ContextMenu"]);

/** Must be invoked synchronously inside a user-gesture handler -- like
 * requestFullscreen(), getDisplayMedia() requires transient activation and
 * browsers reject it once an await has consumed the click. This is a
 * standalone export (not a createLockdown() method) for two reasons: the
 * initial request has to fire from the "Begin Exam" button before any
 * lockdown controller for the not-yet-started attempt exists, and the
 * controller's own resume() path (see _resume() below) needs to call the
 * exact same acquire-and-validate logic to re-share after a mid-exam stop,
 * without depending on init() having already run.
 *
 * "Entire screen" here means the monitor the student's browser picker let
 * them choose -- it does not guarantee every attached display is inside the
 * shared surface. displaySurface is checked after selection because the
 * constraint hints below are Chrome-only steers, not an enforced filter: a
 * student can still pick a window or a tab despite them. */
export function requestScreenShare() {
  if (!navigator.mediaDevices?.getDisplayMedia) {
    return Promise.reject(new Error("Screen sharing is not supported by this browser."));
  }

  let mediaPromise;
  try {
    // getDisplayMedia() itself is wrapped in try/catch, not just its promise
    // -- WebIDL dictionary conversion for its options happens synchronously,
    // and a browser that doesn't recognise one of the newer Chrome-only hints
    // below (monitorTypeSurfaces, selfBrowserSurface, surfaceSwitching) is
    // supposed to just ignore the unknown member, but this must never be able
    // to throw *before* returning a promise -- every caller here is chained
    // off the assumption that this function always returns one.
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
    // Only reject when the browser actually *reports* the wrong surface.
    // displaySurface is a fairly new addition to getSettings() -- on a
    // browser/version that doesn't expose it, settings.displaySurface is
    // undefined, and the old `!== "monitor"` check treated that as a
    // mismatch too. That stopped a student's perfectly correct "Entire
    // Screen" share and threw a false "please share your entire screen"
    // warning at them every single time on any such browser.
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
  // Which breach is currently up, so resume() knows whether it needs to
  // re-acquire a screen-share stream (fullscreen exits don't) before
  // clearing the overlay. Distinct from `breached` (a bool) because two
  // different breach causes need two different recovery actions.
  let lastBreachEventType = null;

  /* ------------------------------------------------------------------ *
   * Screen sharing                                                      *
   * ------------------------------------------------------------------ */

  /** Wires "the shared screen stopped" to the same breach/strike path as
   * leaving fullscreen -- a student ending the share mid-exam (closing the
   * shared window, clicking the browser's own "Stop sharing" bar) is exactly
   * as meaningful a signal as Alt-Tabbing out of the exam. */
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

  /** Auto-return to fullscreen the instant the student does *anything* while
   * breached -- a click or a keypress, anywhere on the page -- rather than
   * requiring them to find and press a specific "Return to Exam" button.
   *
   * This exists because a plain timer cannot do this: browsers only grant
   * requestFullscreen() in direct response to a real user gesture, and
   * reject it outright if called from a setTimeout with no click/keypress in
   * between (a deliberate anti-abuse restriction on every browser, not
   * something this app can route around). The next real gesture the student
   * makes -- for any reason, not necessarily aimed at a "resume" button --
   * IS a valid gesture, so it's used directly. Capture-phase, so it fires
   * before whatever else the gesture would have done (the exam content
   * behind the overlay is `inert` and covered by the overlay anyway; see
   * Exam.jsx's LockdownOverlay).
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

  /** A single physical action (Alt-Tab) fires blur + visibilitychange +
   * fullscreenchange in quick succession. We latch on the first one and
   * ignore the rest until the student explicitly resumes, so one mistake
   * costs exactly one strike. The server debounces independently as a
   * second line of defence. */
  async function reportBreach(eventType, description) {
    if (stopped || terminated || breached) return;
    breached = true;
    lastBreachEventType = eventType;
    attachResumeListeners();
    if (onBreach) onBreach({ reason: description, eventType });

    if (!attemptId) return;
    try {
      const res = await Api.post("/proctoring/lockdown/strike", {
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
      // A failed strike report must not unblock the exam. The overlay stays
      // up regardless; the student still has to return to fullscreen, and
      // the next successful call will resync the true count from the server.
      strikes += 1;
      if (onStrikeUpdate) onStrikeUpdate({ strikes, limit: strikeLimit, remaining: Math.max(0, strikeLimit - strikes) });
    }
  }

  /** Called by the overlay's "Return to Exam" button (a real user gesture).
   *
   * Re-verifies the authoritative strike count from the server before letting
   * the student back in, rather than trusting whatever `strikes`/`terminated`
   * this client last knew about. That distinction matters exactly when it's
   * needed most: the breach that brought up this overlay may itself be the
   * one that crossed the strike limit, and if that report's response was
   * lost (a network blip, a slow server) `terminated` would still be `false`
   * locally -- silently reopening an exam the server already force-submitted.
   *
   * requestFullscreen() is called first and NOT awaited before the status
   * check starts: browsers only honor requestFullscreen() when it's invoked
   * synchronously inside the user-gesture call stack, and an `await` ahead of
   * it would consume that activation and get it silently rejected. The
   * status check runs concurrently instead of blocking it. */
  async function resume() {
    if (terminated) return false;
    // Deduped, and this is the normal path rather than a rare race: the
    // auto-return listener is registered on `document` in capture phase, so
    // clicking the overlay's own "Return to Exam" button fires it *before*
    // React's onClick -> resumeFromBreach() -> resume(). Two concurrent
    // resumes meant two requestFullscreen() calls, the second of which
    // browsers routinely reject -- surfacing "Your browser blocked
    // fullscreen" on a resume that had in fact just worked.
    if (resumeInFlight) return resumeInFlight;
    resumeInFlight = _resume().finally(() => {
      resumeInFlight = null;
    });
    return resumeInFlight;
  }

  async function _resume() {
    const fullscreenPromise = requestFullscreen();
    // A screen_share_stopped breach left the old stream dead -- fullscreen
    // alone doesn't fix that, so the same gesture that resumes fullscreen
    // also has to re-open the browser's screen-share picker. Only fired for
    // that specific breach type; a plain fullscreen-exit/tab-switch resume
    // doesn't touch screen sharing at all.
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

    // Only conditions the student can actually DO something about keep the
    // overlay up. Everything else must let them back in.
    //
    // This used to also bail out when the status fetch failed, which was a
    // trap: a single failed (or slow, or 404-ing) status request left the
    // student sealed behind the overlay with a toast as their only clue and
    // no way back into an exam whose timer was still running -- even though
    // fullscreen had been restored successfully and nothing was actually
    // wrong on their end. The strike count is authoritative on the SERVER
    // (see lockdown_service.py), so a client that can't read it is not a
    // reason to withhold the exam: if they really are over the limit, the
    // server has already force-submitted and the next successful call --
    // including the one behind the very next breach -- surfaces that. Fail
    // open on the read, never on the enforcement.
    // What matters is the END STATE, not whether our request was the thing
    // that produced it. requestFullscreen() rejects in several situations
    // where the student is nonetheless already fullscreen and fine -- most
    // commonly when the element is in fullscreen ALREADY (calling it again
    // is an error in some engines), or when two resume paths raced and the
    // other one won. Treating a rejection as authoritative there is what
    // turns "Return to Exam" into a button that visibly does nothing.
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
    // Re-check state *after* the awaits. Between the status call starting and
    // finishing, the student can have resumed, Alt-Tabbed again, and taken a
    // fresh strike -- in which case this now-stale resume would tear down the
    // overlay for a breach that is still live, and remove the auto-return
    // listeners with the student outside fullscreen.
    //
    // `!breached` is deliberately NOT one of those cases. It means this
    // controller no longer thinks it's breached while the UI still shows the
    // overlay -- a desync, and one where silently returning false would
    // leave that overlay up with nothing left to ever take it down. Falling
    // through re-runs onRestored() and puts the two back in agreement, which
    // is idempotent and safe.
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

  /** PrintScreen leaves the captured image on the OS clipboard. We cannot
   * stop the capture, but overwriting the clipboard immediately afterwards
   * makes the result useless for pasting elsewhere, and the attempt is
   * logged either way. Best-effort: clipboard writes require permission and
   * a focused document, so this silently no-ops when unavailable. */
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

    // Screen-capture shortcuts: PrintScreen, Win+Shift+S (Windows Snip),
    // Cmd+Shift+3/4/5 (macOS). The OS handles most of these before the page
    // ever sees them -- logging is the realistic outcome, not prevention.
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

    // Alt+Tab / Ctrl+W / Ctrl+T / Alt+F4 cannot be intercepted reliably --
    // the OS or browser chrome consumes them first. The resulting focus loss
    // is caught by handleBlur() instead, which is the honest place to catch it.
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
    // No camera access here (lockdown.js doesn't own the video stream --
    // proctoring.js does), so these violations carry no screenshot.
    // Routed through the shared batching logger (lib/eventLogger.js) rather
    // than a direct Api.post -- see Exam.jsx's logEventCallback wiring. Falls
    // back to a direct, best-effort POST if no logger was wired up, so a
    // violation is never silently dropped.
    if (logEvent) {
      logEvent(eventType, description, null);
      return;
    }
    Api.post("/proctoring/events", { attempt_id: attemptId, event_type: eventType, description }).catch(() => {
      /* best-effort logging; never cascade */
    });
  }

  /* ------------------------------------------------------------------ *
   * Lifecycle                                                           *
   * ------------------------------------------------------------------ */

  /** Single source of truth for "what does the server think our strike state
   * is" -- used both on init (a reload mid-exam must show the real remaining
   * budget) and on every resume attempt (see resume() above for why that
   * second call site is the one that actually closes the resync gap). */
  async function fetchAuthoritativeStatus() {
    if (!attemptId) return { ok: false };
    try {
      const res = await Api.get(`/proctoring/lockdown/status/${attemptId}`);
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

    // If the page loaded outside fullscreen (e.g. after a refresh), treat it
    // as a breach so the overlay appears and the student has to re-enter --
    // but do NOT charge a strike for it, since a refresh already cost one via
    // the visibilitychange/blur that preceded it.
    //
    // This check is deliberately delayed rather than run immediately: the
    // "Begin Exam" click also fires requestScreenShare() (see lockdown.js's
    // export of that name), and on Chromium-family browsers, showing the
    // native "share your screen" picker forces the page OUT of fullscreen
    // the moment it appears -- a browser-driven side effect, not anything
    // the student did. That exit happens somewhere during beginExam()'s
    // network round trip, i.e. *before* this controller even exists yet, so
    // an immediate check here caught that in-flight transition and threw up
    // "Fullscreen is required to continue" on essentially every exam start.
    // Exam.jsx's beginScreenShare() already makes a best-effort attempt to
    // re-enter fullscreen once the share picker is dismissed; this delay
    // gives that attempt a moment to land before deciding there's a real
    // problem. A genuine miss (e.g. an actual page reload mid-exam, with no
    // pending screen-share interruption to recover from) still gets caught,
    // just a beat later than before.
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
