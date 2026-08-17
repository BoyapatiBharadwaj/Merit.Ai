/**
 * Shared status-badge styling + date/number formatting for the admin drill-down pages
 * (Examiners, Candidates, Exams, Live Sessions, Violations, Candidate Exam Report).
 */

const TONE_CLASSES = {
  success: "bg-success/10 text-success",
  warning: "bg-warning/10 text-warning",
  danger: "bg-danger/10 text-danger",
  primary: "bg-primary/10 text-primary",
  muted: "bg-page text-muted border border-border",
};

export function badgeClass(tone) {
  return `inline-flex items-center rounded-full px-2.5 py-1 text-xs font-semibold capitalize ${TONE_CLASSES[tone] || TONE_CLASSES.muted}`;
}

const STATUS_TONE = {
  active: "success",
  upcoming: "primary",
  completed: "muted",
  draft: "muted",
  "in progress": "warning",
  "not started": "muted",
  absent: "muted",
  terminated: "danger",
  passed: "success",
  failed: "danger",
  low: "success",
  medium: "warning",
  high: "danger",
  verified: "success",
  pending: "warning",
  confirmed: "danger",
  dismissed: "muted",
  yes: "success",
  no: "muted",
};

/** Looks up the badge tone for any of the small enum-like strings the admin
 * pages render (exam bucket, exam/attempt status, result, risk tier,
 * verification, admin decision) -- unrecognized or missing values fall back
 * to "muted" rather than throwing, since "—" placeholders pass through here too. */
export function toneFor(value) {
  if (!value) return "muted";
  return STATUS_TONE[String(value).toLowerCase()] || "muted";
}

export function fmtDate(value) {
  if (!value) return "—";
  return new Date(value).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
}

export function fmtDateTime(value) {
  if (!value) return "—";
  return new Date(value).toLocaleString(undefined, {
    day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit",
  });
}

export function fmtTime(value) {
  if (!value) return "—";
  return new Date(value).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export function fmtPercent(value) {
  return value === null || value === undefined ? "—" : `${value}%`;
}

export function fmtNum(value) {
  return value === null || value === undefined ? "—" : value;
}

export function fmtMinutes(value) {
  if (value === null || value === undefined) return "—";
  const h = Math.floor(value / 60);
  const m = Math.round(value % 60);
  return h > 0 ? `${h}h ${m}m` : `${m}m`;
}

export function fmtSeconds(value) {
  if (value === null || value === undefined) return "—";
  return fmtMinutes(Math.round(value / 60));
}

/** Short, table-friendly phrasing for each EventType value -- mirrors
 * admin_service.py's _EVENT_LABELS (used there for the templated proctoring
 * summary sentence), just trimmed to a label rather than a full clause. */
const EVENT_TYPE_LABELS = {
  no_face: "Face not visible",
  multiple_faces: "Multiple faces detected",
  face_mismatch: "Face mismatch",
  fullscreen_exit: "Exited fullscreen",
  tab_switch: "Switched tabs",
  copy_paste_attempt: "Copy/paste blocked",
  right_click_attempt: "Right-click blocked",
  noise_detected: "Background noise detected",
  noise_detected_loud: "Loud sustained audio",
  id_name_mismatch: "ID name mismatch",
  spoof_detected: "Spoofed camera suspected",
  phone_detected: "Phone detected",
  book_detected: "Book/notes detected",
  multiple_persons_detected: "Multiple people detected",
  looking_away: "Looked away repeatedly",
  gaze_deviation: "Gaze deviation",
  external_monitor_detected: "External monitor detected",
  screenshot_attempt: "Screenshot attempt",
  screen_share_stopped: "Screen sharing stopped",
  lockdown_terminated: "Attempt force-closed",
};

export function eventTypeLabel(eventType) {
  return EVENT_TYPE_LABELS[eventType] || String(eventType || "").replace(/_/g, " ");
}

/** The eight fixed proctoring cards the Candidate Exam Report shows, derived
 * from the staff-report's violation_type_counts map (event_type -> count).
 * Audio Alerts folds both noise-detection tiers together -- see
 * EventType.NOISE_DETECTED / NOISE_DETECTED_LOUD's two-tier design. */
export function proctoringCardCounts(violationTypeCounts) {
  const c = violationTypeCounts || {};
  return {
    tabSwitches: c.tab_switch || 0,
    fullscreenExits: c.fullscreen_exit || 0,
    faceMismatches: c.face_mismatch || 0,
    multiplePersons: c.multiple_persons_detected || 0,
    phoneDetections: c.phone_detected || 0,
    audioAlerts: (c.noise_detected || 0) + (c.noise_detected_loud || 0),
  };
}
