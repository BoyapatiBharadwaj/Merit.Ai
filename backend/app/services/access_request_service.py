"""
Examiner access request workflow.

    submit  -> pending
    approve -> creates the examiner account, marks approved
    reject  -> marks rejected, creates nothing

The public submit endpoint is unauthenticated by necessity (the whole point is
that the requester has no account yet), so it is written defensively: it never
reveals whether an email already belongs to a user, and it silently de-dupes
repeat submissions instead of erroring, so the form can't be used to probe for
registered addresses.
"""
import logging
from datetime import datetime, timezone

from fastapi import BackgroundTasks, HTTPException, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.locks import distributed_lock
from app.models.access_request import AccessRequest
from app.models.enums import AccessRequestStatus
from app.models.user import User
from app.repositories import access_request_repository, user_repository
from app.services import email_service, examiner_provisioning_service

logger = logging.getLogger("app")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _admin_recipients(db: Session) -> list[str]:
    """Who to tell about a new access request.

    An explicitly configured ADMIN_NOTIFICATION_EMAIL wins outright -- that is
    the shared operational mailbox, and once it is set, fanning out to every
    individual admin as well would just duplicate the message. Only when it is
    unset does this fall back to the admin accounts in the database, so a
    deployment that configures nothing still gets the notification somewhere
    rather than nowhere.
    """
    configured = settings.ADMIN_NOTIFICATION_EMAIL.strip()
    if configured:
        return [configured]
    return user_repository.list_admin_emails(db)


def _as_utc(value: datetime | None) -> datetime | None:
    """SQLite returns naive datetimes even for timezone=True columns, so a
    direct comparison against an aware _now() raises TypeError. Postgres
    returns aware ones and this is a no-op there -- the same normalisation
    otp_service and attempt_service already do, for the same reason."""
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _notification_is_due(request: AccessRequest) -> bool:
    """Has the cooldown elapsed since admins were last told about this request?

    A NULL last_notified_at means "never told" -- either a row created before
    the column existed, or one whose notification failed to send. Both should
    notify on the next submission rather than stay silent forever.
    """
    last = _as_utc(request.last_notified_at)
    if last is None:
        return True
    elapsed = (_now() - last).total_seconds()
    return elapsed >= settings.ACCESS_REQUEST_RENOTIFY_SECONDS


def _notify_admins(db: Session, request: AccessRequest,
                   background: BackgroundTasks | None) -> bool:
    """Queue one admin-notification email per recipient and enqueue the job
    that delivers them and stamps `last_notified_at` only once every one of
    them has actually gone out. Returns whether anything was queued at all
    (i.e. there was at least one recipient) -- NOT whether delivery
    succeeded, since delivery is now asynchronous (Redis + RQ, see
    app/worker/jobs.py); the caller (submit()/approve(), below) must not
    stamp `last_notified_at` itself any more, because it cannot know the
    outcome yet. That side effect now lives entirely inside
    jobs.notify_access_request, which is the only code that ever sets it.

    `background` is accepted for call-site compatibility but no longer used:
    delivery goes through the same durable, retried queue every other
    transactional email in this app uses.
    """
    subject, text, html = email_service.access_request_message(
        first_name=request.first_name, last_name=request.last_name,
        email=request.email, organization_name=request.organization_name,
        purpose=request.purpose,
    )
    recipients = _admin_recipients(db)
    if not recipients:
        # Worth saying out loud. Silence here means a request landed in the
        # queue and nobody was told, which looks from the outside exactly like
        # the platform working.
        logger.warning("Access request %s recorded but no admin recipient is configured; "
                       "set ADMIN_NOTIFICATION_EMAIL or create an admin account.", request.email)
        return False

    from app.repositories import email_outbox_repository
    from app.worker import jobs

    outbox_ids = []
    for recipient in recipients:
        row = email_outbox_repository.create(
            db, to_address=recipient, subject=subject, text_body=text, html_body=html,
            max_attempts=settings.EMAIL_OUTBOX_MAX_ATTEMPTS,
        )
        outbox_ids.append(row.id)

    # One lock per request id: a resubmission racing the worker's own retry of
    # an earlier notification for the SAME request must not enqueue two
    # overlapping notification jobs that could both eventually try to stamp
    # last_notified_at (harmless on its own, since both would just set the
    # same field to close timestamps, but the lock is what makes "exactly one
    # notification job in flight per request at a time" a guarantee rather
    # than a usually-true accident of timing).
    with distributed_lock(f"access-request-notify:{request.id}", blocking_timeout=0) as acquired:
        if acquired:
            jobs.enqueue_access_request_notification(request.id, outbox_ids)
        else:
            logger.info("A notification job for access request %s is already queued; "
                       "not enqueueing a second one.", request.id)
    return True


