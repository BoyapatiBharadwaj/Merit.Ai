/**
 * Centralized batching event logger for proctoring/lockdown violations.
 */
import { getToken } from "./auth.js";

const FLUSH_INTERVAL_MS = 4000;
const MAX_BATCH_SIZE = 8;
// Hard cap on the in-memory queue. A prolonged outage (or a client
// that never flushes) must not let this grow without bound.
const MAX_QUEUE_SIZE = 500;
// Where the queue is mirrored, keyed per attempt so two tabs or a stale entry
// from a previous exam cannot bleed into this one.
const STORAGE_PREFIX = "aep_pending_events_";
const BASE_URL = "/api/v1";

export function createEventLogger() {
  let attemptId = null;
  let queue = [];
  let flushTimer = null;
  let flushing = false;
  let stopped = false;

  const storageKey = () => `${STORAGE_PREFIX}${attemptId}`;

  /**
   * Mirror the queue to localStorage.
   */
  function persistQueue() {
    if (!attemptId) return;
    try {
      localStorage.setItem(storageKey(), JSON.stringify(
        queue.map(({ screenshot_base64: _drop, ...rest }) => rest),
      ));
    } catch {
      // A full or unavailable localStorage must never break proctoring.
    }
  }

  /** Re-adopt anything a previous page load left unsent. */
  function restoreQueue() {
    if (!attemptId) return;
    try {
      const raw = localStorage.getItem(storageKey());
      if (!raw) return;
      const pending = JSON.parse(raw);
      if (Array.isArray(pending) && pending.length) {
        queue = [...pending, ...queue].slice(-MAX_QUEUE_SIZE);
        scheduleFlush();
      }
    } catch {
      // Corrupt entry -- drop it rather than refusing to start.
    }
  }

  function clearPersisted() {
    if (!attemptId) return;
    try {
      localStorage.removeItem(storageKey());
    } catch {
      // ignore
    }
  }

  function scheduleFlush() {
    if (flushTimer || stopped) return;
    flushTimer = setTimeout(() => {
      flushTimer = null;
      flush();
    }, FLUSH_INTERVAL_MS);
  }

  /** Enqueue a violation. Never throws and never awaits a network call --
   * callers (lockdown.js's logViolation, proctoring.js's logViolation) are
   * fire-and-forget by design, same as before this module existed. */
  function log(eventType, description, screenshotBase64 = null) {
    if (stopped || !attemptId) return;
    queue.push({
      attempt_id: attemptId,
      event_type: eventType,
      description: description ?? null,
      screenshot_base64: screenshotBase64 || null,
    });
    if (queue.length > MAX_QUEUE_SIZE) queue.splice(0, queue.length - MAX_QUEUE_SIZE);
    persistQueue();
    if (queue.length >= MAX_BATCH_SIZE) flush();
    else scheduleFlush();
  }

  async function flush() {
    if (flushTimer) {
      clearTimeout(flushTimer);
      flushTimer = null;
    }
    if (flushing || queue.length === 0) return;
    const batch = queue;
    queue = [];
    flushing = true;
    try {
      const token = getToken();
      const res = await fetch(`${BASE_URL}/proctoring/events/batch`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body: JSON.stringify({ events: batch }),
      });
      if (!res.ok) throw new Error(`batch flush failed (${res.status})`);
      // Acknowledged by the server -- only now is it safe to forget them.
      persistQueue();
      if (queue.length === 0) clearPersisted();
    } catch {
      // Best-effort: put the failed batch back ahead of anything logged
      // since, so the next flush (timer, next log(), or unload) retries it --
      // capped the same way log() caps the live queue.
      queue = [...batch, ...queue];
      if (queue.length > MAX_QUEUE_SIZE) queue.splice(0, queue.length - MAX_QUEUE_SIZE);
      persistQueue();
    } finally {
      flushing = false;
    }
  }

  /**
   * Best-effort synchronous-ish flush for page unload. Plain fetch() is not guaranteed to
   * complete once the page starts tearing down, but a keepalive fetch is.
   */
  function flushOnUnload() {
    if (queue.length === 0) return;
    const batch = queue;
    queue = [];
    const token = getToken();
    try {
      fetch(`${BASE_URL}/proctoring/events/batch`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body: JSON.stringify({ events: batch }),
        keepalive: true,
      }).catch(() => {});
    } catch {
      /* best-effort; the exam is ending regardless */
    }
  }

  function handleVisibilityChange() {
    // Not just "the page is closing" -- a tab switch or window minimise
    // (already itself a logged lockdown violation) is also a good moment to
    // flush, since the student's next action is unpredictable from here.
    if (document.hidden) flushOnUnload();
  }

  function init({ attemptIdVal }) {
    attemptId = attemptIdVal;
    // Adopt anything a previous page load (a crash, a reload, a closed laptop)
    // left unsent for THIS attempt before accepting anything new.
    restoreQueue();
    window.addEventListener("pagehide", flushOnUnload);
    document.addEventListener("visibilitychange", handleVisibilityChange);
  }

  function stop() {
    stopped = true;
    if (flushTimer) {
      clearTimeout(flushTimer);
      flushTimer = null;
    }
    window.removeEventListener("pagehide", flushOnUnload);
    document.removeEventListener("visibilitychange", handleVisibilityChange);
    flushOnUnload(); // drain whatever's left (e.g. the exam just submitted)
  }

  return { init, log, flush, stop };
}
