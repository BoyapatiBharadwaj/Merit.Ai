import { useEffect, useState } from "react";
import Icon from "./Icon.jsx";
import { Api, ApiError } from "../lib/api.js";

/**
 * Registered face photo + ID card photo for one student, fetched on demand
 * as authenticated blobs -- a plain <img src="/api/..."> can't carry a
 * Bearer token, and these endpoints are deliberately not public (see
 * backend/app/api/v1/proctoring.py's _can_view_student_identity). Shared by
 * the examiner's Attempts table and the admin's Student Users table, the two
 * places staff already look at one specific student and might reasonably
 * need to confirm who they actually are.
 *
 * A 403 here (student outside the viewer's organization and never invited
 * to any of their exams) is treated the same as "not available" rather than
 * shown as an alarming error -- the button that opens this modal shouldn't
 * exist for someone the viewer has no relationship to in the first place.
 */
/** Exported so other staff-only image viewers (the Candidate Exam Report's
 * violation-evidence viewer) can reuse the exact same fetch-and-revoke
 * logic instead of a second, slightly-different copy of it. */
export function usePhotoBlob(path, active) {
  const [state, setState] = useState({ loading: true, url: null, error: "" });

  useEffect(() => {
    if (!active) return undefined;
    let cancelled = false;
    let objectUrl = null;
    setState({ loading: true, url: null, error: "" });

    Api.fetchBlob(path)
      .then((blob) => {
        if (cancelled) return;
        if (!blob) {
          setState({ loading: false, url: null, error: "Not on file yet." });
          return;
        }
        objectUrl = URL.createObjectURL(blob);
        setState({ loading: false, url: objectUrl, error: "" });
      })
      .catch((err) => {
        if (cancelled) return;
        const message = err instanceof ApiError && err.status === 403
          ? "Not available."
          : err instanceof ApiError ? err.message : "Couldn't load this image.";
        setState({ loading: false, url: null, error: message });
      });

    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [path, active]);

  return state;
}

/** Exported so the student's own Profile page can render the same two boxes
 * with the same auth-aware loading and the same "not on file yet" fallback,
 * rather than a near-identical second copy that drifts. */
export function PhotoBox({ label, path, active }) {
  const { loading, url, error } = usePhotoBlob(path, active);
  return (
    <div className="flex-1 min-w-0">
      <div className="text-xs font-semibold text-muted uppercase tracking-wide mb-2">{label}</div>
      <div className="aspect-[4/3] rounded-xl border border-border bg-page flex items-center justify-center overflow-hidden">
        {loading ? (
          <span className="skeleton w-full h-full block" />
        ) : url ? (
          <img src={url} alt={label} className="w-full h-full object-cover" />
        ) : (
          <span className="text-xs text-muted px-3 text-center">{error || "Not available."}</span>
        )}
      </div>
    </div>
  );
}

/**
 * `studentId` doubles as the open/closed flag -- render
 * `<IdentityPhotoModal studentId={viewing?.id} .../>` and set `viewing` back
 * to null to close, so callers don't need a separate boolean to keep in
 * sync with which student is showing.
 */
export default function IdentityPhotoModal({ studentId, studentName, onClose }) {
  useEffect(() => {
    if (!studentId) return undefined;
    function onKeyDown(e) {
      if (e.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [studentId, onClose]);

  if (!studentId) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4 animate-fade-in"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
      aria-label="Student identity photos"
    >
      <div
        className="w-full max-w-lg rounded-2xl border border-border bg-surface shadow-card p-5"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between mb-4">
          <h3 className="font-bold text-ink truncate pr-3">{studentName || "Student"} — identity on file</h3>
          <button type="button" onClick={onClose} aria-label="Close" className="shrink-0 text-muted hover:text-ink transition-colors">
            <Icon name="x" width={18} height={18} />
          </button>
        </div>
        <div className="flex gap-4">
          <PhotoBox label="Registered face" path={`/proctoring/face/photo/${studentId}`} active={!!studentId} />
          <PhotoBox label="ID card" path={`/proctoring/id-card/photo/${studentId}`} active={!!studentId} />
        </div>
        <p className="mt-4 text-xs text-muted leading-relaxed">
          Visible to administrators, and to an examiner this student is connected to through their organization or a
          specific exam invite.
        </p>
      </div>
    </div>
  );
}
