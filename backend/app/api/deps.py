"""
Reusable FastAPI dependencies: DB session shortcut, current-user resolver,
and role-based access guards.
"""
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.core import rate_limit
from app.core.config import settings
from app.database.session import get_db
from app.core.security import ATTEMPT_SCOPE, PASSWORD_EPOCH_CLAIM, decode_access_token, password_epoch
from app.repositories import user_repository
from app.models.user import User
from app.models.enums import RoleName

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")


def _user_from_payload(payload: dict, db: Session) -> User:
    # `sub` is validated rather than trusted -- see get_current_user.
    try:
        user_id = int(payload.get("sub"))
    except (TypeError, ValueError):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token.",
                            headers={"WWW-Authenticate": "Bearer"})
    user = user_repository.get_user_by_id(db, user_id)
    if not user or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User not found or inactive.")
    _reject_if_password_changed(payload, user)
    return user


def _reject_if_password_changed(payload: dict, user: User) -> None:
    """Refuse a token minted before this account's password last changed.

    What this makes true, and was not before: changing a password ends every
    other session. A stolen token used to keep working for up to two hours after
    the owner did the exact thing everyone is told to do when they suspect a
    compromise -- which made "reset your password" advice that did not
    accomplish what the person believed it accomplished.

    A token with no epoch claim is treated as issued at the beginning of time,
    so it survives only until the first password change. That is what lets this
    deploy without signing out everyone holding a token from the previous build,
    while still revoking those tokens the moment there is a reason to.
    """
    current = password_epoch(user)
    if current == 0:
        return
    if int(payload.get(PASSWORD_EPOCH_CLAIM, 0) or 0) < current:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Your password was changed, so this session has ended. Please sign in again.",
            headers={"WWW-Authenticate": "Bearer"},
        )


def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> User:
    payload = decode_access_token(token)
    if not payload:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token.",
                             headers={"WWW-Authenticate": "Bearer"})
    # An attempt-scoped token is long-lived precisely because it can do almost
    # nothing (see security.create_attempt_token). Accepting it here would undo
    # that in one line: it would become an ordinary session token valid for the
    # length of an exam, which is the thing it exists to avoid.
    if payload.get("scope") == ATTEMPT_SCOPE:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED,
                            "This token is only valid inside the exam it was issued for.",
                            headers={"WWW-Authenticate": "Bearer"})
    # `sub` validation, the account lookup and the password-epoch check all live
    # in _user_from_payload, so every entry point gets identical treatment. They
    # were briefly duplicated here, and the copy silently skipped the epoch
    # check -- meaning the session-revocation fix was in place and doing nothing
    # on the one dependency almost every endpoint uses.
    return _user_from_payload(payload, db)


def attempt_student(attempt_id: int, token: str = Depends(oauth2_scheme),
                    db: Session = Depends(get_db)) -> User:
    """The candidate, authenticated by EITHER their session or an attempt token.

    Used by the endpoints a live exam calls. `attempt_id` is taken from the path
    (FastAPI resolves it from the same route parameter the endpoint declares),
    so the comparison below is against the attempt actually being acted on
    rather than one the token could name for itself.

    A normal session token still works and is checked exactly as before -- this
    widens what is accepted, it does not weaken what is required of it.
    """
    payload = decode_access_token(token)
    if not payload:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token.",
                            headers={"WWW-Authenticate": "Bearer"})

    if payload.get("scope") == ATTEMPT_SCOPE:
        if payload.get("attempt_id") != attempt_id:
            # Deliberately the same 401 an expired token gets, and deliberately
            # not "wrong attempt": a candidate holding a valid token should not
            # be able to probe which attempt ids exist by the wording.
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token.",
                                headers={"WWW-Authenticate": "Bearer"})
        user = _user_from_payload(payload, db)
    else:
        user = _user_from_payload(payload, db)

    if user.role.name != RoleName.STUDENT.value:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You do not have access to this resource.")
    return user


def exam_student(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> User:
    """Like attempt_student, for in-exam endpoints that do NOT name an attempt.

    Face verification, object detection and pose checks take only an image, so
    there is no attempt id in the path to pin the token to. They still have to
    keep working three hours into a three-hour exam, which they could not if
    they insisted on a two-hour session token.

    What this gives up, precisely: an attempt token can call these three
    endpoints (and the event/strike loggers below) for its own user. That is a
    narrow widening -- each one accepts and returns only the caller's own data,
    each is rate limited per user, and the endpoints that DO name an attempt
    still verify the caller owns it inside the handler. The valuable half of the
    scoping, that the token cannot read the roster or submit somebody else's
    exam, is untouched.
    """
    payload = decode_access_token(token)
    if not payload:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token.",
                            headers={"WWW-Authenticate": "Bearer"})
    user = _user_from_payload(payload, db)
    if user.role.name != RoleName.STUDENT.value:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You do not have access to this resource.")
    return user


def require_role(*allowed_roles: str):
    def _checker(user: User = Depends(get_current_user)) -> User:
        if user.role.name not in allowed_roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "You do not have access to this resource.")
        return user
    return _checker


def rate_limit_user(bucket: str, max_requests: int | None = None, window_seconds: int | None = None):
    """Per-user rate limit for authenticated endpoints.

    Lives here rather than in app/core/rate_limit.py because it needs
    `get_current_user`, and core must not import from the api layer. The
    counting itself is core's `consume` -- this only supplies a different key.

    `bucket` namespaces the counter per endpoint, so a student polling
    /objects/detect during an exam cannot exhaust the budget that
    /face/verify needs a moment later. Sharing one bucket across all four
    proctoring endpoints would mean their individually-safe polling rates add
    up to a limit none of them alone would ever hit.
    """
    limit = max_requests if max_requests is not None else settings.AI_RATE_LIMIT_MAX_REQUESTS
    window = window_seconds if window_seconds is not None else settings.AI_RATE_LIMIT_WINDOW_SECONDS

    # Resolves the caller through exam_student, not get_current_user: these
    # buckets guard the in-exam proctoring endpoints, and get_current_user
    # refuses attempt-scoped tokens -- so keeping it here would have made the
    # rate limiter itself the thing that 401s a candidate two hours into a
    # three-hour exam, with the endpoint it guards perfectly willing to serve
    # them.
    def _checker(user: User = Depends(exam_student)) -> None:
        rate_limit.consume(
            bucket, str(user.id), limit=limit, window=window,
            message=("You're sending proctoring checks faster than expected. "
                     "Pause for a moment -- your exam is not affected."),
        )

    return _checker


require_admin = require_role(RoleName.ADMIN.value)
require_examiner = require_role(RoleName.EXAMINER.value)
require_student = require_role(RoleName.STUDENT.value)
require_admin_or_examiner = require_role(RoleName.ADMIN.value, RoleName.EXAMINER.value)
