"""Temporary one-time-passcode state, in Redis, for the two purposes that are genuinely
transient: SIGNUP and PASSWORD_RESET codes.
"""
import hmac
import logging

from app.core.redis_client import get_client, translate_errors
from app.models.otp import OtpPurpose

logger = logging.getLogger("app")


def _code_key(purpose: OtpPurpose, email: str) -> str:
    return f"otp:{purpose.value}:{email}"


def _attempts_key(purpose: OtpPurpose, email: str) -> str:
    return f"otp:attempts:{purpose.value}:{email}"


def _cooldown_key(purpose: OtpPurpose, email: str) -> str:
    return f"otp:cooldown:{purpose.value}:{email}"


def cooldown_remaining_seconds(purpose: OtpPurpose, email: str) -> int | None:
    """Seconds left before another code may be requested, or None if the
    cooldown has elapsed (or no code was ever requested)."""
    with translate_errors("check the resend cooldown"):
        ttl = get_client().ttl(_cooldown_key(purpose, email))
    return ttl if ttl and ttl > 0 else None


def issue(purpose: OtpPurpose, email: str, *, code_hash: str, ttl_seconds: int, cooldown_seconds: int) -> None:
    """Store a freshly issued code's hash, reset its attempt budget, and start a new resend cooldown."""
    with translate_errors("issue a one-time code"):
        pipe = get_client().pipeline(transaction=True)
        pipe.set(_code_key(purpose, email), code_hash, ex=max(int(ttl_seconds), 1))
        pipe.delete(_attempts_key(purpose, email))
        if cooldown_seconds > 0:
            pipe.set(_cooldown_key(purpose, email), "1", ex=int(cooldown_seconds))
        else:
            pipe.delete(_cooldown_key(purpose, email))
        pipe.execute()


def verify(purpose: OtpPurpose, email: str, *, code_hash: str, max_attempts: int) -> str:
    """Check one guess against the live code for (purpose, email)."""
    code_key = _code_key(purpose, email)
    attempts_key = _attempts_key(purpose, email)

    with translate_errors("verify a one-time code"):
        client = get_client()
        stored = client.get(code_key)
        if stored is None:
            return "invalid"

        current_attempts = int(client.get(attempts_key) or 0)
        if current_attempts >= max_attempts:
            return "locked"

        # compare_digest, not ==, for the same constant-time
        # reason otp_service always compared this way.
        if hmac.compare_digest(stored, code_hash):
            client.delete(code_key)
            client.delete(attempts_key)
            return "ok"

        new_attempts = client.incr(attempts_key)
        if new_attempts == 1:
            # First wrong guess against this code: give the counter the same
            # remaining lifetime as the code, so it can never outlive what it
            # is counting attempts against and needs no separate cleanup.
            ttl = client.ttl(code_key)
            if ttl and ttl > 0:
                client.expire(attempts_key, ttl)

        if new_attempts >= max_attempts:
            # Not deleted -- see the "locked" branch above for why the code
            # must keep existing (as unusable) rather than vanish.
            return "locked"
        return "invalid"


def discard(purpose: OtpPurpose, email: str) -> None:
    """Forget any live code and attempt counter for (purpose, email), without
    touching the resend cooldown. Used when a fresher flow supersedes an
    outstanding one and the old code should stop being guessable."""
    with translate_errors("discard a one-time code"):
        client = get_client()
        client.delete(_code_key(purpose, email))
        client.delete(_attempts_key(purpose, email))
