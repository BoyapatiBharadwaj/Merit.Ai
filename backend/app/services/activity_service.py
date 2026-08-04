"""
Recording and reading the per-account activity trail.

Every caller goes through `record()`. Two properties it guarantees, because
callers are spread across auth, users, proctoring and admin code and cannot each
be relied on to remember:

**It never raises.** An audit write failing must not fail the action it was
describing. A student who logged in successfully has logged in, whether or not
the row recording it landed -- turning that into a 500 would mean the logging
system could take down the thing it exists to observe. Failures are logged and
swallowed, the same graceful-degradation contract email_service.send follows.

**It never records a secret.** Passwords, OTP codes, tokens and embeddings are
not accepted by any helper here, and `context` is documented as safe-to-display.
An audit log is read by more people than the data it describes.
"""
import json
import logging
from datetime import datetime, timedelta, timezone

from fastapi import Request
from sqlalchemy.orm import Session

from app.models.activity_log import ActivityLog, ActivityType
from app.models.user import User

logger = logging.getLogger("app")

# Shown in the admin timeline. Written as complete sentences about the SUBJECT,
# so a row reads correctly whether the actor was the person themselves or an
# administrator acting on them.
ACTIVITY_LABELS: dict[str, str] = {
    ActivityType.SIGNED_UP.value: "Created their account",
    ActivityType.LOGGED_IN.value: "Signed in",
    ActivityType.LOGIN_FAILED.value: "Failed sign-in attempt",
    ActivityType.FACE_REGISTERED.value: "Registered their face",
    ActivityType.ID_VERIFIED.value: "ID card verified",
    ActivityType.ID_VERIFICATION_FAILED.value: "ID card verification failed",
    ActivityType.IDENTITY_LOCKED.value: "Identity locked (face + ID both verified)",
    ActivityType.IDENTITY_UNLOCKED.value: "Identity unlocked by an administrator",
    ActivityType.BIOMETRICS_ERASED.value: "Face and ID data erased",
    ActivityType.PASSWORD_CHANGED.value: "Changed their password",
    ActivityType.PASSWORD_RESET_BY_ADMIN.value: "Password reset by an administrator",
    ActivityType.PASSWORD_RESET_BY_EMAIL.value: "Password reset using an emailed code",
    ActivityType.ACCOUNT_CREATED_BY_ADMIN.value: "Account created by an administrator",
    ActivityType.ACCOUNT_DISABLED.value: "Account disabled",
    ActivityType.ACCOUNT_ENABLED.value: "Account re-enabled",
    ActivityType.ACCOUNT_DELETED.value: "Account deleted",
    ActivityType.PROFILE_UPDATED.value: "Profile details updated",
}

# Entries an administrator should be able to spot at a glance.
NOTABLE_ACTIVITY = {
    ActivityType.LOGIN_FAILED.value,
    ActivityType.PASSWORD_RESET_BY_ADMIN.value,
    ActivityType.IDENTITY_UNLOCKED.value,
    ActivityType.BIOMETRICS_ERASED.value,
    ActivityType.ACCOUNT_DISABLED.value,
    ActivityType.ACCOUNT_DELETED.value,
}


def client_ip(request: Request | None) -> str | None:
    """Source address of the request, if there is one.

    `request.client.host` is the TCP peer, not an X-Forwarded-For header, for
    the same reason rate_limit uses it: a header is trivially forged, and an
    audit trail recording an attacker's chosen value is worse than one recording
    nothing. Behind the bundled proxy this is the proxy's address; a deployment
    that needs the true client here should configure uvicorn's --proxy-headers
    with an explicit trusted-hosts list rather than trusting the header blindly.
    """
    if request is None or request.client is None:
        return None
    return request.client.host


