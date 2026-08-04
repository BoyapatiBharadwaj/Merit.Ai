"""
Exam lockdown enforcement.

The browser side of lockdown (fullscreen, blur/visibility listeners, blocked
shortcuts) is inherently defeatable -- a student can open devtools and unhook
every listener. So the *authoritative* strike count lives here and is derived
from persisted ProctorEvent rows, which means:

  * refreshing the page does not reset the count
  * clearing localStorage does not reset the count
  * killing the client JS stops new strikes being reported, but any strike
    already banked stays banked

When the limit is reached the attempt is force-submitted server-side, so the
termination is a fact in the database rather than a client-side courtesy.

What this genuinely cannot do: stop an OS-level screenshot, a screen
recorder, or a second device pointed at the monitor. Those need a lockdown
browser (Safe Exam Browser) or a proctored physical environment. Attempts we
*can* observe are recorded as violations so they surface on the report.
"""
import logging

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.enums import AttemptStatus
from app.repositories import attempt_repository, proctor_repository
from app.services import attempt_service, proctor_service

logger = logging.getLogger("app")

# Only these count toward termination. Copy/paste and right-click attempts are
# logged as violations but never end an exam -- they are blocked in the browser
# anyway, so the student gains nothing and a mis-keyed Ctrl+C shouldn't be
# treated the same as deliberately leaving the exam window.
#
# screen_share_stopped is included alongside fullscreen_exit/tab_switch: ending
# the shared-screen stream mid-exam is exactly as deliberate a signal as
# Alt-Tabbing out of the window, and the frontend (lib/lockdown.js) reports it
# through this same strike endpoint rather than a separate one.
STRIKE_EVENT_TYPES = ["fullscreen_exit", "tab_switch", "screen_share_stopped"]


def _debounced_strike_count(events: list) -> int:
    """Collapse bursts of events into single strikes.

    One physical Alt-Tab typically fires window blur, visibilitychange, and
    fullscreenchange within milliseconds of each other. Counting each one
    separately would burn a student's entire strike budget on a single
    accidental keystroke, so events inside the debounce window after a counted
    strike are folded into it.
    """
    window = settings.LOCKDOWN_STRIKE_DEBOUNCE_SECONDS
    # A window of zero (or negative) means debouncing is switched off: every
    # recorded event is its own strike. Handled explicitly because the
    # comparison below is strictly-greater-than, so a zero window would
    # otherwise still collapse same-timestamp events -- which is the opposite
    # of what "no debounce" should mean.
    if window <= 0:
        return len(events)

    strikes = 0
    last_counted_at = None
    for event in events:
        created = event.created_at
        if created is None:
            # No usable timestamp (possible on backends with coarse clock
            # resolution): count it rather than silently swallowing a breach.
            strikes += 1
            continue
        if last_counted_at is None or (created - last_counted_at).total_seconds() > window:
            strikes += 1
            last_counted_at = created
    return strikes


def strike_status(db: Session, attempt_id: int) -> dict:
    events = proctor_repository.list_events_by_types(db, attempt_id, STRIKE_EVENT_TYPES)
    strikes = _debounced_strike_count(events)
    limit = settings.LOCKDOWN_STRIKE_LIMIT
    return {
        "strikes": strikes,
        "limit": limit,
        "remaining": max(0, limit - strikes),
        "terminated": strikes >= limit,
    }


def record_strike(db: Session, student_id: int, attempt_id: int, event_type: str, description: str | None) -> dict:
    """Log a lockdown breach and, if it takes the student to the limit,
    force-submit the attempt.

    Returns the post-write status so the client can render the correct
    warning ("1 of 3") or the terminal state without a second round-trip.
    """
    if event_type not in STRIKE_EVENT_TYPES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Unsupported lockdown event type.")

    attempt = attempt_repository.get_attempt(db, attempt_id)
    if not attempt or attempt.student_id != student_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Attempt not found.")

    # An already-finished attempt still answers honestly rather than 4xx-ing:
    # the client may be flushing a queued event after auto-submit landed.
    if attempt.status != AttemptStatus.IN_PROGRESS:
        return {**strike_status(db, attempt_id), "terminated": True, "already_closed": True}

    proctor_service.log_event(db, attempt_id, event_type, description, None)

    state = strike_status(db, attempt_id)
    if not state["terminated"]:
        return {**state, "already_closed": False}

    proctor_service.log_event(
        db, attempt_id, "lockdown_terminated",
        f"Attempt auto-submitted after {state['strikes']} lockdown violations (limit {state['limit']}).", None,
    )
    try:
        attempt_service.submit_attempt(db, student_id, attempt_id, auto=True)
    except HTTPException:
        # Raced with a normal submit or the timer's auto-submit -- the exam is
        # closed either way, which is the outcome we wanted.
        logger.info("Lockdown termination raced with an existing submit for attempt %s", attempt_id)
    return {**state, "already_closed": False}
