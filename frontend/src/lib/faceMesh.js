/**
 * In-browser face tracking: presence, face count, head pose and gaze, all computed locally from
 * the webcam at ~12fps via MediaPipe Tasks Vision (WASM/WebGL).
 */

// Self-hosted, not fetched from jsdelivr.
const WASM_URL = "/mediapipe/wasm";

// The face-landmarker weights (~3.7MB) are NOT distributed on npm.
const LOCAL_MODEL_URL = "/mediapipe/face_landmarker.task";
const REMOTE_MODEL_URL =
  "https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task";

/** Prefer the self-hosted weights; fall back to Google's only if absent. */
async function resolveModelUrl() {
  try {
    const res = await fetch(LOCAL_MODEL_URL, { method: "HEAD" });
    if (res.ok) return LOCAL_MODEL_URL;
  } catch {
    // Network/HEAD unsupported -- fall through to the remote copy.
  }
  console.warn(
    "[faceMesh] Using Google's hosted face_landmarker.task. Run `npm run fetch:model` " +
    "to self-host it before a production deployment."
  );
  return REMOTE_MODEL_URL;
}

// ~12fps. Fast enough that a glance away is caught within ~80ms, slow enough to leave the
// student's CPU to the exam itself.
export const DETECT_INTERVAL_MS = 80;

// Deviation from the student's own calibrated neutral pose, in degrees.
export const YAW_DEVIATION_DEG = 28;
export const PITCH_DEVIATION_DEG = 24;

// Iris offset within the eye socket, relative to calibrated neutral.
// 0 = dead centre, 0.5 = iris at the eye corner.
export const GAZE_DEVIATION = 0.19;

const CALIBRATION_FRAMES = 12; // ~1s at DETECT_INTERVAL_MS

// Refined-iris landmark indices, available because refineLandmarks is on.
// Same indices the Python implementation used.
const LEFT_IRIS = 468;
const RIGHT_IRIS = 473;
const LEFT_EYE_CORNERS = [33, 133];
const RIGHT_EYE_CORNERS = [362, 263];

let visionModulePromise = null;

/**
 * Loads the Tasks Vision ESM bundle once per page.
 */
function loadVisionModule() {
  if (!visionModulePromise) {
    visionModulePromise = import("@mediapipe/tasks-vision").catch((err) => {
      // Reset so a later attempt can retry rather than being stuck with a
      // permanently rejected promise.
      visionModulePromise = null;
      throw err;
    });
  }
  return visionModulePromise;
}

/**
 * Euler angles in degrees from MediaPipe's 4x4 facial transformation matrix.
 */
function eulerFromMatrix(data) {
  const r00 = data[0], r10 = data[1], r20 = data[2];
  const r21 = data[6], r22 = data[10];

  const pitch = Math.atan2(r21, r22) * (180 / Math.PI);
  const yaw = Math.atan2(-r20, Math.sqrt(r21 * r21 + r22 * r22)) * (180 / Math.PI);
  const roll = Math.atan2(r10, r00) * (180 / Math.PI);
  return { pitch, yaw, roll };
}

/** Mean horizontal iris offset from eye centre, as a signed fraction of eye
 * width. Mirrors backend/app/ai/pose_service.py's _gaze_ratio, re-centred on
 * 0 instead of 0.5 so "deviation" is just the absolute value. */
function gazeOffset(landmarks) {
  function offsetFor(irisIndex, [innerIndex, outerIndex]) {
    const inner = landmarks[innerIndex];
    const outer = landmarks[outerIndex];
    const iris = landmarks[irisIndex];
    if (!inner || !outer || !iris) return null;
    const span = outer.x - inner.x;
    if (Math.abs(span) < 1e-6) return null;
    return (iris.x - inner.x) / span - 0.5;
  }
  const values = [
    offsetFor(LEFT_IRIS, LEFT_EYE_CORNERS),
    offsetFor(RIGHT_IRIS, RIGHT_EYE_CORNERS),
  ].filter((v) => v !== null);
  if (!values.length) return null;
  return values.reduce((a, b) => a + b, 0) / values.length;
}

/**
 * Creates a tracker bound to one <video> element.
 */
