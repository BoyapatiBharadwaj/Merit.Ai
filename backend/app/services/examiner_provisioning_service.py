"""
The one place an examiner account is created.

Two flows lead here: an admin approving a public access request
(access_request_service.approve), and an admin creating an examiner directly
from the Examiners page (POST /auth/examiners). Both used to duplicate the same
three steps -- mint the account, issue an activation token, send the email --
and the two copies had already drifted: approval minted a random, unusable
password and mailed an activation link; manual creation let the admin type a
password and mailed (or displayed) it back in plain text. An admin who could
see a new examiner's password could authenticate as them, and that password
sat in the admin's browser tab and the admin's own memory for as long as either
lasted -- neither is a property "an admin created this account" should imply.

Consolidating both onto this module is what makes it structurally impossible
for the two flows to disagree about that again: there is exactly one function
that creates an examiner, and it never accepts a caller-supplied password.
"""
import logging
import secrets

from fastapi import BackgroundTasks, HTTPException, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.locks import distributed_lock
from app.models.user import User
from app.repositories import user_repository
from app.services import auth_service, email_service, otp_service

logger = logging.getLogger("app")


def create_examiner_pending_activation(db: Session, *, admin_id: int, first_name: str, last_name: str,
                                       email: str, organization_name: str | None, commit: bool = True):
    """Mint the account and its activation token. Does NOT send the email.

    Split out so a caller that must land the account creation atomically with
    something else -- access_request_service.approve flips the request to
    "approved" in the SAME commit as minting the account, so a crash between
    the two can never leave an examiner that exists with a request stuck
    pending forever -- can pass `commit=False` and fold this into its own
    transaction. `provision_examiner` below is the commit=True convenience for
    the flow that has nothing else to commit alongside it.

    Locked per email for the life of the creation: two concurrent callers for
    the SAME address (an admin double-submitting the "create examiner" form,
    or an approval racing a manual creation for the same email) must not both
    pass auth_service.create_examiner's uniqueness check before either has
    committed -- a plain uniqueness constraint at commit time would still
    catch it, but only after both had already sent an activation email and
    picked up a confusing 500/409 instead of a clear "someone just did that".

    The account is born with 256 bits of random noise as its password --
    nobody, including the admin who ran this, ever knows it -- and
    `must_change_password=True`, so nothing the account does is attributable to
    its owner until they have set their own password through the activation
    link.

    Returns `(user, examiner, token)`. The token is a plaintext secret that
    exists only in this return value and the email it is about to go into --
    it is stored in the database as a hash (see otp_service), never as itself.
    """
    normalized_email = email.strip().lower()
    with distributed_lock(f"examiner-create:{normalized_email}", blocking_timeout=2.0) as acquired:
        if not acquired:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "An account for this email is already being created. Please wait a moment and check again.",
            )
        return _create_examiner_pending_activation_locked(
            db, admin_id=admin_id, first_name=first_name, last_name=last_name,
            email=email, organization_name=organization_name, commit=commit,
        )


def _create_examiner_pending_activation_locked(db: Session, *, admin_id: int, first_name: str, last_name: str,
                                               email: str, organization_name: str | None, commit: bool):
    placeholder = secrets.token_urlsafe(32)

    user, examiner = auth_service.create_examiner(
        db, admin_id, first_name, last_name, email, placeholder, organization_name, commit=False,
    )
    user.must_change_password = True

    try:
        # Minted inside the same transaction so a rollback leaves no usable
        # link behind -- the token is handed to the mailer strictly after the
        # commit below, because an email cannot be rolled back.
        token, _expires_at = otp_service.issue_activation_token(db, email=user.email)
        if commit:
            db.commit()
            db.refresh(user)
            db.refresh(examiner)
        else:
            db.flush()
    except Exception:
        db.rollback()
        raise

    return user, examiner, token


def provision_examiner(db: Session, *, admin_id: int, first_name: str, last_name: str,
                       email: str, organization_name: str | None,
                       background: BackgroundTasks | None = None) -> tuple[User, bool]:
    """Create an examiner account and queue the activation email, in one call.

    For the manual-creation flow (POST /auth/examiners), which has nothing
    else to commit alongside the account. Returns `(user, activation_sent)`;
    the account exists either way, because a mail outage must not block
    account creation the way it would a signup gate. An admin can retry
    delivery with the resend-activation action, and the email's actual,
    final delivery outcome -- sent, or failed after every retry -- is always
    in the `email_outbox` table (see email_service.enqueue_tracked), never
    only in this synchronous response.
    """
    user, _examiner, token = create_examiner_pending_activation(
        db, admin_id=admin_id, first_name=first_name, last_name=last_name,
        email=email, organization_name=organization_name, commit=True,
    )
    activation_sent = send_activation_email(
        user, organization_name=organization_name, token=token, background=background,
    )
    return user, activation_sent


def send_activation_email(user: User, *, organization_name: str | None, token: str,
                          background: BackgroundTasks | None = None) -> bool:
    """Queue one activation email for an already-issued token, and report
    whether it was successfully QUEUED -- not whether it has been delivered.

    Split out from `provision_examiner` so `resend_activation` (below) can
    reuse the exact same message-building and queueing path for a freshly
    reissued token, instead of a second, slightly different copy of the email.

    Delivery is asynchronous (Redis + RQ, see email_service.enqueue_tracked),
    so this function's return value can only ever answer "did queueing
    succeed", the same way an access request's HTTP 201 answers "was this
    recorded" and not "has an admin read it yet". `enqueue_tracked` itself
    raises (converted to a 503 for the whole request -- see
    app/core/redis_client.py) if Redis cannot be reached at all, which is the
    one case queueing genuinely fails; short of that, this always returns
    True, and the real, final, retried delivery outcome lives in the
    `email_outbox` table, which is what an admin's resend-activation action
    and any operational check should read instead of this boolean.
    """
    subject, text, html = email_service.activation_message(
        full_name=user.full_name,
        email=user.email,
        activation_url=otp_service.activation_link(token, user.email),
        expires_hours=settings.ACTIVATION_TTL_HOURS,
        organization_name=organization_name,
    )
    from app.database.session import SessionLocal

    # enqueue_tracked needs a session to write the outbox row. The caller's
    # own session is mid-request-lifecycle in some call paths (approve() has
    # already committed by this point) and closed by the time a background
    # task would run in others, so this opens a short-lived session of its own
    # rather than assuming either is safe to reuse.
    db = SessionLocal()
    try:
        email_service.enqueue_tracked(db, to=user.email, subject=subject, text_body=text, html_body=html)
    finally:
        db.close()

    return True


def resend_activation(db: Session, examiner_user_id: int,
                      background: BackgroundTasks | None = None) -> bool:
    """Issue a fresh activation link for an existing, not-yet-activated
    examiner and mail it.

    For a link that expired, or one whose email genuinely failed to arrive the
    first time. Reissuing invalidates any previous outstanding token for this
    address (see otp_service.issue_activation_token) -- there is never more
    than one live activation link for an account at a time.
    """
    user = user_repository.get_user_by_id(db, examiner_user_id)
    if not user or (user.role.name or "").lower() != "examiner":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Examiner not found.")
    if not user.must_change_password:
        # Already activated -- a stale link has nothing left to do, and
        # sending one would just confuse someone who can already sign in.
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "This examiner has already activated their account.")

    token, _ = otp_service.issue_activation_token(db, email=user.email)
    db.commit()

    organization_name = user.examiner_profile.organization_name if user.examiner_profile else None
    return send_activation_email(user, organization_name=organization_name, token=token, background=background)
