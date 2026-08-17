/**
 * Client-side AI proctoring controller — a React-friendly
 * port of the legacy frontend/js/proctoring.js.
 */
import { Api, ApiError } from "./api.js";
import { createFaceTracker } from "./faceMesh.js";

// Presence, face count, head pose and gaze are now tracked locally at ~12fps by lib/faceMesh.js
// instead of being polled off the server.
const FACE_IDENTITY_INTERVAL_MS = 12000;

// 10s -> 5s. A phone held up for eight seconds could previously
// fall entirely between two polls and never be seen at all.
const OBJECT_CHECK_INTERVAL_MS = 5000;

// Longest edge, in pixels, of every frame uploaded for server-side inference.
const CAPTURE_MAX_EDGE = 640;
// 0.72 rather than 0.7: at 640px the file is small enough that the extra few KB is free, and
// JPEG artefacts hurt a face-embedding model more than they hurt a human viewer.
const CAPTURE_QUALITY = 0.72;
const MONITOR_CHECK_INTERVAL_MS = 30000;
const NOISE_CHECK_INTERVAL_MS = 1000;
const SPEECH_BAND_HZ = [85, 3400];
const SPEECH_RATIO_THRESHOLD = 0.45;
// Two audio tiers instead of one: a lower bar for "there's background noise/
// chatter" (still worth a warning, but common enough to happen innocently)
// and a higher bar for "sustained loud talking," which is logged as its own,
// more severe event (see EventType.NOISE_DETECTED_LOUD on the backend).
const NOISE_FLOOR_MARGIN_MODERATE = 24;
const NOISE_FLOOR_MARGIN_LOUD = 38;
const REQUIRED_CONSECUTIVE_FRAMES_MODERATE = 6;
const REQUIRED_CONSECUTIVE_FRAMES_LOUD = 5;
// Once a tier has fired, stay quiet for this many checks before it can fire again.
const NOISE_COOLDOWN_FRAMES = 30;
// Streaks are counted in *detector frames*, and the local tracker runs
// at ~12fps rather than the old one-poll-every-5-seconds, so these had
// to be rescaled or a 4-frame streak would mean a third of a second.
const LOCAL_FPS = 12;
const REQUIRED_STREAK = {
  lookingAway: 2 * LOCAL_FPS,
  gazeDeviation: 2 * LOCAL_FPS,
  // Object-detector person count, still polled from the server every 10s (YOLO is too heavy to
  // run client-side alongside the landmarker).
  multiplePersons: 1,
  // ~2.5s of continuous absence. A blink, a head-scratch, or a
  // single dropped frame must not read as "the student left".
  noFace: 30,
  // Two faces for ~1s. The local tracker sees every frame
  // now, so a single frame of a misdetected background face.
  multipleFaces: 12,
  // How many consecutive "detector unavailable" replies before
  // object monitoring gives up for the rest of the exam.
  objectsUnavailable: 3,
};

/**
 * A repeating async check that never overlaps itself.
 */
function repeatWithoutOverlap(fn, intervalMs) {
  let timer = null;
  let cancelled = false;

  async function run() {
    if (cancelled) return;
    try {
      await fn();
    } catch {
      // Never let one failed check stop the loop -- an offline moment must not
      // silently end proctoring for the rest of the exam.
    } finally {
      if (!cancelled) timer = setTimeout(run, intervalMs);
    }
  }

  timer = setTimeout(run, intervalMs);
  return {
    cancel() {
      cancelled = true;
      if (timer) clearTimeout(timer);
      timer = null;
    },
  };
}


