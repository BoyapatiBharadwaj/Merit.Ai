"""One-time passcode issuance and verification."""
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
from app.services import email_service, otp_redis_store

logger = logging.getLogger("app")

# The two purposes whose live state is Redis, not Postgres. ACTIVATION is
# everything else request_code/verify_code handle.
_REDIS_BACKED_PURPOSES = (OtpPurpose.SIGNUP, OtpPurpose.PASSWORD_RESET)

_PURPOSE_LABELS = {
    OtpPurpose.SIGNUP: "verify your email address",
    OtpPurpose.PASSWORD_RESET: "reset your Merit.Ai password",
    OtpPurpose.ACTIVATION: "activate your Merit.Ai account",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime | None) -> datetime | None:
    """SQLite hands back naive datetimes even for timezone=True columns, so a direct comparison
    against an aware `_now()` raises TypeError.
    """
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _digest(code: str) -> str:
    return hmac.new(settings.SECRET_KEY.encode(), code.encode(), hashlib.sha256).hexdigest()


def _generate_code() -> str:
    """A uniformly random decimal code of OTP_LENGTH digits, leading zeros kept."""
    upper = 10 ** settings.OTP_LENGTH
    return str(secrets.randbelow(upper)).zfill(settings.OTP_LENGTH)


def _generate_token() -> str:
    """A 256-bit URL-safe token for an activation link."""
    return secrets.token_urlsafe(32)


def issue_activation_token(db: Session, *, email: str) -> tuple[str, datetime]:
    """Mint an activation token and return it with its expiry."""
    email = email.strip().lower()
    otp_repository.consume_outstanding(db, email=email, purpose=OtpPurpose.ACTIVATION, when=_now())

    token = _generate_token()
    expires_at = _now() + timedelta(hours=settings.ACTIVATION_TTL_HOURS)
    otp_repository.create(
        db,
        email=email,
        purpose=OtpPurpose.ACTIVATION,
        code_hash=_digest(token),
        expires_at=expires_at,
    )
    return token, expires_at


def activation_link(token: str, email: str) -> str:
    """The URL that lands on the activation screen."""
    from urllib.parse import quote

    base = settings.APP_BASE_URL.rstrip("/")
    return f"{base}/activate?token={quote(token)}&email={quote(email)}"


def _seconds_since(value: datetime | None) -> float:
    aware = _as_utc(value)
    return float("inf") if aware is None else (_now() - aware).total_seconds()


def request_code(db: Session, *, email: str, purpose: OtpPurpose,
                 background: BackgroundTasks | None = None) -> None:
    """Issue and email a fresh code. Always succeeds from the caller's view, for SIGNUP and PASSWORD_RESET."""
    if purpose not in _REDIS_BACKED_PURPOSES:
        raise ValueError(f"request_code is for SIGNUP/PASSWORD_RESET only; got {purpose!r}. "
                         "Activation tokens are issued by issue_activation_token.")

    email = email.strip().lower()

    wait = otp_redis_store.cooldown_remaining_seconds(purpose, email)
    if wait is not None:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            f"A code was just sent. Please wait {max(wait, 1)} more seconds before requesting another.",
        )

    code = _generate_code()
    otp_redis_store.issue(
        purpose, email,
        code_hash=_digest(code),
        ttl_seconds=settings.OTP_TTL_MINUTES * 60,
        cooldown_seconds=settings.OTP_RESEND_COOLDOWN_SECONDS,
    )

    subject, text, html = email_service.otp_message(
        code=code,
        purpose_label=_PURPOSE_LABELS[purpose],
        ttl_minutes=settings.OTP_TTL_MINUTES,
    )
    email_service.enqueue_tracked(db, to=email, subject=subject, text_body=text, html_body=html)

    if not email_service.is_enabled():
        # Without this the code is unrecoverable on a dev box with no SMTP set up, and every
        # email-gated flow becomes untestable by hand.
        logger.warning("Email is disabled; OTP for %s (%s) is %s", email, purpose.value, code)


def verify_code(db: Session, *, email: str, purpose: OtpPurpose, code: str,
                consume: bool = True, commit: bool = True) -> OtpCode | None:
    """Check a submitted code. Raises 400 (or 429 on an exhausted attempt budget) on any failure."""
    email = email.strip().lower()
    if purpose in _REDIS_BACKED_PURPOSES:
        _verify_code_redis(email=email, purpose=purpose, code=code)
        return None
    return _verify_code_postgres(db, email=email, purpose=purpose, code=code, consume=consume, commit=commit)


def _verify_code_redis(*, email: str, purpose: OtpPurpose, code: str) -> None:
    """SIGNUP / PASSWORD_RESET verification against otp_redis_store."""
    generic = "That code is invalid or has expired. Request a new one."
    result = otp_redis_store.verify(
        purpose, email, code_hash=_digest(code.strip()), max_attempts=settings.OTP_MAX_ATTEMPTS,
    )
    if result == "ok":
        return
    if result == "locked":
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Too many incorrect attempts for this code. Request a new one.",
        )
    raise HTTPException(status.HTTP_400_BAD_REQUEST, generic)


def _verify_code_postgres(db: Session, *, email: str, purpose: OtpPurpose, code: str,
                          consume: bool, commit: bool) -> OtpCode:
    """ACTIVATION verification against the otp_codes table -- the original implementation,
    unchanged, for the one purpose that still lives here.
    """
    generic = "That code is invalid or has expired. Request a new one."

    # Locked when the caller is going to hold the transaction open: two requests
    # racing with the same valid code must not both see it unconsumed.
    row = otp_repository.get_latest(db, email=email, purpose=purpose, lock=not commit)
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

    # compare_digest, not ==. String equality short-circuits on the first differing byte, so its
    # runtime leaks how many leading characters were correct.
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
        otp_repository.mark_consumed(db, row, when=_now(), commit=commit)
    return row


def purge_expired(db: Session) -> int:
    """Delete codes that expired more than a day ago."""
    return otp_repository.delete_expired(db, before=_now() - timedelta(days=1))
