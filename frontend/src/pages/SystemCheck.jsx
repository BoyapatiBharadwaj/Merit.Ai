import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import Navbar from "../components/Navbar.jsx";
import Footer from "../components/Footer.jsx";
import Icon from "../components/Icon.jsx";
import { btnPrimary, btnGhost, sectionEyebrow } from "../lib/ui.js";

/**
 * Test your device, without an account.
 *
 * A candidate could only discover that their browser, camera or connection was
 * a problem at the moment they tried to start a real exam -- with a clock about
 * to run and an invigilator to convince. The checks already existed inside the
 * exam page's pre-flight; they were simply unreachable until it was too late
 * for them to be useful.
 *
 * Deliberately behind no login. The people most likely to need it are the ones
 * who have not signed up yet, and requiring an account to find out whether the
 * platform works on your laptop is a poor trade.
 *
 * Nothing here is recorded. No camera frame is uploaded and no result is sent
 * anywhere -- this page talks to the browser and nothing else, which is also
 * the honest answer when someone asks what it does with their webcam.
 */

const IDLE = "idle";
const CHECKING = "checking";
const OK = "ok";
const WARN = "warning";
const FAIL = "error";

const TONE = {
  [IDLE]: "bg-page text-muted border border-border",
  [CHECKING]: "bg-primary/10 text-primary",
  [OK]: "bg-success/10 text-success",
  [WARN]: "bg-warning/10 text-warning",
  [FAIL]: "bg-danger/10 text-danger",
};

function CheckRow({ icon, label, detail, status, statusText, action, children }) {
  return (
    <div className="rounded-2xl border border-border bg-surface p-4 sm:p-5">
      <div className="flex items-start gap-4">
        <span className="inline-flex items-center justify-center w-10 h-10 rounded-xl bg-primary/10 text-primary shrink-0">
          <Icon name={icon} width={18} height={18} />
        </span>
        <div className="flex-1 min-w-0">
          <div className="flex flex-wrap items-center gap-2 mb-1">
            <span className="font-semibold text-ink">{label}</span>
            <span className={`inline-flex items-center rounded-full px-2.5 py-1 text-xs font-semibold ${TONE[status]}`}>
              {statusText}
            </span>
          </div>
          <p className="text-sm text-muted leading-relaxed">{detail}</p>
          {children}
        </div>
        {action && <div className="shrink-0">{action}</div>}
      </div>
    </div>
  );
}

