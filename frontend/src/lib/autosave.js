/**
 * The autosave queue for a live exam attempt.
 *
 * This replaces three near-identical copies of "PUT, retry on network error,
 * delete the pending entry" that lived inside Exam.jsx. Three copies is how the
 * bugs below came to be fixed in one of them and not the others; more
 * importantly, none of them could answer the only question that matters during
 * an exam -- "is this candidate's work actually on the server?" -- because none
 * of them kept a promise you could await, and all of them treated HTTP 200 as
 * success without reading what the response said.
 *
 * What it fixes, concretely:
 *
 *   1. `applied: false` was ignored. The backend already told the truth: a save
 *      refused as stale came back 200 with applied=false, and the client
 *      deleted its pending entry and moved on. The candidate was shown a saved
 *      answer the server had rejected. Now a refusal is reconciled -- adopt the
 *      server's version and re-send the value the candidate can actually see.
 *
 *   2. The version counter restarted at 1 after a page reload while the server
 *      still held 5, so EVERY save after a reload was refused as stale and
 *      silently discarded, by both halves working exactly as designed. `seed`
 *      takes the server's version from the question load and continues from it.
 *
 *   3. A fresh idempotency key was minted per retry, so a retry of a request
 *      that had actually landed looked like a brand new write instead of the
 *      duplicate it was. One key per user change, reused for every retry of
 *      that change.
 *
 *   4. Only network errors were retried. A 429 or a 502 -- exactly what a hall
 *      of candidates saving at once produces -- was dropped on the floor with
 *      no retry and no indication to the candidate.
 *
 *   5. Nothing could be awaited, so submission raced the save it depended on.
 *      `waitForIdle` and `snapshot` exist for that; see Exam.jsx's doSubmit.
 *
 * Deliberately framework-free: no React imports, so it can be tested directly.
 */

// Backoff for retryable failures. Caps rather than grows unboundedly: a
// candidate on a flaky connection needs the next attempt soon, and the queue
// keeps retrying for as long as the exam lasts.
const RETRY_DELAYS_MS = [1000, 2000, 4000, 8000, 15000, 15000, 30000];

// A stale response means our version is behind the server's. Re-sending at the
// server's version + 1 fixes it in one round trip; needing several means
// something else is writing this answer (a second tab), and looping forever
// would turn that into a request storm.
const MAX_RECONCILES = 3;

export const SaveState = {
  SAVED: "saved",
  SAVING: "saving",
  RETRYING: "retrying",
  FAILED: "failed",
};

