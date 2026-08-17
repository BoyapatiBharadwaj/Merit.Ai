# Proctoring tuning guide

Where each signal runs, what to verify against a real webcam, and exactly which
constant to move when a signal misbehaves.

---

## Where each signal runs

| Signal | Location | Rate |
|---|---|---|
| Face presence, face count, head pose, gaze | Browser — MediaPipe Tasks Vision (`frontend/src/lib/faceMesh.js`) | ~12 fps |
| Identity match | Server — ArcFace | Every 12 s |
| Object detection (phone, book, extra person) | Server — YOLO11s | Every 5 s |
| Liveness / anti-spoofing | Server | With each identity check |
| Noise | Browser — Web Audio | Every 1 s |
| External monitor | Browser — Window Management API | Every 30 s |
| Tab switch, fullscreen exit, copy/paste | Browser — DOM events | Event-driven |

Tracking runs in the browser because a poll interval is a floor on both latency
and detection: anything faster than the interval falls between samples, and every
verdict arrives after the frame it describes. At ~12 fps a glance away is caught
within about 80 ms, and no exam traffic is involved.

Identity matching stays on the server because it needs the enrolled embedding,
which should never leave it. Presence no longer rides on that poll, so the
12-second interval governs only how often identity is *re*-confirmed.

Object detection also stays server-side: YOLO is too heavy to run in the browser
alongside the landmarker. If phone detection feels less immediate than head pose,
that is why, and it is a deliberate trade.

## Calibration

Head pose and gaze are measured **relative to the candidate's own neutral
posture**, captured over the first ~1 second of tracking (`CALIBRATION_FRAMES`,
12 frames), not as absolute angles.

This matters because people sit differently, cameras sit at different heights,
and a tilted laptop screen puts an attentive candidate at a permanent 15° pitch.
It also means a constant offset in the angle convention cancels out rather than
flagging a candidate who is looking straight ahead.

The status panel reads "Calibrating…" during this window. A candidate who
calibrates while already turned away carries a wrong baseline for the rest of the
exam.

## Anti-spoofing

Only a **trained model** may raise a spoofing violation. The built-in classical
heuristic — face-region texture, colour spread, glare, and an FFT screen-moiré
cue — still runs, and its `liveness_score` rides on every response and is logged
server-side, but it never accuses on its own. It is advisory input for a reviewer,
not an enforcing gate.

To enable an enforcing gate, place a MiniFASNet-style ONNX classifier at
`backend/models/antispoof/antispoof.onnx` and verify it:

```bash
python -m app.utils.fetch_models --only antispoof
```

This confirms the model loads and reports its input shape and head size. No
restart or configuration change is needed beyond placing the file. Widely used
exports include
[`garciafido/minifasnet-v2-anti-spoofing-onnx`](https://huggingface.co/garciafido/minifasnet-v2-anti-spoofing-onnx)
and [`yakhyo/face-anti-spoofing`](https://github.com/yakhyo/face-anti-spoofing).

There is no canonical URL for such an export, so `fetch_models` tells you where to
put one rather than auto-pulling an unverified binary — a bad trade for something
that decides whether a candidate is cheating.

## Noise detection

A voice-activity heuristic rather than a raw volume threshold: speech-band energy
ratio against an adaptive ambient floor.

A 5-frame calibration period seeds the floor from the actual room before anything
can fire. Margins are 24 dB (moderate) and 38 dB (loud) above that floor, a
moderate flag needs ~6 seconds sustained, the floor drifts *up* slowly during
sustained noise as well as down while quiet, and a 30-second cooldown prevents
re-triggering on the same conversation.

## Verifying against a real webcam

With the browser devtools console open during an exam, in rough order of value:

1. **Does the landmarker load?** If the model file is unreachable or WebGL is
   unavailable, the client falls back to the server-polled path by design —
   pose and gaze will feel slower. Check the Network tab.
   `npm run fetch:model` self-hosts the weights at
   `/mediapipe/face_landmarker.task`; without it the client falls back to
   Google's hosted copy and logs a warning.
2. **Calibration.** Sit normally for the first second.
3. **Head pose.** Turn slowly toward an imaginary second screen — it should flag
   after ~2 seconds sustained. Normal reading posture and glancing at the
   keyboard should not.
4. **Gaze.** The same test, eyes only.
5. **Multiple faces.** Have someone lean into frame for ~2 seconds.
6. **Noise.** Talk normally for ~8 seconds. It should flag once, then stay quiet
   for 30 seconds.

The `yaw=`, `pitch=`, and `offset=` values in each violation description are
relative to the calibrated neutral, so they show directly how far from the
threshold a misfire was.

## Which constant to move

In `frontend/src/lib/faceMesh.js` unless noted.

| Symptom | Constant | Direction |
|---|---|---|
| "Looking away" fires on normal posture | `YAW_DEVIATION_DEG` (28), `PITCH_DEVIATION_DEG` (24) | raise |
| Never catches a real turn | the same two | lower |
| Gaze too sensitive | `GAZE_DEVIATION` (0.19) | raise |
| Flags take too long | `REQUIRED_STREAK.lookingAway` / `.gazeDeviation` in `proctoring.js` (`2 * LOCAL_FPS` ≈ 2 s) | lower |
| "No face" on a blink or lean | `REQUIRED_STREAK.noFace` (30 ≈ 2.5 s) | raise |
| Second face flagged from a poster or reflection | `REQUIRED_STREAK.multipleFaces` (12 ≈ 1 s) | raise |
| CPU too high on candidate laptops | `DETECT_INTERVAL_MS` (80 ≈ 12 fps) | raise to 120–160 |
| Noise too sensitive | `NOISE_FLOOR_MARGIN_MODERATE` (24) in `proctoring.js` | raise |
| Noise never fires | the same | lower |
| Noise repeats too often | `NOISE_COOLDOWN_FRAMES` (30) | raise |
| Identity re-checked too often | `FACE_IDENTITY_INTERVAL_MS` (12000) in `proctoring.js` | raise |
| Object detection too slow to react | `OBJECT_CHECK_INTERVAL_MS` (5000) in `proctoring.js` | lower |

Server-side thresholds live in `backend/app/ai/anti_spoof.py` (liveness) and
`backend/app/core/config.py` (`FACE_MATCH_TOLERANCE`, the per-signal kill
switches, and the lockdown strike limit and debounce).

## Known limitations

- **Gaze is a heuristic, not a calibrated gaze model.** It answers "is the iris
  noticeably off-centre in the socket", which catches someone repeatedly looking
  hard to one side. It cannot tell you *where* on screen someone is looking. A
  real estimator (L2CS-Net, MPIIGaze) needs per-user calibration and its own
  model.
- **The head-pose Euler convention has not been validated against a physical
  camera.** The relative-to-baseline design is robust to a constant offset, but if
  yaw and pitch appear swapped or inverted, that is a two-line fix in
  `eulerFromMatrix`.
- **MediaPipe is pinned to version 0.10.14.** The WASM bundle is vendored out of
  `node_modules` by `npm run vendor:mediapipe`, wired into both `npm run build`
  and `npm run dev` so it cannot be forgotten. Run `npm run fetch:model` to
  self-host the `.task` weights too before a production deployment.
- **Object detection is only as good as the weights being present.** Until
  `fetch_models --only yolo` has run, `POST /proctoring/objects/detect` reports
  itself unavailable and the frontend stops polling it — by design, but it does
  mean phone, book, and second-person detection is off until the weights exist.
