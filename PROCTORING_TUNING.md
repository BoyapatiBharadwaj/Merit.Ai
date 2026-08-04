# Proctoring tuning guide

What changed, what to watch for when you test with a real webcam, and exactly
which number to move if a signal still misbehaves.

---

## What changed

### 1. Face, multi-face, head pose and gaze now run in your browser

Previously each of these was a JPEG POSTed to the backend — face every 3s,
pose/gaze every 5s — with server-side MediaPipe inference serialized behind a
global lock. That produced all three symptoms you reported: lag (a verdict
arrived hundreds of ms after the frame it described), missed changes (anything
faster than the poll interval fell between samples), and degradation under
load (concurrent students queued on the same lock).

They now run locally at **~12fps** via MediaPipe Tasks Vision
(`frontend/src/lib/faceMesh.js`). No network in the loop.

**Head pose and gaze are now measured relative to your own calibrated neutral
posture**, captured over the first ~1 second of tracking, rather than as
absolute angles. This matters: people sit differently, cameras sit at
different heights, and a tilted laptop screen puts an attentive student at a
permanent 15° pitch. It also means a constant offset in the angle convention
cancels out — which is what caused the old "student staring straight ahead
decomposes to pitch = −173° and is flagged every check" bug.

### 2. Identity matching stays on the server, at 12s instead of 3s

ArcFace still answers "is this the enrolled student" — that needs the enrolled
embedding, which should never leave the server. This is not a downgrade:
presence used to ride on this same poll and is now continuous, so the interval
only governs how often identity is re-confirmed. ~4x less server work per
attempt.

### 3. Anti-spoofing no longer cries wolf

**This was the root cause of your constant spoof warnings.** No trained model
is installed (`backend/models/antispoof/` does not exist), so the *classical
heuristic* was running — and `verify_live_frame` was copying its advisory
verdict straight into `spoof_suspected`, which the frontend turns into a
violation plus a toast. Every few seconds. All exam long. From a heuristic
whose own module docstring admits it false-positived on the first real face it
ever saw.

Now only a **trained model** may accuse. The heuristic's opinion is still
recorded (`liveness_score` rides on every response, concerns are logged
server-side) but never raises a violation on its own.

To get a real enforcing gate, drop a MiniFASNet-style ONNX classifier at
`backend/models/antispoof/antispoof.onnx` and run:

```
python -m app.utils.fetch_models --only antispoof
```

which verifies it loads and reports its input shape / head size. Widely-used
ONNX exports: [`garciafido/minifasnet-v2-anti-spoofing-onnx`](https://huggingface.co/garciafido/minifasnet-v2-anti-spoofing-onnx)
or [`yakhyo/face-anti-spoofing`](https://github.com/yakhyo/face-anti-spoofing).
No restart or config change needed beyond placing the file.

### 4. Microphone is much less trigger-happy

Old behaviour: warned after **2 seconds** over a margin of 14 above a noise
floor hardcoded to start at 10 — a level a desk fan clears — and the floor
only ever adapted *downward* while quiet, so a genuinely noisy room never
recalibrated and warned forever.

Now: a 5-frame calibration period seeds the floor from your actual room before
anything can fire, margins are 24/38, a moderate flag needs **~6 seconds**
sustained, the floor also drifts up slowly during sustained noise, and a
30-second cooldown stops re-triggering.

### 5. Examiners can rename sections (draft only)

`PATCH /exams/sections/{id}`, with a "Rename" affordance on each section
header in the builder. Draft-only, same freeze rule as questions and options —
a section title is part of the paper candidates see, so it must not change
under a live attempt.

---

## What to check when you test

Open the browser devtools console during an exam. In rough order of value:

1. **Does the landmarker load at all?** If the CDN is blocked or WebGL is
   unavailable you'll silently fall back to the old server-polled path (that's
   deliberate). You'll know because pose/gaze will feel slow again. Check the
   Network tab for `cdn.jsdelivr.net/npm/@mediapipe/tasks-vision`.
2. **Calibration.** For the first ~1 second the status reads "Calibrating…".
   Sit normally during it. If you calibrate while already turned away, the
   baseline is wrong for the rest of the exam.
3. **Head pose.** Turn slowly toward an imaginary second screen. It should
   flag after ~2 seconds sustained. Normal reading posture and glancing at the
   keyboard should *not* flag.
4. **Gaze.** Same, with eyes only.
5. **Multi-face.** Have someone lean into frame for 2 seconds.
6. **Mic.** Talk normally for ~8 seconds. Should flag once, then stay quiet
   for 30s.

---

## Which number to move

All in `frontend/src/lib/faceMesh.js` unless noted.

| Symptom | Change | Direction |
|---|---|---|
| "Looking away" fires on normal posture | `YAW_DEVIATION_DEG` (28), `PITCH_DEVIATION_DEG` (24) | raise |
| Never catches a real turn | same two | lower |
| Gaze too sensitive | `GAZE_DEVIATION` (0.19) | raise |
| Flags take too long | `REQUIRED_STREAK.lookingAway` / `.gazeDeviation` in `proctoring.js` (currently `2 * LOCAL_FPS` = ~2s) | lower |
| "No face" on a blink/lean | `REQUIRED_STREAK.noFace` (30 ≈ 2.5s) | raise |
| Second face flagged from a poster/reflection | `REQUIRED_STREAK.multipleFaces` (12 ≈ 1s) | raise |
| CPU too high on student laptops | `DETECT_INTERVAL_MS` (80 ≈ 12fps) | raise to 120–160 |
| Mic still too sensitive | `NOISE_FLOOR_MARGIN_MODERATE` (24) in `proctoring.js` | raise |
| Mic never fires | same | lower |
| Mic repeats too often | `NOISE_COOLDOWN_FRAMES` (30) | raise |

**If you report back with what misfired**, the most useful thing is the
console values from a moment it got it wrong — the `yaw=` / `pitch=` /
`offset=` numbers in the violation description are all relative to your
calibrated neutral, so they tell me directly how far off the thresholds are.

---

## Known limitations, stated honestly

- **The gaze signal is a heuristic, not a calibrated gaze model.** It answers
  "is the iris noticeably off-centre in the socket", which catches someone
  repeatedly looking hard to one side. It cannot tell you *where* on screen
  someone is looking. A real gaze estimator (L2CS-Net, MPIIGaze) needs
  per-user calibration and its own model.
- **Object detection (phone/book/person count) still polls the server** every
  10s. YOLO is too heavy to run client-side alongside the landmarker. If phone
  detection feels laggy, that's why — and it's a deliberate trade.
- **The head-pose euler convention is untested against a real camera.** I
  could not run a webcam from my environment. The relative-to-baseline design
  makes this robust to a constant offset, but if yaw and pitch feel *swapped*
  or *inverted*, that's the thing to tell me — it's a two-line fix in
  `eulerFromMatrix`.
- **MediaPipe is loaded from a pinned CDN version** (`0.10.14`). If your
  institution blocks jsDelivr you'll want to self-host the WASM bundle and the
  `.task` model file and repoint `TASKS_VISION_URL` / `MODEL_URL`.
