/**
 * Client-side AI proctoring controller — a React-friendly port of the
 * legacy frontend/js/proctoring.js. Exported as a factory (createProctoring())
 * rather than a singleton module, so each mounted Exam page gets its own
 * instance instead of sharing global mutable state across mounts (relevant
 * under React StrictMode's double-invoke-in-dev behavior).
 *
 * Handles: webcam capture + periodic face verification, object/pose/gaze
 * checks, external-monitor detection, fullscreen exit detection, tab-switch
 * detection, copy/paste & right-click blocking, and microphone noise
 * detection (Web Audio API). Violations are logged to the backend and
 * surfaced as short-lived toasts.
 */
import { Api, ApiError } from "./api.js";
import { createFaceTracker } from "./faceMesh.js";

// Presence, face count, head pose and gaze are now tracked locally at ~12fps
// by lib/faceMesh.js instead of being polled off the server -- see that
// file's header for why. What remains on the server is identity matching
// ("is this the enrolled student"), which needs the enrolled embedding and
// is the one signal where a slow cadence is genuinely fine.
//
// 3000 -> 12000ms. This is not a downgrade: presence used to be answered by
// this same poll and is now continuous, so the only thing this interval
// governs is how often we re-confirm identity. Twelve seconds of a
// substituted student is caught by the same check that three seconds was,
// and the request costs ~4x less server time per attempt.
const FACE_IDENTITY_INTERVAL_MS = 12000;
const OBJECT_CHECK_INTERVAL_MS = 10000;
const MONITOR_CHECK_INTERVAL_MS = 30000;
const NOISE_CHECK_INTERVAL_MS = 1000;
const SPEECH_BAND_HZ = [85, 3400];
const SPEECH_RATIO_THRESHOLD = 0.45;
// Two audio tiers instead of one: a lower bar for "there's background noise/
// chatter" (still worth a warning, but common enough to happen innocently)
// and a higher bar for "sustained loud talking," which is logged as its own,
// more severe event (see EventType.NOISE_DETECTED_LOUD on the backend).
//
// Margins and streaks both raised substantially (14/26 over 2/3 frames ->
// 24/38 over 6/5). The old values warned after ~2 seconds of anything in the
// speech band at a level a fan, a keyboard, or a person clearing their throat
// clears easily, so the mic warning fired more or less continuously in a
// normal room -- which is the same failure mode as the anti-spoofing false
// positives: an alarm that is always on carries no information. At a 1s poll
// this now needs ~6 seconds of sustained voice-band audio for a moderate
// flag, which is a conversation rather than a cough.
const NOISE_FLOOR_MARGIN_MODERATE = 24;
const NOISE_FLOOR_MARGIN_LOUD = 38;
const REQUIRED_CONSECUTIVE_FRAMES_MODERATE = 6;
const REQUIRED_CONSECUTIVE_FRAMES_LOUD = 5;
// Once a tier has fired, stay quiet for this many checks before it can fire
// again. Without it, a genuinely noisy room re-triggers the moment the streak
// counter rolls past the threshold again -- roughly every 6 seconds, forever.
const NOISE_COOLDOWN_FRAMES = 30;
// Streaks are counted in *detector frames*, and the local tracker runs at
// ~12fps rather than the old one-poll-every-5-seconds, so these had to be
// rescaled or a 4-frame streak would mean a third of a second. Expressed as
// durations: looking away and gaze both need ~2s sustained, which is a
// deliberate look rather than a glance, and matches what the old 4-poll
// streak was reaching for (20s was far too slow to be useful).
const LOCAL_FPS = 12;
const REQUIRED_STREAK = {
  lookingAway: 2 * LOCAL_FPS,
  gazeDeviation: 2 * LOCAL_FPS,
  // Object-detector person count, still polled from the server every 10s
  // (YOLO is too heavy to run client-side alongside the landmarker). One
  // detection is enough: a second person in frame is serious, and the
  // landmarker's own multipleFaces check corroborates it within a second.
  multiplePersons: 1,
  // ~2.5s of continuous absence. A blink, a head-scratch, or a single
  // dropped frame must not read as "the student left" -- at 12fps a
  // 2-frame streak (the old value, tuned for a 3s poll) would be a sixth of
  // a second and would fire constantly.
  noFace: 30,
  // Two faces for ~1s. The local tracker sees every frame now, so a single
  // frame of a misdetected background face -- a poster, a photo on a shelf,
  // a reflection -- would otherwise log a violation instantly. One second
  // of a genuinely present second person is still near-immediate.
  multipleFaces: 12,
  // How many consecutive "detector unavailable" replies before object
  // monitoring gives up for the rest of the exam. A genuinely missing model
  // reports unavailable every single time, so 3 costs ~15 seconds of pointless
  // polling in that case -- cheap insurance against a transient failure
  // silently disabling phone/book detection for the whole attempt.
  objectsUnavailable: 3,
};

