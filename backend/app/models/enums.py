"""
Shared enumerations used across models.
"""
import enum
from sqlalchemy import Enum


def db_enum(enum_class):
    return Enum(enum_class, values_callable=lambda values: [item.value for item in values])


class RoleName(str, enum.Enum):
    ADMIN = "admin"
    EXAMINER = "examiner"
    STUDENT = "student"


class ExamStatus(str, enum.Enum):
    DRAFT = "draft"
    PUBLISHED = "published"
    CLOSED = "closed"


class QuestionType(str, enum.Enum):
    MCQ = "mcq"
    # "Select all that apply" -- one or more options marked correct, graded as an exact match of
    # the selected set against the correct set (see attempt_service._grade_multi_select_answer).
    MULTI_SELECT = "multi_select"
    CODING = "coding"


class AttemptStatus(str, enum.Enum):
    IN_PROGRESS = "in_progress"
    SUBMITTED = "submitted"
    AUTO_SUBMITTED = "auto_submitted"
    TERMINATED = "terminated"


class EventType(str, enum.Enum):
    NO_FACE = "no_face"
    MULTIPLE_FACES = "multiple_faces"
    FACE_MISMATCH = "face_mismatch"
    FULLSCREEN_EXIT = "fullscreen_exit"
    TAB_SWITCH = "tab_switch"
    COPY_PASTE_ATTEMPT = "copy_paste_attempt"
    RIGHT_CLICK_ATTEMPT = "right_click_attempt"
    NOISE_DETECTED = "noise_detected"
    # Sustained, louder audio -- a distinct, more severe tier above NOISE_DETECTED (see the
    # two-threshold detector in proctoring.js), not a replacement for it.
    NOISE_DETECTED_LOUD = "noise_detected_loud"
    ID_NAME_MISMATCH = "id_name_mismatch"
    SPOOF_DETECTED = "spoof_detected"
    PHONE_DETECTED = "phone_detected"
    BOOK_DETECTED = "book_detected"
    MULTIPLE_PERSONS_DETECTED = "multiple_persons_detected"
    LOOKING_AWAY = "looking_away"
    GAZE_DEVIATION = "gaze_deviation"
    EXTERNAL_MONITOR_DETECTED = "external_monitor_detected"
    # PrintScreen / snipping-shortcut press. A browser
    # cannot actually prevent an OS-level screen capture.
    SCREENSHOT_ATTEMPT = "screenshot_attempt"
    # The student's shared-screen stream ended mid-exam (closed the shared window/tab, clicked
    # the browser's own "Stop sharing" bar, revoked the permission).
    SCREEN_SHARE_STOPPED = "screen_share_stopped"
    # Raised when a strike limit is reached and the attempt is force-closed.
    LOCKDOWN_TERMINATED = "lockdown_terminated"


class AccessRequestStatus(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class Severity(str, enum.Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class AdminDecision(str, enum.Enum):
    """A reviewer's verdict on one logged violation, shown
    in the candidate exam report's violation timeline.
    """
    PENDING = "pending"
    CONFIRMED = "confirmed"
    DISMISSED = "dismissed"


class ResultsReleaseMode(str, enum.Enum):
    """When a candidate may see their OWN score/pass-fail outcome for an exam."""
    # As soon as this candidate's own attempt is submitted (subject to the exam's optional
    # release_results_at delay, unchanged from before this existed).
    IMMEDIATE = "immediate"
    # Withheld from every candidate until the exam's own end_time has passed, regardless of when
    # any individual candidate submitted.
    AFTER_END_TIME = "after_end_time"