function newKey() {
  return globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

/**
 * True for failures where trying again can plausibly succeed.
 *
 * 429 and 5xx are in here on purpose: they are the transient, load-shaped
 * failures a whole exam hall submitting at once actually produces, and they
 * used to be treated as permanent. A 4xx that is not 408/429 is a rejection of
 * the request itself -- retrying it just fails again, so it surfaces instead.
 */
function isRetryable(error) {
  const status = error?.status;
  if (status === undefined || status === null) return false;
  return status === 0 || status === 408 || status === 425 || status === 429 || status >= 500;
}

/**
 * @param {object} options
 * @param {object} options.transports - { mcq, multi, code }, each
 *   (questionId, value, envelope) => Promise<response>
 * @param {function} [options.onChange] - called with summary() after any state change
 * @param {function} [options.onExpired] - called if the server reports the attempt
 *   already auto-submitted, which ends the exam rather than being a save failure
 */
export function createAutosaveQueue({ transports, onChange, onExpired } = {}) {
  /** key ("mcq_12") -> entry. One entry per question, holding its LATEST value. */
  const entries = new Map();
  let stopped = false;
  let lastSavedAt = null;

  const keyOf = (kind, questionId) => `${kind}_${questionId}`;

  function entryFor(kind, questionId) {
    const key = keyOf(kind, questionId);
    let entry = entries.get(key);
    if (!entry) {
      entry = {
        key, kind, questionId,
        value: undefined,
        version: 0,
        idempotencyKey: null,
        state: SaveState.SAVED, // nothing to save yet
        dirty: false,
        inFlight: false,
        attempt: 0,
        reconciles: 0,
        timer: null,
        error: null,
      };
      entries.set(key, entry);
    }
    return entry;
  }

  function notify() {
    if (onChange) onChange(summary());
  }

  /**
   * Adopt the server's stored version for a question.
   *
   * Called when a question is loaded, including on the first load after a
   * reload -- which is the case that was losing answers. Never lowers a version
   * we have already used locally: a seed arriving after the candidate has
   * started editing (a slow question load resolving late) must not rewind the
   * counter and make their next save look stale.
   */
  function seed(kind, questionId, serverVersion) {
    const entry = entryFor(kind, questionId);
    const version = Number(serverVersion) || 0;
    if (version > entry.version) entry.version = version;
  }

  /** Record a change the candidate just made, and start persisting it. */
  function save(kind, questionId, value) {
    if (stopped) return;
    const entry = entryFor(kind, questionId);
    entry.value = value;
    entry.version += 1;
    // One key per CHANGE. Every retry of this change reuses it, so a retry of a
    // request that landed but whose response was lost is recognised server-side
    // as a duplicate rather than applied a second time.
    entry.idempotencyKey = newKey();
    entry.dirty = true;
    entry.attempt = 0;
    entry.reconciles = 0;
    entry.error = null;
    entry.state = SaveState.SAVING;
    if (entry.timer) {
      clearTimeout(entry.timer);
      entry.timer = null;
    }
    notify();
    if (!entry.inFlight) void send(entry);
  }

  async function send(entry) {
    if (stopped || entry.inFlight || !entry.dirty) return;
    entry.inFlight = true;

    // Captured before the await: by the time the response arrives the candidate
    // may have changed this answer again, and the entry will hold the newer
    // value. Comparing against these tells us whether the response we got back
    // still describes what is on screen.
    const sentVersion = entry.version;
    const sentKey = entry.idempotencyKey;

    try {
      const response = await transports[entry.kind](entry.questionId, entry.value, {
        answer_version: sentVersion,
        idempotency_key: sentKey,
      });
      entry.inFlight = false;

      if (entry.version !== sentVersion) {
        // Superseded while in flight -- send the newer value instead of
        // reporting this one saved.
        void send(entry);
        return;
      }

      // The server refused the write. It says so in `applied`; the old code
      // read only the HTTP status and reported success regardless.
      if (response && response.applied === false) {
        if (response.outcome === "duplicate") {
          // This exact request already landed. That IS saved.
          markSaved(entry);
          return;
        }
        // Stale: the server holds a version we don't know about. Adopt it and
        // re-send what the candidate can actually see, which is authoritative
        // over whatever is stored.
        const serverVersion = Number(response.answer_version) || 0;
        if (entry.reconciles >= MAX_RECONCILES) {
          fail(entry, new Error("Could not reconcile this answer with the server."));
          return;
        }
        entry.reconciles += 1;
        entry.version = Math.max(entry.version, serverVersion) + 1;
        entry.idempotencyKey = newKey();
        void send(entry);
        return;
      }

      markSaved(entry);
    } catch (error) {
      entry.inFlight = false;

      // The attempt is already over server-side; this is not a save failure and
      // retrying it can only produce the same answer.
      if (isExpiredAttemptError(error)) {
        entry.dirty = false;
        entry.state = SaveState.SAVED;
        notify();
        if (onExpired) onExpired(error.detail.attempt_id);
        return;
      }

      if (!isRetryable(error)) {
        fail(entry, error);
        return;
      }

      const delay = RETRY_DELAYS_MS[Math.min(entry.attempt, RETRY_DELAYS_MS.length - 1)];
      entry.attempt += 1;
      entry.state = SaveState.RETRYING;
      entry.error = error;
      notify();
      entry.timer = setTimeout(() => {
        entry.timer = null;
        void send(entry);
      }, delay);
    }
  }

  function markSaved(entry) {
    entry.dirty = false;
    entry.state = SaveState.SAVED;
    entry.attempt = 0;
    entry.reconciles = 0;
    entry.error = null;
    lastSavedAt = new Date();
    notify();
  }

  function fail(entry, error) {
    // Stays `dirty` on purpose. A failed save is still an answer the candidate
    // believes they gave, so it must remain in snapshot() and go out with the
    // submission -- which is the last chance for it to be recorded.
    entry.state = SaveState.FAILED;
    entry.error = error;
    notify();
  }

  function isExpiredAttemptError(error) {
    return error?.status === 400 && error?.detail?.code === "attempt_expired_auto_submitted";
  }

  /** Force an immediate retry of everything outstanding (e.g. back online). */
  function retryAll() {
    for (const entry of entries.values()) {
      if (!entry.dirty || entry.inFlight) continue;
      if (entry.timer) {
        clearTimeout(entry.timer);
        entry.timer = null;
      }
      entry.attempt = 0;
      entry.state = SaveState.SAVING;
      void send(entry);
    }
    notify();
  }

  /**
   * Resolves once nothing is in flight or waiting to retry.
   *
   * Submission awaits this. It resolves rather than rejects when saves are
   * still failing after the timeout -- the submission must go ahead regardless,
   * carrying snapshot() -- so the caller checks summary().failed to decide what
   * to tell the candidate, and the answers travel with the submission either
   * way. Blocking submission on a save that will never succeed would strand a
   * candidate at the deadline with no way to hand in their paper.
   */
  function waitForIdle({ timeoutMs = 8000 } = {}) {
    const deadline = Date.now() + timeoutMs;
    return new Promise((resolve) => {
      const check = () => {
        const outstanding = [...entries.values()].filter((e) => e.dirty && e.state !== SaveState.FAILED);
        if (outstanding.length === 0 || Date.now() >= deadline) {
          resolve(summary());
          return;
        }
        setTimeout(check, 120);
      };
      // Anything sitting in backoff gets one immediate push rather than waiting
      // out a 15-second delay while the candidate stares at "Submitting…".
      retryAll();
      check();
    });
  }

  /**
   * Every answer the candidate has touched, in the shape the finalize endpoint
   * takes -- including ones whose autosave failed, which is the point.
   */
  function snapshot() {
    const items = [];
    for (const entry of entries.values()) {
      if (entry.value === undefined) continue;
      const item = {
        question_id: entry.questionId,
        answer_version: entry.version,
        idempotency_key: entry.idempotencyKey || newKey(),
      };
      if (entry.kind === "mcq") item.selected_option_id = entry.value;
      else if (entry.kind === "multi") item.selected_option_ids = entry.value || [];
      else if (entry.kind === "code") item.source_code = entry.value ?? "";
      items.push(item);
    }
    return items;
  }

  function summary() {
    let pending = 0;
    let failed = 0;
    for (const entry of entries.values()) {
      if (entry.state === SaveState.FAILED) failed += 1;
      else if (entry.dirty) pending += 1;
    }
    return {
      pending,
      failed,
      lastSavedAt,
      state: failed ? SaveState.FAILED : pending ? SaveState.SAVING : SaveState.SAVED,
    };
  }

  /** Per-question state, for the question navigator. */
  function stateFor(kind, questionId) {
    return entries.get(keyOf(kind, questionId))?.state ?? SaveState.SAVED;
  }

  function stop() {
    stopped = true;
    for (const entry of entries.values()) {
      if (entry.timer) clearTimeout(entry.timer);
      entry.timer = null;
    }
  }

  return { seed, save, waitForIdle, snapshot, summary, stateFor, retryAll, stop };
}