export function createProctoring() {
  let attemptId = null;
  let videoEl = null;
  let statusEl = null;
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
  // True once the local landmarker has delivered a frame. Governs which of
  // the two writers owns the "face" status signal, so local presence and the
  // slower server identity check don't overwrite each other.
  let localTrackingActive = false;
  // Latched by the server identity check so local tracking doesn't reset a
  // real mismatch back to "verified" 12 times a second while the wrong
  // person is still sitting in frame.
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

  function captureFrame() {
    const canvas = document.createElement("canvas");
    canvas.width = videoEl.videoWidth || 320;
    canvas.height = videoEl.videoHeight || 240;
    const ctx = canvas.getContext("2d");
    ctx.drawImage(videoEl, 0, 0, canvas.width, canvas.height);
    return canvas.toDataURL("image/jpeg", 0.7);
  }

  /**
   * Presence, face count, head pose and gaze -- all local, ~12fps, no
   * network. See lib/faceMesh.js for the calibration design.
   *
   * Falls back to the previous server-polled pose endpoint if the landmarker
   * can't load at all (CDN blocked, no WebGL, unsupported browser), so an
   * unusual environment degrades to the old behaviour rather than losing
   * these signals outright.
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
          // Presence is confirmed locally; whether it's the *right* person is
          // the server identity check's job, and it owns the "face" signal
          // once it has an answer. Don't overwrite a mismatch warning here.
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
   * Identity only: "is the face in frame the enrolled student". Presence and
   * face count are handled locally now, so this ignores those fields and
   * reacts solely to `match`.
   */
  function startIdentityMonitoring() {
    if (stopped) return;
    faceCheckInterval = setInterval(async () => {
      if (!videoEl || !videoEl.videoWidth) return;
      try {
        const frame = captureFrame();
        const result = await Api.post("/proctoring/face/verify", { image_base64: frame });

        // spoof_suspected is only ever set by a *trained* anti-spoofing
        // model now; the classical heuristic no longer accuses on its own
        // (see backend/app/ai/face_service.py). So this branch means a real
        // model made a real call, and is worth surfacing.
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
        // result.match == null -> inconclusive (no usable stored profile, or
        // no encoding for this frame). Deliberately no state change: `null`
        // is not a match and not a mismatch, and asserting either would be
        // claiming evidence we don't have.
      } catch {
        // Fail quietly (profile not registered yet, transient network hiccup).
      }
    }, FACE_IDENTITY_INTERVAL_MS);
  }

  function startObjectMonitoring() {
    if (stopped) return;
    let unavailableStreak = 0;
    objectCheckInterval = setInterval(async () => {
      if (!videoEl || !videoEl.videoWidth) return;
      try {
        const frame = captureFrame();
        const result = await Api.post("/proctoring/objects/detect", { image_base64: frame });
        if (!result.available) {
          // Two very different things arrive as available=false: "no detector
          // is installed" (permanent -- stop polling, which is the point of
          // the flag) and "that one inference failed" (transient). This used
          // to give up permanently on the first of either, which was harmless
          // while object detection only ever ran in the optional worker and
          // was usually off anyway. Now that it runs in-process and is on by
          // default, one blip would silently disable phone/book detection for
          // the rest of the exam, so give it a few tries before concluding
          // the detector genuinely is not there.
          unavailableStreak += 1;
          if (unavailableStreak >= REQUIRED_STREAK.objectsUnavailable) {
            clearInterval(objectCheckInterval);
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
   * FALLBACK ONLY. The server-polled pose/gaze path, kept for environments
   * where the local landmarker can't run (CDN blocked by a school firewall,
   * no WebGL, an old browser). startLocalTracking() calls this from its
   * onUnavailable handler; nothing else should.
   *
   * Note the streak constants are shared with the local path but were
   * rescaled for 12fps, so on this 5s-poll path they'd mean 2 minutes. The
   * local-vs-poll divisor below converts them back to poll counts.
   */
  const POSE_CHECK_INTERVAL_MS = 5000;
  const FALLBACK_STREAK_DIVISOR = LOCAL_FPS * (POSE_CHECK_INTERVAL_MS / 1000);

  function startServerPoseMonitoring() {
    if (stopped) return;
    const fallbackStreak = {
      lookingAway: Math.max(2, Math.round(REQUIRED_STREAK.lookingAway / FALLBACK_STREAK_DIVISOR)),
      gazeDeviation: Math.max(2, Math.round(REQUIRED_STREAK.gazeDeviation / FALLBACK_STREAK_DIVISOR)),
    };
    poseCheckInterval = setInterval(async () => {
      if (!videoEl || !videoEl.videoWidth) return;
      try {
        const frame = captureFrame();
        const result = await Api.post("/proctoring/pose/check", { image_base64: frame });
        if (!result.available) {
          clearInterval(poseCheckInterval);
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

      // Two independent streaks, not one: crossing the loud bar always also
      // crosses the moderate bar (it's a strictly higher margin over the same
      // noise floor), so each frame counts toward at most one of the two --
      // see the isLoud/isModerate branching below -- rather than both tiers
      // firing off the same instant of noise.
      let consecutiveModerateFrames = 0;
      let consecutiveLoudFrames = 0;
      let noiseFloor = 10;
      let cooldown = 0;
      // Calibrate against the room's real baseline for the first few seconds
      // before any violation can fire. Starting the floor at a hardcoded 10
      // and immediately arming meant a room whose idle level is above 10 (an
      // air conditioner, a desk fan, a laptop under load) began the exam
      // already over the moderate margin and flagged the student for the
      // room's own noise floor before they had said anything.
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

        // Drift the floor upward during *sustained* noise too, not only while
        // quiet. The old code only ever adapted on quiet frames, so a room
        // with steady background sound never recalibrated: every check stayed
        // over the margin and the warning repeated for the whole exam. A much
        // slower coefficient than the quiet path (0.02 vs 0.1) so a real
        // conversation still crosses the bar well before the floor catches up.
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

  // Fullscreen enforcement, tab-switch detection, and input blocking all
  // moved to lib/lockdown.js. They used to live here, but they are a
  // different concern with different failure semantics: proctoring degrades
  // gracefully when the AI worker is unreachable, whereas lockdown must never
  // degrade. Keeping two sets of listeners on the same DOM events also meant
  // a single Alt-Tab got logged twice. lockdown.js is now the sole owner.

  // Two distinct tones so a violation is noticeable (and distinguishable by
  // severity) even if the student isn't looking at the screen -- Web Audio
  // API only, no audio asset to ship. The AudioContext is created lazily on
  // first use (creating one before any user gesture can throw/stay suspended
  // in some browsers) and reused across calls rather than rebuilt every time.
  //
  // "default": the original short two-tone down-glide, for ordinary warnings.
  // "severe": three sharper alternating pulses, reserved for violations
  // serious enough to be logged at high severity (currently just sustained
  // loud/voice audio) -- meant to read as more urgent without needing a
  // shipped audio asset.
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
    // Attach the current webcam frame when one is available -- every
    // violation type raised in this file (face, spoof, multi-person, phone/
    // book, pose/gaze, external monitor) is something the camera just
    // observed, so the frame is the actual evidence an examiner needs to
    // review the flag, not just its text description. Best-effort: a capture
    // failure (e.g. the stream just dropped) must never block logging the
    // violation itself.
    let screenshot = null;
    if (videoEl && videoEl.videoWidth) {
      try {
        screenshot = captureFrame();
      } catch {
        screenshot = null;
      }
    }
    // Routed through the shared batching logger (lib/eventLogger.js) so a
    // burst of checks flagging in the same few seconds -- a spoof result
    // plus a phone detection plus loud audio, say -- becomes one request
    // instead of three. Falls back to a direct, best-effort POST if no
    // logger was wired up (see Exam.jsx's logEventCallback). The violation
    // counter increments on enqueue rather than on confirmed delivery -- it
    // is a live UI counter, not an audit record, so "flagged" is the right
    // moment to bump it rather than "the batch flush later succeeded."
    if (logEvent) {
      logEvent(eventType, description, screenshot);
    } else {
      Api.post("/proctoring/events", {
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

    // The `stopped` guards after each await are load-bearing. init() is async
    // and stop() is synchronous, so a stop() landing during either await --
    // React 18 StrictMode's double-mount, the effect re-running on an
    // attemptId change, or a submit within the first second -- found every
    // interval handle still null, cleared nothing, and then init() resumed
    // and installed four intervals with no owner. Those kept polling
    // /proctoring/face/verify every 3s and logging violations against a
    // finished attempt for the life of the page.
    await startCamera();
    if (stopped) return;
    // Local landmarker first (presence/count/pose/gaze at ~12fps), then the
    // slower server identity poll. startServerPoseMonitoring is NOT called
    // here -- it is the landmarker's fallback and starts itself only if the
    // landmarker fails to load.
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
    if (faceCheckInterval) clearInterval(faceCheckInterval);
    if (noiseCheckInterval) clearInterval(noiseCheckInterval);
    if (objectCheckInterval) clearInterval(objectCheckInterval);
    if (poseCheckInterval) clearInterval(poseCheckInterval);
    if (monitorCheckInterval) clearInterval(monitorCheckInterval);
    if (videoEl && videoEl.srcObject) videoEl.srcObject.getTracks().forEach((t) => t.stop());
    if (micStream) micStream.getTracks().forEach((t) => t.stop());
    if (audioContext) audioContext.close().catch(() => {});
    if (alertAudioCtx) alertAudioCtx.close().catch(() => {});
    // Fullscreen/tab/input listeners belong to lockdown.js now, including
    // exiting fullscreen on teardown -- doing it here too would race with
    // lockdown's own cleanup and could fire a spurious breach.
  }

  return {
    init,
    stop,
    getViolationCount: () => violationCount,
    // Exposed so the exam page can re-establish the neutral pose baseline
    // after a legitimate reposition (returning from a fullscreen prompt, a
    // permission dialog) instead of measuring the student forever against a
    // posture they've since left. See faceMesh.js's recalibrate().
    recalibratePose: () => faceTracker && faceTracker.recalibrate(),
  };
}

export function isNetworkError(err) {
  return err instanceof ApiError && err.status === 0;
}
