"""
One-time passcode issuance and verification.

Two different backends for two different lifetimes, split by purpose:

  SIGNUP, PASSWORD_RESET   A typed, six-digit code living minutes. State
                           (hash, expiry, attempt count, resend cooldown) is
                           entirely in Redis (app/services/otp_redis_store.py)
                           -- genuinely transient data with no reason to be
                           permanent, and TTL-based expiry there needs no
                           sweep the way a Postgres row would.
  ACTIVATION               A clicked, 256-bit link token living days, minted
                           by an admin action rather than requested by the
                           account's own owner. Stays in the `otp_codes`
                           Postgres table via otp_repository, unchanged --
                           this rewrite's Redis-namespacing brief names OTP
                           codes specifically, not activation links, and nothing
                           about an activation link benefits from moving.

The rules a code must satisfy to be accepted are the same either way, all
enforced below rather than assumed:

  1. It is the (one) live code issued for that (email, purpose).
  2. It has not expired (OTP_TTL_MINUTES / ACTIVATION_TTL_HOURS).
  3. It has not already been consumed.
  4. Its attempt budget (OTP_MAX_ATTEMPTS) is not exhausted.
  5. The digest matches, compared in constant time.

Two design decisions worth stating explicitly, because both look like
over-engineering until the thing they prevent happens:

**Codes are stored as HMAC-SHA256 digests, never plaintext.** A one-time
passcode is a credential. Storing it in the clear means a read-only leak of
wherever it lives -- a stray Postgres backup, an unauthenticated Redis
instance, a log of a query -- hands over the ability to complete a password
reset for every address with a live code. Hashing costs nothing and removes
that entirely. HMAC keyed with SECRET_KEY rather than a bare SHA-256 because
the input space is a million six-digit numbers: a bare digest of that is
trivially rainbow-tabled, whereas an HMAC cannot be precomputed without the key.

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


def _generate_token() -> str:
    """A 256-bit URL-safe token for an activation link.

    Deliberately not the six-digit code above. A code is typed, so it has to be
    short, and shortness is survivable only because the code expires in minutes
    and burns after five wrong guesses. An activation link lives for days and is
    clicked rather than typed, so the same defences do not apply and the secret
    has to carry its own weight -- 43 characters of base64url from
    `secrets.token_urlsafe` is not guessable at any rate the network permits.
    """
    return secrets.token_urlsafe(32)


def issue_activation_token(db: Session, *, email: str) -> tuple[str, datetime]:
    """Mint an activation token and return it with its expiry.

    Returns the token instead of mailing it because the caller (an approval, an
    invitation, an admin re-send) writes a different message around the same
    link, and because the token must be handed over strictly after the caller's
    own transaction commits -- an email cannot be rolled back.

    Any earlier live activation token for this address is invalidated. Otherwise
    re-sending an invitation would leave two working links in two inboxes, and
    revoking access would mean hunting down every one ever issued.
    """
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
    """The URL that lands on the activation screen.

    The address rides along so the screen can show whose account is being
    activated without asking the person to retype it -- and so the server has
    something to look the token up by. It is not a secret and grants nothing on
    its own; the token is what authorises.
    """
    from urllib.parse import quote

    base = settings.APP_BASE_URL.rstrip("/")
    return f"{base}/activate?token={quote(token)}&email={quote(email)}"


def _seconds_since(value: datetime | None) -> float:
    aware = _as_utc(value)
    return float("inf") if aware is None else (_now() - aware).total_seconds()


def request_code(db: Session, *, email: str, purpose: OtpPurpose,
                 background: BackgroundTasks | None = None) -> None:
    """Issue and email a fresh code. Always succeeds from the caller's view,
    for SIGNUP and PASSWORD_RESET -- the only two purposes this issues codes
    for (ACTIVATION tokens are minted by issue_activation_token instead).

    The only condition that surfaces as an error is the resend cooldown, and
    that is deliberately about the *address being mailed*, not about whether an
    account exists -- so it leaks nothing. Everything else (unknown address,
    delivery failure) is silent by design.

    `background` is accepted for call-site compatibility but no longer used
    for the send itself: the message goes through email_service.enqueue_tracked
    now (Redis+RQ, with a permanent Postgres delivery record), the same queue
    every other transactional email in this app uses -- see that function's
    docstring for why a background task is not the right tool for a message
    whose delivery outcome must be knowable.
    """
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
        # Without this the code is unrecoverable on a dev box with no SMTP set
        # up, and every email-gated flow becomes untestable by hand. Gated on
        # is_enabled() precisely so it can never fire on a deployment that does
        # send mail -- a passcode in a production log is a real credential leak.
        logger.warning("Email is disabled; OTP for %s (%s) is %s", email, purpose.value, code)


def verify_code(db: Session, *, email: str, purpose: OtpPurpose, code: str,
                consume: bool = True, commit: bool = True) -> OtpCode | None:
    """Check a submitted code. Raises 400 (or 429 on an exhausted attempt
    budget) on any failure. Dispatches on purpose to whichever backend that
    purpose's code actually lives in -- see this module's docstring.

    Every rejection uses the same generic message. Distinguishing "expired" from
    "wrong" from "already used" tells an attacker which of their guesses landed
    on a real, live code -- and tells a legitimate user nothing they can act on
    that "request a new code" doesn't already cover.
    """
    email = email.strip().lower()
    if purpose in _REDIS_BACKED_PURPOSES:
        _verify_code_redis(email=email, purpose=purpose, code=code)
        return None
    return _verify_code_postgres(db, email=email, purpose=purpose, code=code, consume=consume, commit=commit)


def _verify_code_redis(*, email: str, purpose: OtpPurpose, code: str) -> None:
    """SIGNUP / PASSWORD_RESET verification against otp_redis_store.

    Always consumes on success -- Redis has no notion of an outer SQL
    transaction to defer into, and this rewrite's brief is explicit that OTP
    data must be deleted immediately once verification succeeds. See
    otp_redis_store's module docstring for the one behavioural change that
    follows from that (a failed signup after a correct code cannot reuse it).
    """
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
    """ACTIVATION verification against the otp_codes table -- the original
    implementation, unchanged, for the one purpose that still lives here.

    `commit=False` marks the code consumed without committing, so the caller can
    finish the work the code authorised in the same transaction.
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
        otp_repository.mark_consumed(db, row, when=_now(), commit=commit)
    return row


def purge_expired(db: Session) -> int:
    """Delete codes that expired more than a day ago.

    A day rather than immediately: an expired-but-recent row is what makes the
    resend cooldown and the "already used" rejection work correctly right after
    a code lapses. Only rows old enough that no live flow can reference them
    are removed.
    """
    return otp_repository.delete_expired(db, before=_now() - timedelta(days=1))