def record(db: Session, *, activity_type: ActivityType, subject: User | None = None,
           actor: User | None = None, description: str | None = None,
           request: Request | None = None, context: dict | None = None,
           subject_email: str | None = None, commit: bool = True) -> ActivityLog | None:
    """Append one entry. Returns the row, or None if it could not be written.

    `subject_email` exists for events where there is no User to point at -- a
    failed login against an address that does not exist, most importantly. Those
    are exactly the entries worth keeping, so the trail must not depend on the
    account being real.

    `context` must contain nothing secret; it is rendered to the admin as-is.
    """
    try:
        entry = ActivityLog(
            subject_user_id=subject.id if subject else None,
            actor_user_id=actor.id if actor else (subject.id if subject else None),
            activity_type=activity_type,
            description=(description or ACTIVITY_LABELS.get(activity_type.value))[:255],
            subject_email=(subject.email if subject else subject_email),
            actor_email=(actor.email if actor else (subject.email if subject else None)),
            ip_address=client_ip(request),
            request_id=getattr(getattr(request, "state", None), "request_id", None),
            context_json=json.dumps(context) if context else None,
        )
        db.add(entry)
        if commit:
            db.commit()
            db.refresh(entry)
        else:
            db.flush()
        return entry
    except Exception:
        # Never let auditing break the thing it is auditing. Rolled back so a
        # failed insert cannot leave the caller's session in a broken state and
        # take their own commit down with it.
        try:
            db.rollback()
        except Exception:
            pass
        logger.exception("Could not record activity %s", getattr(activity_type, "value", activity_type))
        return None


def list_for_user(db: Session, user_id: int, *, limit: int = 100) -> list[dict]:
    """One account's timeline, newest first, shaped for the admin UI.

    Serialised here rather than through a Pydantic response model because the
    label and notable flag are presentation concerns derived from the type --
    computing them in the frontend would mean duplicating ACTIVITY_LABELS in
    JavaScript and letting the two drift.
    """
    rows = (
        db.query(ActivityLog)
        .filter(ActivityLog.subject_user_id == user_id)
        .order_by(ActivityLog.created_at.desc(), ActivityLog.id.desc())
        .limit(limit)
        .all()
    )
    return [_serialize(row) for row in rows]


def list_recent(db: Session, *, limit: int = 200, activity_type: str | None = None) -> list[dict]:
    """Platform-wide feed, for the admin's overview."""
    query = db.query(ActivityLog)
    if activity_type:
        query = query.filter(ActivityLog.activity_type == activity_type)
    rows = query.order_by(ActivityLog.created_at.desc(), ActivityLog.id.desc()).limit(limit).all()
    return [_serialize(row) for row in rows]


def _serialize(row: ActivityLog) -> dict:
    context = None
    if row.context_json:
        try:
            context = json.loads(row.context_json)
        except ValueError:
            context = None

    actor_email = row.actor_email
    subject_email = row.subject_email
    return {
        "id": row.id,
        "type": row.activity_type.value if hasattr(row.activity_type, "value") else row.activity_type,
        "label": ACTIVITY_LABELS.get(
            row.activity_type.value if hasattr(row.activity_type, "value") else row.activity_type,
            "Activity",
        ),
        "description": row.description,
        # True when someone acted on this account other than its owner, which is
        # the distinction an admin scanning the list actually cares about.
        "by_someone_else": bool(actor_email and subject_email and actor_email != subject_email),
        "actor_email": actor_email,
        "subject_email": subject_email,
        "ip_address": row.ip_address,
        "request_id": row.request_id,
        "context": context,
        "notable": (row.activity_type.value if hasattr(row.activity_type, "value") else row.activity_type)
                   in NOTABLE_ACTIVITY,
        "created_at": row.created_at,
    }


def purge_older_than(db: Session, *, days: int) -> int:
    """Housekeeping for a table that only grows.

    Not called from the request path and not scheduled by default: how long an
    institution must keep an audit trail is a compliance question with a
    different answer per deployment, and a library-chosen default that quietly
    destroyed evidence would be the wrong kind of helpful.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    deleted = db.query(ActivityLog).filter(ActivityLog.created_at < cutoff).delete(synchronize_session=False)
    db.commit()
    return deleted
