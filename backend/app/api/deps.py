"""Reusable FastAPI dependencies: DB session shortcut, current-user resolver, and role-based access guards."""
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
    """Refuse a token minted before this account's password last changed."""
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
    # An attempt-scoped token is long-lived precisely because it can do almost nothing (see
    # security.create_attempt_token).
    if payload.get("scope") == ATTEMPT_SCOPE:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED,
                            "This token is only valid inside the exam it was issued for.",
                            headers={"WWW-Authenticate": "Bearer"})
    # `sub` validation, the account lookup and the password-epoch check all live in
    # _user_from_payload, so every entry point gets identical treatment.
    return _user_from_payload(payload, db)


def attempt_student(attempt_id: int, token: str = Depends(oauth2_scheme),
                    db: Session = Depends(get_db)) -> User:
    """The candidate, authenticated by EITHER their session or an attempt token."""
    payload = decode_access_token(token)
    if not payload:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token.",
                            headers={"WWW-Authenticate": "Bearer"})

    if payload.get("scope") == ATTEMPT_SCOPE:
        if payload.get("attempt_id") != attempt_id:
            # Deliberately the same 401 an expired token
            # gets, and deliberately not "wrong attempt".
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token.",
                                headers={"WWW-Authenticate": "Bearer"})
        user = _user_from_payload(payload, db)
    else:
        user = _user_from_payload(payload, db)

    if user.role.name != RoleName.STUDENT.value:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You do not have access to this resource.")
    return user


def exam_student(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> User:
    """Like attempt_student, for in-exam endpoints that do NOT name an attempt."""
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
    """Per-user rate limit for authenticated endpoints."""
    limit = max_requests if max_requests is not None else settings.AI_RATE_LIMIT_MAX_REQUESTS
    window = window_seconds if window_seconds is not None else settings.AI_RATE_LIMIT_WINDOW_SECONDS

    # Resolves the caller through exam_student, not get_current_user.
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
