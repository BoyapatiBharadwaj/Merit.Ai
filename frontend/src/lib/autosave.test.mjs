/**
 * The autosave queue, tested against the failures it was written to fix.
 */
import assert from "node:assert/strict";
import test from "node:test";

import { createAutosaveQueue, SaveState } from "./autosave.js";

/** A transport that records what it was sent and replies with a script. */
function recordingTransport(responses = []) {
  const calls = [];
  let index = 0;
  const transport = async (questionId, value, envelope) => {
    calls.push({ questionId, value, ...envelope });
    const reply = responses[Math.min(index, responses.length - 1)];
    index += 1;
    if (typeof reply === "function") return reply(calls.length);
    if (reply instanceof Error) throw reply;
    return reply ?? { applied: true };
  };
  return { transport, calls };
}

function makeQueue(mcq, extra = {}) {
  return createAutosaveQueue({
    transports: { mcq, multi: mcq, code: mcq },
    ...extra,
  });
}

/** Let the queue's promise chain settle. */
const settle = (ms = 30) => new Promise((resolve) => setTimeout(resolve, ms));

function httpError(status) {
  const error = new Error(`status ${status}`);
  error.status = status;
  return error;
}

// --- the version the server actually holds ------------------------------------

test("a seeded version continues the server's sequence instead of restarting", async () => {
  const { transport, calls } = recordingTransport();
  const queue = makeQueue(transport);

  // A freshly reloaded page learns the server is at 5.
  queue.seed("mcq", 7, 5);
  queue.save("mcq", 7, 42);
  await settle();

  assert.equal(calls.length, 1);
  assert.equal(calls[0].answer_version, 6,
    "restarted the counter -- the server would refuse this as stale, and the page would say it saved");
});

test("a late seed cannot rewind a counter already in use", async () => {
  const { transport, calls } = recordingTransport();
  const queue = makeQueue(transport);

  queue.seed("mcq", 7, 5);
  queue.save("mcq", 7, 1); // version 6
  await settle();
  // A slow question load resolving after the candidate started editing.
  queue.seed("mcq", 7, 2);
  queue.save("mcq", 7, 2);
  await settle();

  assert.equal(calls[1].answer_version, 7, "a stale seed rewound the version");
});

// --- applied: false -----------------------------------------------------------

test("a stale rejection is reconciled and resent, not reported as saved", async () => {
  const { transport, calls } = recordingTransport([
    { applied: false, outcome: "stale", answer_version: 9 },
    { applied: true },
  ]);
  const queue = makeQueue(transport);

  queue.save("mcq", 3, 55);
  await settle();

  assert.equal(calls.length, 2, "the refusal was accepted as success");
  assert.equal(calls[1].answer_version, 10, "did not adopt the server's version");
  assert.equal(calls[1].value, 55, "resent something other than what is on screen");
  assert.equal(queue.summary().state, SaveState.SAVED);
});

test("a duplicate response counts as saved", async () => {
  const { transport } = recordingTransport([{ applied: false, outcome: "duplicate" }]);
  const queue = makeQueue(transport);

  queue.save("mcq", 3, 55);
  await settle();

  assert.equal(queue.summary().pending, 0);
  assert.equal(queue.summary().failed, 0);
});

test("endless staleness gives up rather than looping", async () => {
  const { transport, calls } = recordingTransport([
    () => ({ applied: false, outcome: "stale", answer_version: 100 }),
  ]);
  const queue = makeQueue(transport);

  queue.save("mcq", 3, 1);
  await settle(80);

  assert.ok(calls.length <= 5, `reconciled ${calls.length} times -- that is a request storm`);
  assert.equal(queue.summary().failed, 1);
});

// --- idempotency --------------------------------------------------------------

test("one idempotency key per change, reused across retries", async () => {
  const { transport, calls } = recordingTransport([httpError(0), { applied: true }]);
  const queue = makeQueue(transport);

  queue.save("mcq", 4, 9);
  await settle(1400); // past the first backoff

  assert.equal(calls.length, 2);
  assert.equal(calls[0].idempotency_key, calls[1].idempotency_key,
    "a retry minted a new key, so a request that landed looked like a new write");
  assert.equal(calls[0].answer_version, calls[1].answer_version);
});

