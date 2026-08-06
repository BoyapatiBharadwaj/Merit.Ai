"""
Temporary one-time-passcode state, in Redis, for the two purposes that are
genuinely transient: SIGNUP and PASSWORD_RESET codes. ACTIVATION tokens stay in
the `otp_codes` Postgres table (see app/services/otp_service.py's module
docstring for why) -- an activation link is a days-long, click-once bearer
token handed out by an admin action, not a short-lived typed code, and nothing
in this rewrite's brief singles it out the way it does "OTP" specifically.

Only ever stores a hash, never the code itself -- same HMAC-SHA256 digest
otp_service already used against the Postgres table, just written to a
different backend. Three keys per (purpose, email), matching the namespacing
this rewrite specifies exactly:

    otp:{purpose}:{email}            the current code's hash. TTL = its
                                      lifetime; Redis expiring the key IS the
                                      code expiring, with nothing to sweep.
    otp:attempts:{purpose}:{email}   wrong-guess counter for that code. Given
                                      the same TTL as the code itself on its
                                      first use, so it can never outlive it.
    otp:cooldown:{purpose}:{email}   existence == "an address was mailed too
                                      recently to mail it again". Its own TTL
                                      (OTP_RESEND_COOLDOWN_SECONDS) is
                                      independent of the code's, since a
                                      resend-cooldown and a code's validity
                                      window are different durations.

One-time use is enforced by deleting the code key immediately on a correct
guess -- there is no "consumed" flag to check, because a deleted key cannot be
guessed against again. This is stricter than the Postgres version was for
SIGNUP: that implementation let a caller defer consumption until an outer
transaction committed (`verify_code(..., commit=False)`), so a signup that
failed for an unrelated reason (a duplicate student ID) did not burn the code.
Redis has no transaction to defer into, and the brief this module implements
is explicit that OTP data must be deleted immediately on success -- so that
one narrow edge case (retry a failed signup without waiting for a fresh code)
is a deliberate, documented regression, not an oversight.
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
    """Store a freshly issued code's hash, reset its attempt budget, and start
    a new resend cooldown -- all three in one round trip via a pipeline, so a
    reader never observes a code key with a stale, pre-reset attempts key.

    `cooldown_seconds <= 0` (the test suite's "no cooldown" configuration)
    skips setting the cooldown key entirely rather than passing a zero TTL to
    Redis, which SET EX rejects outright as an invalid expire time.
    """
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
    """Check one guess against the live code for (purpose, email).

    Returns one of:
      "ok"      the guess matched. The code and its attempt counter are gone;
                a second call with the same code now returns "invalid".
      "invalid" no live code, or the guess was wrong (attempts remain).
      "locked"  the attempt budget is exhausted. The code is deliberately
                NOT deleted in this case (only on "ok"): a locked-out code
                must keep returning "locked" for every further call --
                including one with the actually-correct guess -- until it
                expires or a fresh code is issued (see otp_redis_store.issue,
                which always resets the attempt counter). Deleting it here
                would make a subsequent call see no code at all and report
                "invalid" instead, silently downgrading a burned code back to
                merely "wrong so far".

    Mirrors otp_service.verify_code's Postgres-era ordering exactly: the
    budget is checked BEFORE this guess counts against it (so a caller who is
    already locked out never gets a free extra attempt from asking), and a
    correct guess is checked before an incorrect one increments anything.
    """
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

        # compare_digest, not ==, for the same constant-time reason
        # otp_service always compared this way: string equality's early exit on
        # the first differing byte leaks, over enough requests, how many
        # leading characters of a guess were correct.
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
