"""
Merit.Ai Scheduler — the one process that runs periodic work.

Split out of core-api for a specific reason. The reminder loop used to start
inside the API's lifespan, which was correct only while core-api was a single
uvicorn worker. The moment it runs N workers, N copies of that loop wake up on
the same schedule and each tries to send the same reminders -- and while
`Exam.reminder_sent_at` stops most duplicates, two workers can still both read a
NULL before either commits. Moving the loop into its own single-replica service
removes the race by construction rather than papering over it.

This is why `deploy.replicas` on this service in docker-compose.yml must stay 1.
Scaling the API is now free; scaling this is not, and there is nothing to gain
from it -- the work is a handful of queries a minute, not a bottleneck.

Deliberately NOT a FastAPI app: it serves no traffic and needs no port. It is a
plain asyncio process, so nothing about it invites being load-balanced.

What it runs:
  * exam reminders     -- every EXAM_REMINDER_POLL_SECONDS
  * OTP purge          -- alongside the reminder pass
  * biometric retention -- every BIOMETRIC_PURGE_INTERVAL_HOURS, when enabled

Each pass also takes a short-lived Redis lock naming the pass (see
app/core/locks.py) before doing anything. Redundant while this service is
pinned to one replica, but the whole point of a lock here is to make "only one
of these ever runs at a time" true by construction rather than by a deploy
config nobody is guaranteed to remember not to change -- the same reasoning
`deploy.replicas: 1` documents above, enforced a second way. If Redis itself
is unreachable when a pass tries to acquire its lock, the pass is skipped for
that tick and retried on the next one (see `_every`'s exception handling) --
the same "controlled failure, not a silent bypass" this rewrite requires of
every Redis-backed operation.
"""
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
    """Run `work` on an interval until shutdown, surviving its own failures.

    Each pass is individually guarded. A single bad sweep -- a database blip, an
    unexpected row -- must not kill the loop, because nothing would restart it
    short of restarting the container, and the failure would be silent: the work
    simply stops while the process stays healthy.
    """
    logger.info("Started %s loop (every %ss)", label, seconds)
    while not _shutdown.is_set():
        try:
            await asyncio.wait_for(_shutdown.wait(), timeout=seconds)
            break  # shutdown requested during the wait
        except asyncio.TimeoutError:
            pass  # interval elapsed -- do the work

        try:
            # to_thread because SQLAlchemy's sync session and smtplib both
            # block; running them inline would stall the event loop and with it
            # every other loop in this process.
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
    # SIGTERM is what `docker stop` sends. Handling it means an in-flight sweep
    # finishes and the loops exit cleanly, instead of the container being killed
    # ten seconds later mid-query.
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
