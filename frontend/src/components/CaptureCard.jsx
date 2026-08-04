import { useEffect, useRef, useState } from "react";
import Icon from "./Icon.jsx";
import { btnPrimary, btnGhost } from "../lib/ui.js";
import { ApiError } from "../lib/api.js";

/** Reusable "open camera -> capture a frame -> submit it" card, used for
 * both face registration and ID-card OCR verification on the Profile page.
 * Each mounts its own getUserMedia stream and tears it down on unmount. */
export default function CaptureCard({ title, description, mirrored = false, captureLabel, submitLabel, onSubmit, formatResult }) {
  const videoRef = useRef(null);
  const canvasRef = useRef(null);
  const streamRef = useRef(null);

  const [cameraError, setCameraError] = useState("");
  const [captured, setCaptured] = useState(null);
  const [submitting, setSubmitting] = useState(false);
  const [result, setResult] = useState(null);

  useEffect(() => {
    let active = true;
    if (!navigator.mediaDevices?.getUserMedia) {
      setCameraError("This browser doesn't support camera access.");
      return;
    }
    navigator.mediaDevices
      .getUserMedia({ video: true })
      .then((stream) => {
        if (!active) {
          stream.getTracks().forEach((t) => t.stop());
          return;
        }
        streamRef.current = stream;
        if (videoRef.current) videoRef.current.srcObject = stream;
      })
      .catch(() => setCameraError("Camera access is required for this step."));
    return () => {
      active = false;
      streamRef.current?.getTracks().forEach((t) => t.stop());
    };
  }, []);

  function capture() {
    const video = videoRef.current;
    const canvas = canvasRef.current;
    if (!video || !canvas) return;
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    const ctx = canvas.getContext("2d");
    if (mirrored) {
      // The live preview is CSS-mirrored for a natural "looking in a mirror"
      // feel; undo that for the actual captured frame so the stored photo
      // matches what a third party would see, not a flipped version.
      ctx.translate(canvas.width, 0);
      ctx.scale(-1, 1);
    }
    ctx.drawImage(video, 0, 0);
    setCaptured(canvas.toDataURL("image/jpeg", 0.9));
    setResult(null);
  }

  function retake() {
    setCaptured(null);
    setResult(null);
  }

  async function submit() {
    setSubmitting(true);
    setResult(null);
    try {
      const res = await onSubmit(captured);
      setResult(formatResult ? formatResult(res) : { tone: "success", message: "Done." });
    } catch (err) {
      setResult({ tone: "error", message: err instanceof ApiError ? err.message : "Something went wrong. Please try again." });
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="rounded-2xl border border-border bg-surface shadow-card p-6 transition-all duration-200 hover:border-primary/30 hover:shadow-[0_16px_32px_-16px_rgba(15,23,42,0.2)]">
      <h2 className="text-base font-bold text-ink mb-1">{title}</h2>
      <p className="text-sm text-muted leading-relaxed mb-5">{description}</p>

      {cameraError && (
        <div className="mb-4 flex items-start gap-2.5 rounded-xl border border-danger/30 bg-danger/5 px-4 py-3 text-sm text-danger">
          <Icon name="alert" width={16} height={16} className="mt-0.5 shrink-0" />
          <span>{cameraError}</span>
        </div>
      )}

      <div className="flex flex-col items-center gap-4">
        <div className="w-full max-w-sm rounded-xl overflow-hidden bg-black aspect-[4/3] relative">
          <video
            ref={videoRef}
            autoPlay
            playsInline
            muted
            className={`w-full h-full object-cover ${captured ? "hidden" : ""} ${mirrored ? "-scale-x-100" : ""}`}
          />
          {captured && <img src={captured} alt="Captured frame" className="w-full h-full object-cover" />}
        </div>
        <canvas ref={canvasRef} className="hidden" />

        {result && (
          <div
            className={`w-full max-w-sm flex items-start gap-2.5 rounded-xl border px-4 py-3 text-sm ${
              result.tone === "success" ? "border-success/30 bg-success/5 text-success" : "border-danger/30 bg-danger/5 text-danger"
            }`}
          >
            <Icon name={result.tone === "success" ? "check" : "alert"} width={16} height={16} className="mt-0.5 shrink-0" />
            <span>{result.message}</span>
          </div>
        )}

        <div className="flex gap-3">
          {!captured ? (
            <button type="button" onClick={capture} disabled={!!cameraError} className={btnGhost}>
              {captureLabel}
            </button>
          ) : (
            <>
              <button type="button" onClick={submit} disabled={submitting} className={`${btnPrimary} ${submitting ? "opacity-70 pointer-events-none" : ""}`}>
                {submitting && <Icon name="spinner" width={16} height={16} className="animate-spin" />}
                {submitting ? "Submitting…" : submitLabel}
              </button>
              <button type="button" onClick={retake} disabled={submitting} className={btnGhost}>
                Retake
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