test("a new change gets a new key", async () => {
  const { transport, calls } = recordingTransport();
  const queue = makeQueue(transport);

  queue.save("mcq", 4, 1);
  await settle();
  queue.save("mcq", 4, 2);
  await settle();

  assert.notEqual(calls[0].idempotency_key, calls[1].idempotency_key);
});

// --- which failures are retried ------------------------------------------------

for (const status of [0, 429, 500, 503]) {
  test(`a ${status} is retried`, async () => {
    const { transport, calls } = recordingTransport([httpError(status), { applied: true }]);
    const queue = makeQueue(transport);

    queue.save("mcq", 5, 1);
    await settle(1400);

    assert.equal(calls.length, 2,
      `${status} was dropped -- exactly what a hall of candidates saving at once produces`);
  });
}

test("a 400 is not retried, and is surfaced", async () => {
  const { transport, calls } = recordingTransport([httpError(400)]);
  const queue = makeQueue(transport);

  queue.save("mcq", 5, 1);
  await settle(1400);

  assert.equal(calls.length, 1, "retrying a rejected request just fails again");
  assert.equal(queue.summary().failed, 1);
});

// --- submission ----------------------------------------------------------------

test("waitForIdle resolves once everything has landed", async () => {
  const { transport } = recordingTransport([
    async () => { await settle(40); return { applied: true }; },
  ]);
  const queue = makeQueue(transport);

  queue.save("mcq", 1, 10);
  const summary = await queue.waitForIdle({ timeoutMs: 2000 });

  assert.equal(summary.pending, 0, "submission would have raced this save");
});

test("waitForIdle resolves rather than hanging when a save cannot succeed", async () => {
  const { transport } = recordingTransport([httpError(400)]);
  const queue = makeQueue(transport);

  queue.save("mcq", 1, 10);
  const summary = await queue.waitForIdle({ timeoutMs: 500 });

  // Blocking submission on a save that will never succeed would strand a
  // candidate at the deadline with no way to hand in their paper.
  assert.equal(summary.failed, 1);
});

test("the snapshot carries answers whose save failed", async () => {
  const { transport } = recordingTransport([httpError(400)]);
  const queue = makeQueue(transport);

  queue.save("mcq", 12, 77);
  await settle();

  const snapshot = queue.snapshot();
  assert.equal(snapshot.length, 1);
  assert.equal(snapshot[0].question_id, 12);
  assert.equal(snapshot[0].selected_option_id, 77,
    "a failed save must still reach the server with the submission -- it is the last chance");
});

test("the snapshot shapes each answer by its type", async () => {
  const { transport } = recordingTransport();
  const queue = makeQueue(transport);

  queue.save("mcq", 1, 5);
  queue.save("multi", 2, [7, 8]);
  queue.save("code", 3, "print(1)");
  await settle();

  const byQuestion = Object.fromEntries(queue.snapshot().map((i) => [i.question_id, i]));
  assert.equal(byQuestion[1].selected_option_id, 5);
  assert.deepEqual(byQuestion[2].selected_option_ids, [7, 8]);
  assert.equal(byQuestion[3].source_code, "print(1)");
});

// --- coalescing ----------------------------------------------------------------

test("a change made while a save is in flight sends the newer value", async () => {
  const { transport, calls } = recordingTransport([
    async () => { await settle(40); return { applied: true }; },
    { applied: true },
  ]);
  const queue = makeQueue(transport);

  queue.save("mcq", 6, "first");
  await settle(5);
  queue.save("mcq", 6, "second");
  await queue.waitForIdle({ timeoutMs: 2000 });

  assert.equal(calls[calls.length - 1].value, "second",
    "the queue reported success for a value the candidate had already replaced");
});

// --- the expired attempt --------------------------------------------------------

test("an expired attempt ends the exam rather than retrying forever", async () => {
  const expired = new Error("expired");
  expired.status = 400;
  expired.detail = { code: "attempt_expired_auto_submitted", attempt_id: 99 };

  let reported = null;
  const { transport, calls } = recordingTransport([expired]);
  const queue = makeQueue(transport, { onExpired: (id) => { reported = id; } });

  queue.save("mcq", 1, 1);
  await settle(1400);

  assert.equal(reported, 99);
  assert.equal(calls.length, 1, "kept retrying an attempt that is already over");
});