export function createFaceTracker() {
  let landmarker = null;
  let rafId = null;
  let stopped = false;
  let lastRun = 0;

  let calibrationSamples = [];
  let baseline = null; // { yaw, pitch, gaze }

  async function start(videoEl, onResult, onUnavailable) {
    try {
      const vision = await loadVisionModule();
      if (stopped) return;
      const fileset = await vision.FilesetResolver.forVisionTasks(WASM_URL);
      if (stopped) return;
      const modelUrl = await resolveModelUrl();
      if (stopped) return;
      landmarker = await vision.FaceLandmarker.createFromOptions(fileset, {
        baseOptions: { modelAssetPath: modelUrl, delegate: "GPU" },
        runningMode: "VIDEO",
        // 2, not 1: detecting a second person in frame is a proctoring signal in its own right,
        // and a landmarker capped at one face can never report it.
        numFaces: 2,
        outputFacialTransformationMatrixes: true,
        refineLandmarks: true,
      });
      if (stopped) {
        landmarker.close();
        return;
      }
      loop(videoEl, onResult);
    } catch (err) {
      // A CDN block, a WebGL-less environment, an unsupported browser: the caller falls back to
      // server-side checks rather than losing the signal entirely.
      if (onUnavailable) onUnavailable(err);
    }
  }

  function loop(videoEl, onResult) {
    const tick = () => {
      if (stopped) return;
      rafId = requestAnimationFrame(tick);

      const now = performance.now();
      if (now - lastRun < DETECT_INTERVAL_MS) return;
      lastRun = now;

      if (!videoEl || !videoEl.videoWidth || videoEl.paused) return;

      let result;
      try {
        result = landmarker.detectForVideo(videoEl, now);
      } catch {
        return; // a dropped frame is not worth tearing the loop down for
      }

      const faces = result.faceLandmarks || [];
      const faceCount = faces.length;

      if (faceCount !== 1) {
        // Nothing meaningful to say about pose or gaze without exactly one face -- and
        // importantly, do NOT reset the calibration baseline here.
        onResult({
          faceCount, lookingAway: null, gazeDeviation: null,
          yaw: null, pitch: null, gazeOffset: null, calibrating: !baseline,
        });
        return;
      }

      const landmarks = faces[0];
      const matrix = result.facialTransformationMatrixes?.[0]?.data;
      const angles = matrix ? eulerFromMatrix(matrix) : null;
      const gaze = gazeOffset(landmarks);

      if (!baseline) {
        if (angles && gaze !== null) {
          calibrationSamples.push({ yaw: angles.yaw, pitch: angles.pitch, gaze });
          if (calibrationSamples.length >= CALIBRATION_FRAMES) {
            // Median, not mean: the student may glance away once during the first second, and
            // one outlier must not define "neutral" for the rest of the exam.
            const median = (key) => {
              const sorted = calibrationSamples.map((s) => s[key]).sort((a, b) => a - b);
              return sorted[Math.floor(sorted.length / 2)];
            };
            baseline = { yaw: median("yaw"), pitch: median("pitch"), gaze: median("gaze") };
            calibrationSamples = [];
          }
        }
        onResult({
          faceCount: 1, lookingAway: null, gazeDeviation: null,
          yaw: null, pitch: null, gazeOffset: null, calibrating: true,
        });
        return;
      }

      const yawDev = angles ? angles.yaw - baseline.yaw : null;
      const pitchDev = angles ? angles.pitch - baseline.pitch : null;
      const gazeDev = gaze === null ? null : gaze - baseline.gaze;

      onResult({
        faceCount: 1,
        lookingAway: yawDev === null ? null
          : Math.abs(yawDev) > YAW_DEVIATION_DEG || Math.abs(pitchDev) > PITCH_DEVIATION_DEG,
        gazeDeviation: gazeDev === null ? null : Math.abs(gazeDev) > GAZE_DEVIATION,
        yaw: yawDev === null ? null : Math.round(yawDev * 10) / 10,
        pitch: pitchDev === null ? null : Math.round(pitchDev * 10) / 10,
        gazeOffset: gazeDev === null ? null : Math.round(gazeDev * 100) / 100,
        calibrating: false,
      });
    };
    rafId = requestAnimationFrame(tick);
  }

  /**
   * Drops the calibrated neutral pose so the next second of tracking re-establishes it.
   */
  function recalibrate() {
    baseline = null;
    calibrationSamples = [];
  }

  function stop() {
    stopped = true;
    if (rafId) cancelAnimationFrame(rafId);
    if (landmarker) {
      try {
        landmarker.close();
      } catch {
        // already torn down
      }
      landmarker = null;
    }
  }

  return { start, stop, recalibrate, isCalibrated: () => !!baseline };
}
