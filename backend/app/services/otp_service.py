"""
One-time passcode issuance and verification.

Policy lives here; storage lives in otp_repository. The rules a code must
satisfy to be accepted, all of which are enforced below rather than assumed:

  1. It is the newest code issued for that (email, purpose).
  2. It has not expired (OTP_TTL_MINUTES).
  3. It has not already been consumed.
  4. Its attempt budget (OTP_MAX_ATTEMPTS) is not exhausted.
  5. The digest matches, compared in constant time.

Two design decisions worth stating explicitly, because both look like
over-engineering until the thing they prevent happens:

**Codes are stored as HMAC-SHA256 digests, never plaintext.** A one-time
passcode is a credential. Storing it in the clear means a read-only leak of the
database -- a stray backup, a log of a query, an over-permissive analytics
connection -- hands over the ability to complete a password reset for every
address with a live code. Hashing costs nothing and removes that entirely.
HMAC keyed with SECRET_KEY rather than a bare SHA-256 because the input space is
a million six-digit numbers: a bare digest of that is trivially rainbow-tabled,
whereas an HMAC cannot be precomputed without the key.

**Requesting a code never reveals whether the account exists.** `request_code`
returns the same response, with the same timing characteristics, for a
registered and an unregistered address. Password-reset forms are the classic
account-enumeration oracle, and this application already takes the same care in
access_request_service.submit -- being careful in one place and careless in
another just moves the oracle.
"""
import hashlib
import hmac
import logging
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import BackgroundTasks, HTTPException, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.otp import OtpCode, OtpPurpose
from app.repositories import otp_repository
from app.services import email_service

logger = logging.getLogger("app")

_PURPOSE_LABELS = {
    OtpPurpose.SIGNUP: "verify your email address",
    OtpPurpose.PASSWORD_RESET: "reset your Merit.Ai password",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime | None) -> datetime | None:
    """SQLite hands back naive datetimes even for timezone=True columns, so a
    direct comparison against an aware `_now()` raises TypeError. Postgres
    returns aware ones and this is a no-op there. The same normalisation
    attempt_service._as_utc already does, for the same reason."""
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _digest(code: str) -> str:
    return hmac.new(settings.SECRET_KEY.encode(), code.encode(), hashlib.sha256).hexdigest()


def _generate_code() -> str:
    """A uniformly random decimal code of OTP_LENGTH digits, leading zeros kept.

    `secrets.randbelow` rather than `random`: this is a credential, and the
    `random` module is a Mersenne Twister whose future output is predictable
    from enough past output. Zero-padding matters too -- stripping a leading
    zero would both shrink the keyspace and produce a code that doesn't match
    what the user is asked to type.
    """
    upper = 10 ** settings.OTP_LENGTH
    return str(secrets.randbelow(upper)).zfill(settings.OTP_LENGTH)


def _seconds_since(value: datetime | None) -> float:
    aware = _as_utc(value)
    return float("inf") if aware is None else (_now() - aware).total_seconds()


def request_code(db: Session, *, email: str, purpose: OtpPurpose,
                 background: BackgroundTasks | None = None) -> None:
    """Issue and email a fresh code. Always succeeds from the caller's view.

    The only condition that surfaces as an error is the resend cooldown, and
    that is deliberately about the *address being mailed*, not about whether an
    account exists -- so it leaks nothing. Everything else (unknown address,
    delivery failure) is silent by design.
    """
    email = email.strip().lower()

    previous = otp_repository.get_latest(db, email=email, purpose=purpose)
    if previous is not None and _seconds_since(previous.created_at) < settings.OTP_RESEND_COOLDOWN_SECONDS:
        wait = int(settings.OTP_RESEND_COOLDOWN_SECONDS - _seconds_since(previous.created_at))
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            f"A code was just sent. Please wait {max(wait, 1)} more seconds before requesting another.",
        )

    code = _generate_code()
    otp_repository.create(
        db,
        email=email,
        purpose=purpose,
        code_hash=_digest(code),
        expires_at=_now() + timedelta(minutes=settings.OTP_TTL_MINUTES),
    )

    subject, text, html = email_service.otp_message(
        code=code,
        purpose_label=_PURPOSE_LABELS[purpose],
        ttl_minutes=settings.OTP_TTL_MINUTES,
    )
    email_service.queue(background, to=email, subject=subject, text_body=text, html_body=html)

    if not email_service.is_enabled():
        # Without this the code is unrecoverable on a dev box with no SMTP set
        # up, and every email-gated flow becomes untestable by hand. Gated on
        # is_enabled() precisely so it can never fire on a deployment that does
        # send mail -- a passcode in a production log is a real credential leak.
        logger.warning("Email is disabled; OTP for %s (%s) is %s", email, purpose.value, code)


def verify_code(db: Session, *, email: str, purpose: OtpPurpose, code: str,
                consume: bool = True) -> OtpCode:
    """Check a submitted code. Raises 400 on any failure; returns the row on success.

    Every rejection uses the same generic message. Distinguishing "expired" from
    "wrong" from "already used" tells an attacker which of their guesses landed
    on a real, live code -- and tells a legitimate user nothing they can act on
    that "request a new code" doesn't already cover.
    """
    email = email.strip().lower()
    generic = "That code is invalid or has expired. Request a new one."

    row = otp_repository.get_latest(db, email=email, purpose=purpose)
    if row is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, generic)

    if row.consumed_at is not None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, generic)

    expires_at = _as_utc(row.expires_at)
    if expires_at is None or expires_at <= _now():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, generic)

    if (row.attempts or 0) >= settings.OTP_MAX_ATTEMPTS:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Too many incorrect attempts for this code. Request a new one.",
        )

    # compare_digest, not ==. String equality short-circuits on the first
    # differing byte, so its runtime leaks how many leading characters were
    # correct; over enough requests that is enough to reconstruct the digest a
    # byte at a time. Both sides are fixed-length hex here, so this is a
    # genuinely constant-time comparison.
    if not hmac.compare_digest(row.code_hash, _digest(code.strip())):
        otp_repository.record_attempt(db, row)
        remaining = max(settings.OTP_MAX_ATTEMPTS - (row.attempts or 0), 0)
        if remaining == 0:
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                "Too many incorrect attempts for this code. Request a new one.",
            )
        raise HTTPException(status.HTTP_400_BAD_REQUEST, generic)

    if consume:
        otp_repository.mark_consumed(db, row, when=_now())
    return row


def purge_expired(db: Session) -> int:
    """Delete codes that expired more than a day ago.

    A day rather than immediately: an expired-but-recent row is what makes the
    resend cooldown and the "already used" rejection work correctly right after
    a code lapses. Only rows old enough that no live flow can reference them
    are removed.
    """
    return otp_repository.delete_expired(db, before=_now() - timedelta(days=1))