export default function SystemCheck() {
  const [secure, setSecure] = useState(IDLE);
  const [browser, setBrowser] = useState({ status: IDLE, text: "", detail: "" });
  const [camera, setCamera] = useState(IDLE);
  const [cameraError, setCameraError] = useState("");
  const [mic, setMic] = useState(IDLE);
  const [micLevel, setMicLevel] = useState(0);
  const [screen, setScreen] = useState(IDLE);
  const [screenError, setScreenError] = useState("");
  const [fullscreen, setFullscreen] = useState(IDLE);
  const [connection, setConnection] = useState({ status: IDLE, text: "Not tested" });

  const videoRef = useRef(null);
  const streamRef = useRef(null);
  const audioCtxRef = useRef(null);
  const meterRef = useRef(null);

  // --- passive checks, run on load ------------------------------------------
  useEffect(() => {
    // getUserMedia and getDisplayMedia are unavailable on plain HTTP outside
    // localhost, so a candidate on an http:// deployment would find every
    // camera check failing with a browser error that explains nothing.
    setSecure(window.isSecureContext ? OK : FAIL);

    const ua = navigator.userAgent;
    const isChromium = /Chrome\/|Edg\//.test(ua) && !/OPR\/|Firefox/.test(ua);
    const isFirefox = /Firefox/.test(ua);
    const isSafari = /Safari/.test(ua) && !/Chrome\/|Chromium/.test(ua);
    setBrowser(
      isChromium
        ? { status: OK, text: "Supported", detail: "Chrome or Edge — the browsers this platform is tested against." }
        : {
            status: WARN,
            text: isFirefox ? "Firefox" : isSafari ? "Safari" : "Unrecognised",
            // Named honestly rather than blocked: these browsers mostly work,
            // and screen sharing is the part that actually differs.
            detail:
              "Screen sharing and fullscreen behave differently here. The exam may still run, but " +
              "Chrome or Edge on a desktop is the combination this platform is tested against.",
          },
    );

    setFullscreen(document.documentElement.requestFullscreen ? OK : FAIL);

    const conn = navigator.connection;
    if (!conn) {
      setConnection({ status: WARN, text: "Can't measure", detail: "" });
    } else {
      const slow = ["slow-2g", "2g"].includes(conn.effectiveType);
      setConnection({
        status: slow ? WARN : OK,
        text: slow ? `Weak (${conn.effectiveType})` : `Looks fine (${conn.effectiveType || "unknown"})`,
      });
    }

    return () => stopMedia();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const stopMedia = useCallback(() => {
    if (meterRef.current) clearInterval(meterRef.current);
    meterRef.current = null;
    if (audioCtxRef.current) {
      audioCtxRef.current.close().catch(() => {});
      audioCtxRef.current = null;
    }
    if (streamRef.current) {
      streamRef.current.getTracks().forEach((track) => track.stop());
      streamRef.current = null;
    }
  }, []);

  async function testCameraAndMic() {
    setCamera(CHECKING);
    setMic(CHECKING);
    setCameraError("");
    stopMedia();
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ video: true, audio: true });
      streamRef.current = stream;
      if (videoRef.current) {
        videoRef.current.srcObject = stream;
        await videoRef.current.play().catch(() => {});
      }
      setCamera(stream.getVideoTracks().length ? OK : FAIL);

      if (stream.getAudioTracks().length) {
        const AudioCtx = window.AudioContext || window.webkitAudioContext;
        const ctx = new AudioCtx();
        audioCtxRef.current = ctx;
        const analyser = ctx.createAnalyser();
        analyser.fftSize = 512;
        ctx.createMediaStreamSource(stream).connect(analyser);
        const data = new Uint8Array(analyser.frequencyBinCount);
        // A live meter rather than a pass/fail: "your microphone works" is a
        // claim someone has to trust, and a bar that moves when they speak is
        // one they can check for themselves.
        meterRef.current = setInterval(() => {
          analyser.getByteFrequencyData(data);
          const average = data.reduce((sum, v) => sum + v, 0) / data.length;
          setMicLevel(Math.min(100, Math.round((average / 128) * 100)));
        }, 100);
        setMic(OK);
      } else {
        setMic(FAIL);
      }
    } catch (err) {
      setCamera(FAIL);
      setMic(FAIL);
      setCameraError(
        err?.name === "NotAllowedError"
          ? "Permission was denied. Allow camera and microphone access for this site and try again."
          : err?.name === "NotFoundError"
            ? "No camera or microphone was found on this device."
            : err?.message || "Couldn't access the camera and microphone.",
      );
    }
  }

  async function testScreenShare() {
    setScreen(CHECKING);
    setScreenError("");
    try {
      const stream = await navigator.mediaDevices.getDisplayMedia({ video: true });
      // Stopped immediately. The point is to confirm the browser can do it and
      // the candidate knows what the prompt looks like -- there is no reason to
      // keep watching their screen on a page that does nothing with it.
      stream.getTracks().forEach((track) => track.stop());
      setScreen(OK);
    } catch (err) {
      setScreen(FAIL);
      setScreenError(
        err?.name === "NotAllowedError"
          ? "You dismissed the prompt, or sharing is blocked. In a real exam you must share your ENTIRE screen."
          : err?.message || "Screen sharing isn't available in this browser.",
      );
    }
  }

  const blocking = secure === FAIL || camera === FAIL || fullscreen === FAIL;
  const untested = camera === IDLE || screen === IDLE;

  return (
    <div className="min-h-screen flex flex-col bg-page text-ink">
      <Navbar />
      <main className="flex-1 max-w-3xl mx-auto w-full px-5 sm:px-6 py-12 sm:py-16">
        <span className={sectionEyebrow}>
          <Icon name="monitor" width={13} height={13} />
          Before exam day
        </span>
        <h1 className="text-3xl font-extrabold tracking-tight mb-3">Test your device</h1>
        <p className="text-muted leading-relaxed mb-8">
          Check that this computer can run a proctored exam — before the day, not five minutes before it
          starts. Nothing here is uploaded or recorded: these checks run entirely in your browser.
        </p>

        <div className="flex flex-col gap-3 mb-8">
          <CheckRow
            icon="lock"
            label="Secure connection"
            detail={
              secure === OK
                ? "This page is served over HTTPS, which browsers require before granting camera access."
                : "Browsers refuse camera, microphone and screen sharing over plain HTTP. Your institution needs to serve Merit.Ai over HTTPS."
            }
            status={secure}
            statusText={secure === OK ? "HTTPS" : "Not secure"}
          />

          <CheckRow
            icon="monitor"
            label="Browser"
            detail={browser.detail}
            status={browser.status}
            statusText={browser.text || "Checking…"}
          />

          <CheckRow
            icon="camera"
            label="Camera & microphone"
            detail="Your exam uses these to confirm it's you. Speak to check the level bar moves."
            status={camera}
            statusText={
              { idle: "Not tested", checking: "Requesting…", ok: "Working", error: "Failed" }[camera]
            }
            action={
              <button type="button" onClick={testCameraAndMic} className={`${btnGhost} px-4 py-2 text-sm`}>
                {camera === OK ? "Test again" : "Test"}
              </button>
            }
          >
            <div className={camera === OK ? "mt-3 flex items-center gap-3" : "hidden"}>
              <video ref={videoRef} muted playsInline
                     className="w-32 h-24 rounded-lg bg-black object-cover" />
              <div className="flex-1">
                <div className="text-[11px] text-muted mb-1">Microphone level</div>
                <div className="h-2 rounded-full bg-border overflow-hidden">
                  <div className="h-full bg-success transition-[width] duration-100"
                       style={{ width: `${micLevel}%` }} />
                </div>
                {mic === OK && micLevel < 3 && (
                  <p className="text-[11px] text-muted mt-1">Say something — the bar should move.</p>
                )}
              </div>
            </div>
            {cameraError && <p className="mt-2 text-sm text-danger leading-relaxed">{cameraError}</p>}
          </CheckRow>

          <CheckRow
            icon="monitor"
            label="Screen sharing"
            detail="A proctored exam asks you to share your ENTIRE screen — not one window or tab. This is just a rehearsal; sharing stops immediately."
            status={screen}
            statusText={
              { idle: "Not tested", checking: "Requesting…", ok: "Works", error: "Failed" }[screen]
            }
            action={
              <button type="button" onClick={testScreenShare} className={`${btnGhost} px-4 py-2 text-sm`}>
                {screen === OK ? "Test again" : "Test"}
              </button>
            }
          >
            {screenError && <p className="mt-2 text-sm text-danger leading-relaxed">{screenError}</p>}
          </CheckRow>

          <CheckRow
            icon="maximize"
            label="Fullscreen"
            detail="Exams run in fullscreen, and leaving it is recorded as a warning."
            status={fullscreen}
            statusText={fullscreen === OK ? "Supported" : "Not supported"}
          />

          <CheckRow
            icon="wifi"
            label="Connection"
            detail="Your answers are saved as you work and again when you submit, so a brief drop is survivable — but a consistently weak connection is worth sorting out first."
            status={connection.status}
            statusText={connection.text}
          />
        </div>

        <div
          className={`rounded-2xl border p-5 ${
            blocking ? "border-danger/30 bg-danger/5" : untested ? "border-border bg-surface" : "border-success/30 bg-success/5"
          }`}
          role="status"
        >
          <p className="font-semibold text-ink mb-1">
            {blocking
              ? "This device needs attention before exam day"
              : untested
                ? "Run the two tests above"
                : "This device looks ready"}
          </p>
          <p className="text-sm text-muted leading-relaxed">
            {blocking
              ? "One of the required checks failed. Fix it, or use a different computer — finding out on exam day is much worse."
              : untested
                ? "The camera and screen-sharing checks need your permission, so they only run when you ask them to."
                : "Everything a proctored exam needs is available here. Re-run this on the same computer and network you'll actually use."}
          </p>
          <div className="flex flex-wrap gap-3 mt-4">
            <Link to="/register" className={`${btnPrimary} px-4 py-2 text-sm`}>
              Create candidate account
            </Link>
            <Link to="/privacy" className={`${btnGhost} px-4 py-2 text-sm`}>
              What gets collected
            </Link>
          </div>
        </div>
      </main>
      <Footer />
    </div>
  );
}
