"""
Password hashing and JWT token utilities.
"""
from datetime import datetime, timedelta, timezone
from typing import Any
import jwt
from jwt import PyJWTError
from passlib.context import CryptContext

from app.core.config import settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


# Claim value marking a token that may only be used inside one exam attempt.
# Checked by app/api/deps.py, which refuses these anywhere else.
ATTEMPT_SCOPE = "attempt"

# Claim carrying the account's password epoch (see User.password_changed_at).
# deps.get_current_user rejects any token whose epoch is older than the one
# stored on the user, which is what makes a password change revoke sessions.
PASSWORD_EPOCH_CLAIM = "pwd"


def password_epoch(user) -> int:
    """The account's password epoch, in whole microseconds."""
    changed_at = getattr(user, "password_changed_at", None)
    if changed_at is None:
        return 0
    if changed_at.tzinfo is None:
        changed_at = changed_at.replace(tzinfo=timezone.utc)
    return int(changed_at.timestamp() * 1_000_000)


def create_access_token(subject: str, role: str, extra_claims: dict | None = None,
                        user=None) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode: dict[str, Any] = {"sub": subject, "role": role, "exp": expire}
    if user is not None:
        to_encode[PASSWORD_EPOCH_CLAIM] = password_epoch(user)
    if extra_claims:
        to_encode.update(extra_claims)
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def create_attempt_token(subject: str, role: str, attempt_id: int, expires_at: datetime,
                         user=None) -> str:
    """A token that lives as long as one exam attempt, and is useless elsewhere."""
    to_encode: dict[str, Any] = {
        "sub": subject, "role": role, "scope": ATTEMPT_SCOPE,
        "attempt_id": attempt_id, "exp": expires_at,
    }
    if user is not None:
        to_encode[PASSWORD_EPOCH_CLAIM] = password_epoch(user)
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def decode_access_token(token: str) -> dict | None:
    """Verify and decode, or None."""
    try:
        return jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
    except PyJWTError:
        return None
