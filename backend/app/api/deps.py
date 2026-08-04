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
from app.core.security import decode_access_token
from app.repositories import user_repository
from app.models.user import User
from app.models.enums import RoleName

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")


def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> User:
    payload = decode_access_token(token)
    if not payload:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token.",
                             headers={"WWW-Authenticate": "Bearer"})
    # `sub` is validated rather than trusted. It used to be read as
    # `int(payload["sub"])` directly, so a token that was correctly signed but
    # malformed -- missing `sub`, or carrying a non-numeric one -- raised
    # KeyError/ValueError out of an auth dependency. That surfaced to the
    # caller as a 500 (via the catch-all handler in main.py) instead of a 401,
    # which is both wrong for the client and noise in the error logs: an
    # unauthenticated request is a normal event, not an application fault.
    subject = payload.get("sub")
    try:
        user_id = int(subject)
    except (TypeError, ValueError):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token.",
                             headers={"WWW-Authenticate": "Bearer"})
    user = user_repository.get_user_by_id(db, user_id)
    if not user or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User not found or inactive.")
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

    def _checker(user: User = Depends(get_current_user)) -> None:
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
