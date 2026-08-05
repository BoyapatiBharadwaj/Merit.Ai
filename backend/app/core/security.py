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
    """The account's password epoch, in whole microseconds.

    Microseconds, not seconds. Second granularity looks tidier and is wrong: a
    token issued in the SAME second as the password change compares equal, `<`
    is false, and it survives -- which is precisely the scenario the check
    exists for, an attacker holding a live token while the owner resets.

    Both sides are computed from the same stored value by this same function, so
    a token minted after the change is exactly equal (accepted) and one minted
    before is strictly smaller (rejected). A database that truncated the column
    would make the stored value smaller than the token's, which fails open
    rather than signing out a legitimate user -- the right direction for a
    rounding error to go.
    """
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
    """A token that lives as long as one exam attempt, and is useless elsewhere.

    ACCESS_TOKEN_EXPIRE_MINUTES is 120 and an exam may run to 480, so a normal
    session token can expire while a candidate is still writing. The first 401
    then cleared their session and dropped them at the login page mid-exam --
    the failure mode is the whole attempt, not an inconvenience.

    Raising every login token to eight hours would have fixed that by making
    every stolen token last eight hours, including an administrator's on a
    shared examination-hall machine. This token is the opposite trade: long
    lived but narrow. `scope` and `attempt_id` are what make it narrow --
    deps.get_current_user refuses any token carrying this scope, so it opens
    nothing outside the attempt endpoints, and those check the attempt id in the
    path against the one in the token. Presenting it for somebody else's attempt
    fails exactly like presenting no token at all.

    Deliberately NOT a refresh token: nothing renews it. Its life is bounded by
    a deadline the server already knows and the candidate cannot move.
    """
    to_encode: dict[str, Any] = {
        "sub": subject, "role": role, "scope": ATTEMPT_SCOPE,
        "attempt_id": attempt_id, "exp": expires_at,
    }
    if user is not None:
        to_encode[PASSWORD_EPOCH_CLAIM] = password_epoch(user)
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def decode_access_token(token: str) -> dict | None:
    """Verify and decode, or None.

    PyJWT, not python-jose. The pinned python-jose 3.3.0 carries CVE-2024-33664
    -- a JWE decompression bomb reachable from exactly this call on an
    attacker-supplied bearer token -- and the project is barely maintained. The
    algorithm allow-list is what closes the other classic hole: without
    `algorithms=[...]` a token claiming `alg: none` would be accepted as valid.
    """
    try:
        return jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
    except PyJWTError:
        return None