def submit(db: Session, *, first_name: str, last_name: str, email: str,
           organization_name: str, purpose: str,
           background: BackgroundTasks | None = None) -> AccessRequest:
    """Record a request from the public form.

    Returns the existing row when this email already has one pending, so a
    double-click (or a refresh-and-resubmit) doesn't fill the admin's queue
    with duplicates. Deliberately does NOT check whether the email already has
    an account: telling an anonymous caller "that address is already
    registered" would turn this form into an account-enumeration oracle.
    """
    existing = access_request_repository.get_pending_by_email(db, email)
    if existing:
        # Re-notify, but no more often than the cooldown.
        #
        # This branch used to return here unconditionally, sending nothing. The
        # intent was right -- a double-click must not spam the admin's inbox --
        # but "never again" is the wrong duration. A request submitted while
        # email was misconfigured stayed permanently unannounced, and the one
        # thing a person naturally tries, submitting again, silently did
        # nothing. That is indistinguishable from a broken mail server, which
        # is exactly what it gets mistaken for.
        #
        # A cooldown keeps the double-click protection (two clicks a second
        # apart send one email) while making the situation recoverable
        # (resubmitting later gets through). It also bounds the abuse: the
        # worst an attacker achieves is one message per address per cooldown.
        if _notification_is_due(existing):
            # last_notified_at is stamped by jobs.notify_access_request itself,
            # only once every recipient has actually been delivered -- see
            # _notify_admins. A delivery that never completes leaves the field
            # exactly as it was (most likely NULL, or older than the cooldown),
            # so the very next submission gets another real chance rather than
            # the platform believing an admin was told when nobody was.
            _notify_admins(db, existing, background)
        return existing

    request = access_request_repository.create(
        db,
        first_name=first_name,
        last_name=last_name,
        email=email,
        organization_name=organization_name,
        purpose=purpose,
    )

    _notify_admins(db, request, background)

    return request


def _load_pending(db: Session, request_id: int) -> AccessRequest:
    request = access_request_repository.get(db, request_id)
    if not request:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Access request not found.")
    if request.status != AccessRequestStatus.PENDING:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"This request has already been {request.status.value}.",
        )
    return request


def approve(db: Session, request_id: int, admin: User, review_note: str | None,
            background: BackgroundTasks | None = None) -> AccessRequest:
    """Approve a request and mint the examiner account it asked for.

    The account creation and the status flip are one commit, not two.
    Previously create_examiner committed the new user/examiner rows on its
    own, then the status update below committed separately -- if the process
    died (or anything else raised) in between, the examiner account existed
    but the request stayed "pending" forever, with no way to re-approve it
    (the email-already-exists check above would now block a retry) and no
    record of who it belonged to. commit=False keeps the account creation
    flushed-but-uncommitted so the single db.commit() below either lands both
    changes or, on failure, rolls back both -- no orphaned account, no
    permanently stuck request.
    """
    # Locked per request id for the life of the approval: two admins clicking
    # "approve" on the same request within milliseconds of each other (or one
    # click double-firing) must never both pass _load_pending's PENDING check
    # and both mint an examiner account for the same email. The lock's window
    # covers the whole approve, not just the account creation, because the
    # status check itself is the thing being raced.
    with distributed_lock(f"access-request-approve:{request_id}", blocking_timeout=2.0) as acquired:
        if not acquired:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "This request is already being approved. Refresh the page in a moment.",
            )
        return _approve_locked(db, request_id, admin, review_note, background)


def _approve_locked(db: Session, request_id: int, admin: User, review_note: str | None,
                    background: BackgroundTasks | None) -> AccessRequest:
    request = _load_pending(db, request_id)

    if user_repository.get_user_by_email(db, request.email.lower()):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "An account already exists for this email. Reject this request instead.",
        )

    # Account creation and the request's status flip happen in ONE commit, via
    # commit=False + this function's own commit below. Two separate commits
    # would mean a crash between them leaves an examiner account that exists
    # while the request stays "pending" forever, with no way to re-approve it
    # (the email-already-exists check above would now block a retry) and no
    # record of who it belonged to.
    #
    # examiner_provisioning_service is the one place that creates an examiner
    # account -- see its module docstring for why the password is never one an
    # admin chose or ever sees.
    user, examiner, token = examiner_provisioning_service.create_examiner_pending_activation(
        db, admin_id=admin.id, first_name=request.first_name, last_name=request.last_name,
        email=request.email, organization_name=request.organization_name, commit=False,
    )

    try:
        request.status = AccessRequestStatus.APPROVED
        request.reviewed_at = _now()
        request.reviewed_by_id = admin.id
        request.review_note = review_note
        request.created_user_id = user.id
        db.commit()
        db.refresh(request)
        db.refresh(user)
        db.refresh(examiner)
    except Exception:
        db.rollback()
        raise

    # Strictly AFTER the commit. Sending inside the try block would mean a
    # rollback still sends someone a working-looking link for an account that
    # does not exist -- and unlike the database, an email cannot be rolled
    # back once it is on its way. Tracked delivery (see email_service's
    # outbox): a failed send here is retried by the `worker` service rather
    # than lost, and an admin can also trigger a resend explicitly.
    examiner_provisioning_service.send_activation_email(
        user, organization_name=request.organization_name, token=token, background=background,
    )

    return request


def reject(db: Session, request_id: int, admin: User, review_note: str | None) -> AccessRequest:
    request = _load_pending(db, request_id)
    try:
        request.status = AccessRequestStatus.REJECTED
        request.reviewed_at = _now()
        request.reviewed_by_id = admin.id
        request.review_note = review_note
        db.commit()
        db.refresh(request)
    except Exception:
        db.rollback()
        raise
    return request