export function createProctoring() {
  let attemptId = null;
  let videoEl = null;
  let statusEl = null;
  let captureCanvas = null;
  let faceCheckInterval = null;
  let audioContext, analyser, micStream, noiseCheckInterval;
  let objectCheckInterval = null;
  let poseCheckInterval = null;
  let monitorCheckInterval = null;
  let onStatusUpdate = null;
  let onViolationLogged = null;
  let onToast = null;
  let logEvent = null;
  let violationCount = 0;
  let stopped = false;
  let alertAudioCtx = null;
  let faceTracker = null;
  // True once the local landmarker has delivered a frame.
  let localTrackingActive = false;
  // Latched by the server identity check so local tracking doesn't reset a real mismatch back
  // to "verified" 12 times a second while the wrong person is still sitting in frame.
  let identityMismatch = false;

  const streaks = { lookingAway: 0, gazeDeviation: 0, multiplePersons: 0, noFace: 0, multipleFaces: 0 };

  function reportSignal(name, state) {
    if (onStatusUpdate) onStatusUpdate(name, state);
  }

  async function startCamera() {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ video: true, audio: false });
      if (stopped) {
        stream.getTracks().forEach((t) => t.stop());
        return;
      }
      videoEl.srcObject = stream;
    } catch {
      logViolation("no_face", "Camera access denied or unavailable.");
      setStatus("Camera unavailable", "warn");
      reportSignal("face", "unavailable");
      toast("Camera access is required for this exam. Please allow camera access and reload.");
    }
  }

  /**
   * A JPEG of the current video frame, downscaled to CAPTURE_MAX_EDGE.
   */
  function captureFrame(maxEdge = CAPTURE_MAX_EDGE) {
    const sourceW = videoEl.videoWidth || 320;
    const sourceH = videoEl.videoHeight || 240;
    // Never upscale -- a 480p webcam should stay 480p, not be interpolated up
    // to 640 and then re-encoded, which adds bytes and no detail.
    const scale = Math.min(1, maxEdge / Math.max(sourceW, sourceH));
    const width = Math.max(1, Math.round(sourceW * scale));
    const height = Math.max(1, Math.round(sourceH * scale));

    if (!captureCanvas) captureCanvas = document.createElement("canvas");
    if (captureCanvas.width !== width || captureCanvas.height !== height) {
      captureCanvas.width = width;
      captureCanvas.height = height;
    }
    const ctx = captureCanvas.getContext("2d", { alpha: false });
    // The browser's built-in smoothing is what makes a downscale look like a
    // resize rather than a nearest-neighbour crunch; "high" matters for small
    // faces, which is exactly what the identity model needs to read.
    ctx.imageSmoothingEnabled = true;
    ctx.imageSmoothingQuality = "high";
    ctx.drawImage(videoEl, 0, 0, width, height);
    return captureCanvas.toDataURL("image/jpeg", CAPTURE_QUALITY);
  }

  /**
   * Presence, face count, head pose and gaze -- all local, ~12fps, no network.
   */
  function startLocalTracking() {
    if (stopped) return;
    faceTracker = createFaceTracker();
    faceTracker.start(
      videoEl,
      (r) => {
        if (stopped) return;
        localTrackingActive = true;

        if (r.calibrating) {
          setStatus("Calibrating…", "pending");
          reportSignal("face", "pending");
          reportSignal("pose", "pending");
          reportSignal("gaze", "pending");
          return;
        }

        // --- presence / face count -------------------------------------
        if (r.faceCount === 0) {
          streaks.multipleFaces = 0;
          streaks.noFace += 1;
          setStatus("No face detected", "warn");
          reportSignal("face", "warn");
          if (streaks.noFace === REQUIRED_STREAK.noFace) {
            logViolation("no_face", "No face visible in the webcam for ~2.5 seconds.");
            toast("No face detected. Please stay visible in the camera frame.");
          }
        } else if (r.faceCount > 1) {
          streaks.noFace = 0;
          streaks.multipleFaces += 1;
          setStatus("Multiple faces detected", "warn");
          reportSignal("face", "warn");
          if (streaks.multipleFaces === REQUIRED_STREAK.multipleFaces) {
            logViolation("multiple_faces", `${r.faceCount} faces detected in the webcam frame.`);
            toast("Multiple faces detected. Only you should be in frame.");
          }
        } else {
          streaks.noFace = 0;
          streaks.multipleFaces = 0;
          // Presence is confirmed locally; whether it's the *right* person is the server
          // identity check's job, and it owns the "face" signal once it has an answer.
          if (!identityMismatch) {
            setStatus("Face verified", "ok");
            reportSignal("face", "ok");
          }
        }

        // --- head pose --------------------------------------------------
        if (r.lookingAway === null) {
          streaks.lookingAway = 0;
        } else if (r.lookingAway) {
          streaks.lookingAway += 1;
          if (streaks.lookingAway === REQUIRED_STREAK.lookingAway) {
            logViolation("looking_away", `Sustained head turn away from the screen (yaw=${r.yaw}, pitch=${r.pitch} relative to calibrated neutral).`);
            toast("Please keep looking at the screen.");
          }
        } else {
          streaks.lookingAway = 0;
        }
        reportSignal("pose", streaks.lookingAway > 0 ? "warn" : "ok");

        // --- gaze -------------------------------------------------------
        if (r.gazeDeviation === null) {
          streaks.gazeDeviation = 0;
        } else if (r.gazeDeviation) {
          streaks.gazeDeviation += 1;
          if (streaks.gazeDeviation === REQUIRED_STREAK.gazeDeviation) {
            logViolation("gaze_deviation", `Sustained gaze deviation detected (offset=${r.gazeOffset} from calibrated neutral).`);
            toast("Please keep your eyes on the screen.");
          }
        } else {
          streaks.gazeDeviation = 0;
        }
        reportSignal("gaze", streaks.gazeDeviation > 0 ? "warn" : "ok");
      },
      () => {
        // Landmarker unavailable -- fall back to the server-polled pose
        // endpoint so these signals still exist, just at the old cadence.
        localTrackingActive = false;
        startServerPoseMonitoring();
      },
    );
  }

  /**
   * Identity only: "is the face in frame the enrolled student". Presence and face count are
   * handled locally now, so this ignores those fields and reacts solely to `match`.
   */
  function startIdentityMonitoring() {
    if (stopped) return;
    faceCheckInterval = repeatWithoutOverlap(async () => {
      if (!videoEl || !videoEl.videoWidth) return;
      try {
        const frame = captureFrame();
        const result = await Api.exam.post("/proctoring/face/verify", { image_base64: frame });

        // available=false means the signal was not collected -- the operator switched face
        // matching off (FACE_MATCHING_ENABLED), or no model could load.
        if (result.available === false) {
          setStatus("Face matching unavailable", "idle");
          reportSignal("face", "idle");
          if (faceCheckInterval) faceCheckInterval.cancel();
          faceCheckInterval = null;
          return;
        }

        // spoof_suspected is only ever set by a *trained* anti-spoofing model now; the
        // classical heuristic no longer accuses on its own (see
        // backend/app/ai/face_service.py).
        if (result.spoof_suspected) {
          setStatus("Possible spoof detected", "warn");
          reportSignal("face", "warn");
          logViolation("spoof_detected", `Liveness check failed (score=${result.liveness_score}). ${result.message || ""}`.trim());
          toast("Possible spoofing detected. Please use your own live camera, not a photo or screen.");
          return;
        }

        if (result.match === false) {
          identityMismatch = true;
          setStatus("Face mismatch", "warn");
          reportSignal("face", "warn");
          logViolation("face_mismatch", `Live face did not match registered profile (distance=${result.distance}).`);
          toast("Face verification failed. Please make sure your face is clearly visible.");
        } else if (result.match === true) {
          identityMismatch = false;
          // Only assert "verified" when local tracking isn't already driving
          // this signal, so the two don't fight over it every few seconds.
          if (!localTrackingActive) {
            setStatus("Face verified", "ok");
            reportSignal("face", "ok");
          }
        }
        // result.match == null -> inconclusive (no usable
        // stored profile, or no encoding for this frame).
      } catch {
        // Fail quietly (profile not registered yet, transient network hiccup).
      }
    }, FACE_IDENTITY_INTERVAL_MS);
  }

  function startObjectMonitoring() {
    if (stopped) return;
    let unavailableStreak = 0;
    objectCheckInterval = repeatWithoutOverlap(async () => {
      if (!videoEl || !videoEl.videoWidth) return;
      try {
        const frame = captureFrame();
        const result = await Api.exam.post("/proctoring/objects/detect", { image_base64: frame });
        if (!result.available) {
          // Two very different things arrive as available=false.
          unavailableStreak += 1;
          if (unavailableStreak >= REQUIRED_STREAK.objectsUnavailable) {
            objectCheckInterval.cancel();
            objectCheckInterval = null;
            reportSignal("objects", "unavailable");
          }
          return;
        }
        unavailableStreak = 0;
        let flagged = false;
        if (result.person_count > 1) {
          streaks.multiplePersons += 1;
          if (streaks.multiplePersons === REQUIRED_STREAK.multiplePersons) {
            logViolation("multiple_persons_detected", `${result.person_count} people detected in frame.`);
            toast("Multiple people detected in frame.");
          }
          flagged = true;
        } else {
          streaks.multiplePersons = 0;
        }
        for (const detection of result.detections || []) {
          if (detection.label === "cell phone") {
            logViolation("phone_detected", `Phone detected in frame (confidence=${detection.confidence}).`);
            toast("Phone detected in frame. Please put it away.");
            flagged = true;
          } else if (detection.label === "book") {
            logViolation("book_detected", `Book detected in frame (confidence=${detection.confidence}).`);
            toast("Book or notes detected in frame.");
            flagged = true;
          }
        }
        reportSignal("objects", flagged ? "warn" : "ok");
      } catch {
        // ignore
      }
    }, OBJECT_CHECK_INTERVAL_MS);
  }

  /**
   * FALLBACK ONLY.
   */
  const POSE_CHECK_INTERVAL_MS = 5000;
  const FALLBACK_STREAK_DIVISOR = LOCAL_FPS * (POSE_CHECK_INTERVAL_MS / 1000);

  function startServerPoseMonitoring() {
    if (stopped) return;
    const fallbackStreak = {
      lookingAway: Math.max(2, Math.round(REQUIRED_STREAK.lookingAway / FALLBACK_STREAK_DIVISOR)),
      gazeDeviation: Math.max(2, Math.round(REQUIRED_STREAK.gazeDeviation / FALLBACK_STREAK_DIVISOR)),
    };
    poseCheckInterval = repeatWithoutOverlap(async () => {
      if (!videoEl || !videoEl.videoWidth) return;
      try {
        const frame = captureFrame();
        const result = await Api.exam.post("/proctoring/pose/check", { image_base64: frame });
        if (!result.available) {
          poseCheckInterval.cancel();
          poseCheckInterval = null;
          reportSignal("pose", "unavailable");
          reportSignal("gaze", "unavailable");
          return;
        }
        if (result.looking_away) {
          streaks.lookingAway += 1;
          if (streaks.lookingAway === fallbackStreak.lookingAway) {
            logViolation("looking_away", `Sustained head turn away from the screen (yaw=${result.yaw}, pitch=${result.pitch}).`);
            toast("Please keep looking at the screen.");
          }
        } else {
          streaks.lookingAway = 0;
        }
        reportSignal("pose", streaks.lookingAway > 0 ? "warn" : "ok");

        if (result.gaze_deviation) {
          streaks.gazeDeviation += 1;
          if (streaks.gazeDeviation === fallbackStreak.gazeDeviation) {
            logViolation("gaze_deviation", `Sustained gaze deviation detected (ratio=${result.gaze_ratio}).`);
            toast("Please keep your eyes on the screen.");
          }
        } else {
          streaks.gazeDeviation = 0;
        }
        reportSignal("gaze", streaks.gazeDeviation > 0 ? "warn" : "ok");
      } catch {
        // ignore
      }
    }, POSE_CHECK_INTERVAL_MS);
  }

  async function watchExternalMonitor() {
    if (typeof window.getScreenDetails !== "function") {
      reportSignal("monitor", "unavailable");
      return;
    }
    try {
      if (navigator.permissions && navigator.permissions.query) {
        const status = await navigator.permissions.query({ name: "window-management" });
        if (status.state === "denied") {
          reportSignal("monitor", "unavailable");
          return;
        }
      }
      const check = async () => {
        try {
          const screenDetails = await window.getScreenDetails();
          if (screenDetails.screens && screenDetails.screens.length > 1) {
            logViolation("external_monitor_detected", `${screenDetails.screens.length} displays detected.`);
            toast("Additional display detected. Please use a single display during the exam.");
            reportSignal("monitor", "warn");
          } else {
            reportSignal("monitor", "ok");
          }
        } catch {
          clearInterval(monitorCheckInterval);
          monitorCheckInterval = null;
          reportSignal("monitor", "unavailable");
        }
      };
      await check();
      monitorCheckInterval = setInterval(check, MONITOR_CHECK_INTERVAL_MS);
    } catch {
      reportSignal("monitor", "unavailable");
    }
  }

  async function startMicMonitoring() {
    if (stopped) return;
    try {
      micStream = await navigator.mediaDevices.getUserMedia({ audio: true });
      if (stopped) {
        micStream.getTracks().forEach((t) => t.stop());
        return;
      }
      audioContext = new (window.AudioContext || window.webkitAudioContext)();
      const source = audioContext.createMediaStreamSource(micStream);
      analyser = audioContext.createAnalyser();
      analyser.fftSize = 2048;
      source.connect(analyser);

      const data = new Uint8Array(analyser.frequencyBinCount);
      const binHz = audioContext.sampleRate / analyser.fftSize;
      const speechStartBin = Math.max(1, Math.floor(SPEECH_BAND_HZ[0] / binHz));
      const speechEndBin = Math.min(data.length - 1, Math.ceil(SPEECH_BAND_HZ[1] / binHz));

      // Two independent streaks, not one: crossing the loud bar always also crosses the
      // moderate bar (it's a strictly higher margin over the same noise floor), so each frame
      // counts toward at most one of the two -- see the isLoud/isModerate branching below --
      // rather than both tiers firing off the same instant of noise.
      let consecutiveModerateFrames = 0;
      let consecutiveLoudFrames = 0;
      let noiseFloor = 10;
      let cooldown = 0;
      // Calibrate against the room's real baseline for the
      // first few seconds before any violation can fire.
      let calibrationFramesLeft = 5;
      reportSignal("audio", "ok");

      noiseCheckInterval = setInterval(() => {
        analyser.getByteFrequencyData(data);
        let speechEnergy = 0;
        let totalEnergy = 0;
        for (let i = 0; i < data.length; i++) {
          totalEnergy += data[i];
          if (i >= speechStartBin && i <= speechEndBin) speechEnergy += data[i];
        }
        const speechAvg = speechEnergy / (speechEndBin - speechStartBin + 1);
        const speechRatio = totalEnergy > 0 ? speechEnergy / totalEnergy : 0;
        // Seed the floor from the room itself before arming.
        if (calibrationFramesLeft > 0) {
          calibrationFramesLeft -= 1;
          noiseFloor = noiseFloor * 0.4 + speechAvg * 0.6;
          return;
        }

        const isLoud = speechAvg > noiseFloor + NOISE_FLOOR_MARGIN_LOUD && speechRatio > SPEECH_RATIO_THRESHOLD;
        const isModerate = !isLoud && speechAvg > noiseFloor + NOISE_FLOOR_MARGIN_MODERATE && speechRatio > SPEECH_RATIO_THRESHOLD;

        if (isLoud) {
          consecutiveLoudFrames += 1;
          consecutiveModerateFrames = 0;
        } else if (isModerate) {
          consecutiveModerateFrames += 1;
          consecutiveLoudFrames = 0;
        } else {
          consecutiveLoudFrames = 0;
          consecutiveModerateFrames = 0;
          noiseFloor = noiseFloor * 0.9 + speechAvg * 0.1;
        }

        // Drift the floor upward during *sustained* noise too, not only while quiet.
        if (isLoud || isModerate) {
          noiseFloor = noiseFloor * 0.98 + speechAvg * 0.02;
        }

        if (cooldown > 0) {
          cooldown -= 1;
        } else if (consecutiveLoudFrames === REQUIRED_CONSECUTIVE_FRAMES_LOUD) {
          logViolation("noise_detected_loud", `Sustained loud/voice-like audio detected (level=${Math.round(speechAvg)}, band-ratio=${speechRatio.toFixed(2)}).`);
          toast("Loud conversation detected. This has been logged as a serious violation.", "severe");
          reportSignal("audio", "warn");
          cooldown = NOISE_COOLDOWN_FRAMES;
        } else if (consecutiveModerateFrames === REQUIRED_CONSECUTIVE_FRAMES_MODERATE) {
          logViolation("noise_detected", `Background noise or brief conversation detected (level=${Math.round(speechAvg)}, band-ratio=${speechRatio.toFixed(2)}).`);
          toast("Background noise detected. Please keep your surroundings quiet.");
          reportSignal("audio", "warn");
          cooldown = NOISE_COOLDOWN_FRAMES;
        } else if (consecutiveLoudFrames === 0 && consecutiveModerateFrames === 0) {
          reportSignal("audio", "ok");
        }
      }, NOISE_CHECK_INTERVAL_MS);
    } catch {
      reportSignal("audio", "unavailable");
    }
  }

  // Fullscreen enforcement, tab-switch detection, and
  // input blocking all moved to lib/lockdown.js.

  // Two distinct tones so a violation is noticeable (and distinguishable by severity) even if
  // the student isn't looking at the screen.
  function playAlertTone(severity = "default") {
    try {
      if (!alertAudioCtx) alertAudioCtx = new (window.AudioContext || window.webkitAudioContext)();
      if (alertAudioCtx.state === "suspended") alertAudioCtx.resume().catch(() => {});
      const ctx = alertAudioCtx;
      const now = ctx.currentTime;
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.type = "sine";

      if (severity === "severe") {
        const notes = [988, 784, 988];
        const pulse = 0.11;
        gain.gain.setValueAtTime(0.0001, now);
        notes.forEach((freq, i) => {
          const t = now + i * pulse;
          osc.frequency.setValueAtTime(freq, t);
          gain.gain.exponentialRampToValueAtTime(0.22, t + 0.02);
          gain.gain.exponentialRampToValueAtTime(0.0001, t + pulse - 0.01);
        });
        osc.connect(gain);
        gain.connect(ctx.destination);
        osc.start(now);
        osc.stop(now + notes.length * pulse + 0.02);
      } else {
        osc.frequency.setValueAtTime(880, now);
        osc.frequency.setValueAtTime(660, now + 0.12);
        gain.gain.setValueAtTime(0.0001, now);
        gain.gain.exponentialRampToValueAtTime(0.18, now + 0.02);
        gain.gain.exponentialRampToValueAtTime(0.0001, now + 0.32);
        osc.connect(gain);
        gain.connect(ctx.destination);
        osc.start(now);
        osc.stop(now + 0.34);
      }
    } catch {
      // Audio is a nice-to-have alert channel, never a requirement -- a
      // blocked/unsupported AudioContext must not affect the visual toast.
    }
  }

  function toast(message, severity = "default") {
    playAlertTone(severity);
    if (onToast) onToast(message);
  }

  function logViolation(eventType, description) {
    if (!attemptId) return;
    // Attach the current webcam frame when one is available.
    let screenshot = null;
    if (videoEl && videoEl.videoWidth) {
      try {
        screenshot = captureFrame();
      } catch {
        screenshot = null;
      }
    }
    // Routed through the shared batching logger (lib/eventLogger.js) so a burst of checks
    // flagging in the same few seconds.
    if (logEvent) {
      logEvent(eventType, description, screenshot);
    } else {
      Api.exam.post("/proctoring/events", {
        attempt_id: attemptId,
        event_type: eventType,
        description,
        screenshot_base64: screenshot,
      }).catch(() => {
        // best-effort; don't let a logging failure cascade into more errors
      });
    }
    violationCount += 1;
    if (onViolationLogged) onViolationLogged(eventType, description, violationCount);
  }

  function setStatus(text, kind) {
    if (onStatusUpdate) onStatusUpdate("__status__", { text, kind });
  }

  async function init({ attemptIdVal, videoElement, statusUpdateCallback, violationCallback, toastCallback, logEventCallback }) {
    attemptId = attemptIdVal;
    videoEl = videoElement;
    onStatusUpdate = statusUpdateCallback || null;
    onViolationLogged = violationCallback || null;
    onToast = toastCallback || null;
    logEvent = logEventCallback || null;

    reportSignal("face", "pending");
    reportSignal("objects", "pending");
    reportSignal("pose", "pending");
    reportSignal("gaze", "pending");
    reportSignal("audio", "pending");
    reportSignal("monitor", "pending");

    // The `stopped` guards after each await are load-bearing. init() is async and stop() is
    // synchronous, so a stop() landing during either await.
    await startCamera();
    if (stopped) return;
    // Local landmarker first (presence/count/pose/gaze at ~12fps), then the slower server
    // identity poll. startServerPoseMonitoring is NOT called here.
    startLocalTracking();
    startIdentityMonitoring();
    startObjectMonitoring();
    await startMicMonitoring();
    if (stopped) return;
    watchExternalMonitor();
  }

  function stop() {
    stopped = true;
    if (faceTracker) faceTracker.stop();
    if (faceCheckInterval) faceCheckInterval.cancel();
    if (noiseCheckInterval) clearInterval(noiseCheckInterval);
    if (objectCheckInterval) objectCheckInterval.cancel();
    if (poseCheckInterval) poseCheckInterval.cancel();
    if (monitorCheckInterval) clearInterval(monitorCheckInterval);
    if (videoEl && videoEl.srcObject) videoEl.srcObject.getTracks().forEach((t) => t.stop());
    if (micStream) micStream.getTracks().forEach((t) => t.stop());
    if (audioContext) audioContext.close().catch(() => {});
    if (alertAudioCtx) alertAudioCtx.close().catch(() => {});
    // Fullscreen/tab/input listeners belong to lockdown.js
    // now, including exiting fullscreen on teardown.
  }

  return {
    init,
    stop,
    getViolationCount: () => violationCount,
    // Exposed so the exam page can re-establish the neutral pose baseline after a legitimate
    // reposition (returning from a fullscreen prompt, a permission dialog) instead of measuring
    // the student forever against a posture they've since left.
    recalibratePose: () => faceTracker && faceTracker.recalibrate(),
  };
}

export function isNetworkError(err) {
  return err instanceof ApiError && err.status === 0;
}
