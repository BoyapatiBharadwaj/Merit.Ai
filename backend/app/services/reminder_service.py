"""Pre-exam reminders."""
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
    """Published exams whose start is inside the reminder window and which have not been reminded yet."""
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
    """One pass. Returns how many reminders were sent."""
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
            # Deliberately not un-stamping.
            logger.warning("Reminder for exam %s (%s) could not be delivered to %s",
                           exam.id, exam.title, exam.notify_email)

    return sent


async def _loop() -> None:
    """Poll forever. Every iteration is individually guarded."""
    logger.info("Exam reminder scheduler started (every %ss, %s minutes before start)",
                settings.EXAM_REMINDER_POLL_SECONDS, settings.EXAM_REMINDER_MINUTES_BEFORE)
    while True:
        try:
            await asyncio.sleep(settings.EXAM_REMINDER_POLL_SECONDS)
            # to_thread because every call below is blocking:
            # SQLAlchemy's sync session and smtplib both are.
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
        # Piggy-backed on this loop rather than given a scheduler of its own.
        otp_service.purge_expired(db)

        # Biometric retention runs on its own much slower
        # cadence, gated here rather than by a second loop.
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
        # Without this the loop would wake every minute forever to
        # discover it has no way to send anything, and would stamp
        # reminder_sent_at on exams that were never actually reminded.
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
