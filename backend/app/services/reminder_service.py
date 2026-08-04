"""
Pre-exam reminders.

A background loop that wakes every EXAM_REMINDER_POLL_SECONDS, finds published
exams starting in about EXAM_REMINDER_MINUTES_BEFORE, and emails the address the
examiner nominated -- once per exam, ever.

Why a polling loop and not a scheduled job per exam:

An in-memory timer per exam is the obvious design and the wrong one. Exams are
created and rescheduled at arbitrary times, so every schedule edit would have to
find and cancel its timer, and a process restart -- a deploy, a crash, a
`docker compose up` -- would silently drop every pending reminder with nothing
to rebuild them from. Polling a table holds no state in the process at all: it
is correct after a restart by construction, because the answer to "what needs a
reminder" is recomputed from the database each pass rather than remembered.

Exactly-once delivery comes from `Exam.reminder_sent_at`, not from the loop
being careful. The window below is deliberately wider than one poll interval so
a slow tick cannot step over an exam entirely, which means the same exam matches
on consecutive passes; the stamped column is what stops it being emailed twice.
Stamping happens BEFORE the send is queued, so the worst case is a reminder that
was recorded and failed to deliver -- logged, and visible -- rather than one
delivered repeatedly to a recipient who cannot make it stop.

Scope limit, stated rather than discovered later: this assumes a single API
process, which is what this stack runs (see the note in app/core/rate_limit.py
making the same assumption for the same reason). Several processes would each
run their own loop and could both match the same exam before either commits its
stamp, sending the reminder more than once. The fix if that day comes is a
`SELECT ... FOR UPDATE SKIP LOCKED` on the candidate rows, which Postgres
supports and SQLite does not -- hence not doing it speculatively now.
"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.core.config import settings
from app.database.session import SessionLocal
from app.models.enums import ExamStatus
from app.models.exam import Exam
from app.services import biometric_service, email_service, exam_service, otp_service

logger = logging.getLogger("app")

_task: asyncio.Task | None = None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def due_exams(db: Session, *, now: datetime | None = None) -> list[Exam]:
    """Published exams whose start is inside the reminder window and which have
    not been reminded yet.

    The window runs from `now` to `now + minutes_before + one poll interval`.
    That upper padding is what makes the loop robust to its own timing: with a
    window of exactly `minutes_before`, an exam starting 5m01s from a tick that
    is then delayed slightly is outside the window on this pass and already
    inside 5 minutes on the next, so it would never be picked up at all.
    Overlapping the window instead means every exam is seen at least once, and
    reminder_sent_at handles the resulting duplicates.

    Exams already past their start time are excluded: a reminder that arrives
    after the exam opened is noise, and on a process that was down for an hour
    it would arrive as a burst of them.
    """
    now = now or _now()
    horizon = now + timedelta(
        minutes=settings.EXAM_REMINDER_MINUTES_BEFORE,
        seconds=settings.EXAM_REMINDER_POLL_SECONDS,
    )
    return (
        db.query(Exam)
        .filter(
            Exam.status == ExamStatus.PUBLISHED,
            Exam.reminder_sent_at.is_(None),
            Exam.notify_email.isnot(None),
            Exam.start_time.isnot(None),
            Exam.start_time > now,
            Exam.start_time <= horizon,
        )
        .all()
    )


def send_due_reminders(db: Session, *, now: datetime | None = None) -> int:
    """One pass. Returns how many reminders were sent.

    Synchronous and dependency-free so it can be called directly from a test or
    a management script without standing up the event loop -- the async wrapper
    below adds scheduling, not behaviour.
    """
    now = now or _now()
    sent = 0

    for exam in due_exams(db, now=now):
        starts_at = _as_utc(exam.start_time)
        minutes_out = max(int((starts_at - now).total_seconds() // 60), 0) if starts_at else 0

        # Stamped and committed before the send is attempted -- see the module
        # docstring on why duplicate delivery is the worse failure to risk here.
        exam.reminder_sent_at = now
        try:
            db.commit()
        except Exception:
            db.rollback()
            logger.exception("Could not stamp reminder_sent_at for exam %s; skipping to avoid a resend loop", exam.id)
            continue

        subject, text, html = email_service.exam_reminder_message(
            exam_title=exam.title,
            minutes_before=minutes_out or settings.EXAM_REMINDER_MINUTES_BEFORE,
            starts_at=exam_service.format_exam_time(exam.start_time),
            duration_minutes=exam.duration_minutes,
        )
        if email_service.send(to=exam.notify_email, subject=subject, text_body=text, html_body=html):
            sent += 1
        else:
            # Deliberately not un-stamping. A failed send that resets the marker
            # would be retried on the very next poll, and if the failure is
            # persistent (bad address, auth misconfigured) that is an infinite
            # retry loop against the SMTP server for every exam. One clear log
            # line, and the exam still opens normally.
            logger.warning("Reminder for exam %s (%s) could not be delivered to %s",
                           exam.id, exam.title, exam.notify_email)

    return sent


async def _loop() -> None:
    """Poll forever. Every iteration is individually guarded.

    A single bad pass -- a database blip, an unexpected row -- must not kill the
    loop, because nothing would restart it short of restarting the process, and
    the failure would be silent: reminders would simply stop, with the app
    otherwise healthy. So the body catches everything except cancellation and
    tries again next tick.
    """
    logger.info("Exam reminder scheduler started (every %ss, %s minutes before start)",
                settings.EXAM_REMINDER_POLL_SECONDS, settings.EXAM_REMINDER_MINUTES_BEFORE)
    while True:
        try:
            await asyncio.sleep(settings.EXAM_REMINDER_POLL_SECONDS)
            # to_thread because every call below is blocking: SQLAlchemy's sync
            # session and smtplib both are. Running them inline would stall the
            # event loop -- and therefore every concurrent request this process
            # is serving -- for the duration of an SMTP handshake.
            await asyncio.to_thread(_run_once)
        except asyncio.CancelledError:
            logger.info("Exam reminder scheduler stopping")
            raise
        except Exception:
            logger.exception("Exam reminder pass failed; will retry on the next tick")


_last_biometric_purge: datetime | None = None


def _run_once() -> None:
    global _last_biometric_purge

    db = SessionLocal()
    try:
        count = send_due_reminders(db)
        if count:
            logger.info("Sent %s exam reminder(s)", count)
        # Piggy-backed on this loop rather than given a scheduler of its own:
        # it needs to happen occasionally, never urgently, and one background
        # task is one thing to reason about instead of two.
        otp_service.purge_expired(db)

        # Biometric retention runs on its own much slower cadence, gated here
        # rather than by a second loop. It walks every student with a live face
        # profile, which is far too expensive to repeat every minute alongside
        # the reminder poll -- and unlike a reminder, being a few hours late
        # costs nothing. Skipped entirely when retention is disabled, which is
        # the default (see biometric_service on why automatic deletion is
        # opt-in).
        if settings.BIOMETRIC_RETENTION_DAYS > 0:
            interval = timedelta(hours=settings.BIOMETRIC_PURGE_INTERVAL_HOURS)
            if _last_biometric_purge is None or _now() - _last_biometric_purge >= interval:
                _last_biometric_purge = _now()
                biometric_service.purge_expired(db)
    finally:
        db.close()


def start() -> None:
    """Begin polling. Idempotent, and a no-op when disabled or unconfigured."""
    global _task
    if not settings.EXAM_REMINDER_ENABLED:
        logger.info("Exam reminder scheduler disabled (EXAM_REMINDER_ENABLED=false)")
        return
    if not email_service.is_enabled():
        # Without this the loop would wake every minute forever to discover it
        # has no way to send anything, and would stamp reminder_sent_at on exams
        # that were never actually reminded -- so enabling email later would
        # silently skip every exam the loop had already "handled".
        logger.info("Exam reminder scheduler not started: email delivery is not configured")
        return
    if _task is not None and not _task.done():
        return
    _task = asyncio.create_task(_loop())


async def stop() -> None:
    """Cancel the loop and wait for it to unwind. Safe to call when not running."""
    global _task
    if _task is None:
        return
    _task.cancel()
    try:
        await _task
    except (asyncio.CancelledError, Exception):
        pass
    _task = None
