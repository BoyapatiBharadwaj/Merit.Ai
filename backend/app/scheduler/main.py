"""Merit.Ai Scheduler — the one process that runs periodic work."""
import asyncio
import logging
import signal

from app.core.config import settings
from app.core.locks import distributed_lock
from app.core.redis_client import RedisUnavailableError
from app.database.session import SessionLocal
from app.services import biometric_service, otp_service, reminder_service

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("scheduler")

_shutdown = asyncio.Event()


def _reminder_pass() -> None:
    """One reminder + OTP-purge sweep. Blocking, so callers use to_thread."""
    with distributed_lock("scheduler:reminder-pass", timeout=settings.EXAM_REMINDER_POLL_SECONDS,
                          blocking_timeout=0) as acquired:
        if not acquired:
            return
        db = SessionLocal()
        try:
            sent = reminder_service.send_due_reminders(db)
            if sent:
                logger.info("Sent %s exam reminder(s)", sent)
            otp_service.purge_expired(db)
        finally:
            db.close()


def _retention_pass() -> None:
    """One biometric retention sweep. A no-op unless BIOMETRIC_RETENTION_DAYS > 0."""
    with distributed_lock("scheduler:retention-pass", timeout=60, blocking_timeout=0) as acquired:
        if not acquired:
            return
        db = SessionLocal()
        try:
            erased = biometric_service.purge_expired(db)
            if erased:
                logger.info("Retention sweep erased biometrics for %s student(s)", erased)
        finally:
            db.close()


async def _every(seconds: float, work, label: str) -> None:
    """Run `work` on an interval until shutdown, surviving its own failures."""
    logger.info("Started %s loop (every %ss)", label, seconds)
    while not _shutdown.is_set():
        try:
            await asyncio.wait_for(_shutdown.wait(), timeout=seconds)
            break  # shutdown requested during the wait
        except asyncio.TimeoutError:
            pass  # interval elapsed -- do the work

        try:
            # to_thread because SQLAlchemy's sync session and smtplib both block; running them
            # inline would stall the event loop and with it every other loop in this process.
            await asyncio.to_thread(work)
        except Exception:
            logger.exception("%s pass failed; will retry on the next tick", label)


async def _main() -> None:
    loops = []

    if settings.EXAM_REMINDER_ENABLED:
        loops.append(_every(settings.EXAM_REMINDER_POLL_SECONDS, _reminder_pass, "exam reminder"))
    else:
        logger.info("Exam reminders disabled (EXAM_REMINDER_ENABLED=false)")

    if settings.BIOMETRIC_RETENTION_DAYS > 0:
        loops.append(_every(settings.BIOMETRIC_PURGE_INTERVAL_HOURS * 3600,
                            _retention_pass, "biometric retention"))
    else:
        # Not an error. 0 means "never auto-delete", which is the default and a
        # deliberate policy choice -- see biometric_service.
        logger.info("Biometric retention sweep disabled (BIOMETRIC_RETENTION_DAYS=0)")

    if not loops:
        logger.warning("Nothing scheduled; this process has no work to do.")

    await asyncio.gather(*loops)
    logger.info("Scheduler stopped")


def _request_shutdown(*_args) -> None:
    logger.info("Shutdown signal received")
    _shutdown.set()


if __name__ == "__main__":
    # SIGTERM is what `docker stop` sends. Handling it means an in-flight sweep finishes and the
    # loops exit cleanly, instead of the container being killed ten seconds later mid-query.
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _request_shutdown)
        except NotImplementedError:
            # Windows has no add_signal_handler; the container runs Linux, so
            # this only matters when someone runs the module directly on a dev box.
            signal.signal(sig, _request_shutdown)
    loop.run_until_complete(_main())
