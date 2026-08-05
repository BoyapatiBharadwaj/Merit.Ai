"""
The password policy. One definition, used by every path that sets a password.

There were previously two policies and they disagreed. The registration UI
listed an uppercase letter, a number and a special character; both the frontend
and the backend enforced only `min_length=8`. So `aaaaaaaa` was accepted while
the screen said it would not be, and a candidate who followed the instructions
got no more protection than one who ignored them. Worse, the eight-character
rule was repeated in six schemas, which is how a policy comes to be tightened in
four of them.

What is enforced now, and why it is not the composition rule the UI promised:

  * A length floor (PASSWORD_MIN_LENGTH, default 10) and nothing about
    character classes. Composition rules push people towards `Password1!` --
    predictable substitutions that add almost nothing against a real cracking
    dictionary while making passwords harder to remember, so they get reused or
    written down. NIST SP 800-63B has recommended against them since 2017.
  * A blocklist, which is what composition rules were badly approximating.
    `Password1!` satisfies every classic composition rule and appears in every
    cracking wordlist; rejecting it directly is the check that was actually
    wanted.
  * A context check: the password must not simply be the person's own name or
    the local part of their email. Those are the first things guessed against a
    named account and no length rule catches them.

The 72-byte ceiling is not arbitrary either. bcrypt truncates at 72 bytes, so
without it two different long passwords could hash identically and both open the
account -- silently, with no error anywhere.
"""
import re
import unicodedata

from fastapi import HTTPException, status

from app.core.config import settings

# bcrypt's hard limit. Everything past this byte is discarded by the KDF, so a
# password accepted beyond it would be quietly weaker than it appears.
MAX_PASSWORD_BYTES = 72

# Deliberately short. This is not a substitute for a breach corpus -- it catches
# the handful of passwords that show up unprompted in any real signup table,
# plus the shapes the old composition rule actively encouraged. A proper
# k-anonymity check against Have I Been Pwned is the real answer and needs an
# outbound call this application does not currently make on the signup path.
_COMMON_PASSWORDS = {
    "password", "password1", "password12", "password123", "password1234",
    "passw0rd", "p@ssword", "p@ssw0rd", "passw0rd123", "p@ssw0rd123",
    "12345678", "123456789", "1234567890", "1234567891", "0123456789",
    "qwertyuiop", "qwerty123", "qwertyui", "asdfghjkl", "zxcvbnm123",
    "iloveyou", "letmein123", "welcome123", "admin1234", "administrator",
    "abc12345", "abcd1234", "aaaaaaaa", "11111111", "00000000",
    "student123", "teacher123", "college123", "university", "examexam",
    "changeme", "changeme123", "secret123", "trustno1", "sunshine1",
    "football1", "baseball1", "princess1", "dragon123", "monkey123",
}


def _normalise(value: str) -> str:
    """NFKC, so visually identical passwords compare and hash consistently.

    Without it a password typed with a composed accent and the same password
    typed with a combining one are different byte strings, and a user who
    switches keyboards cannot sign in with what looks like the same password.
    """
    return unicodedata.normalize("NFKC", value or "")


def _is_repetitive(password: str) -> bool:
    """One character, or one short unit, repeated to reach the length floor.

    `aaaaaaaaaa` and `abababababab` clear a length check while carrying almost
    no entropy, and a length-only policy invites exactly this.
    """
    if len(set(password)) <= 2:
        return True
    for unit in (1, 2, 3):
        if len(password) >= unit * 3 and password == (password[:unit] * (len(password) // unit))[:len(password)]:
            return True
    return False


def _is_sequential(password: str) -> bool:
    """A straight run up or down the alphabet or the number row."""
    lowered = password.lower()
    if len(lowered) < 6:
        return False
    deltas = {ord(b) - ord(a) for a, b in zip(lowered, lowered[1:])}
    return deltas in ({1}, {-1})


def check(password: str) -> str | None:
    """Context-free checks. Returns a reason, or None if the password passes.

    Returns rather than raises so a Pydantic validator and a service call can
    share it without one of them having to catch the other's exception type.
    """
    password = _normalise(password)

    if len(password) < settings.PASSWORD_MIN_LENGTH:
        return (f"Password must be at least {settings.PASSWORD_MIN_LENGTH} characters. "
                "A short phrase you can remember is stronger than a short word with "
                "symbols in it.")

    if len(password.encode("utf-8")) > MAX_PASSWORD_BYTES:
        return f"Password is too long. Please keep it under {MAX_PASSWORD_BYTES} bytes."

    if password != password.strip():
        return "Password cannot begin or end with a space."

    if not password.strip():
        return "Password cannot be blank."

    lowered = password.lower()
    if lowered in _COMMON_PASSWORDS:
        return "That password is one of the most commonly used ones. Please choose another."

    # Also catch the common password with digits stapled on, which is how most
    # people satisfy a "must contain a number" rule.
    stripped = re.sub(r"[0-9!@#$%^&*()_+\-=]+$", "", lowered)
    if stripped and stripped in _COMMON_PASSWORDS:
        return "That password is a common one with characters added. Please choose another."

    if _is_repetitive(password):
        return "Password repeats the same characters. Please choose something less predictable."

    if _is_sequential(password):
        return "Password is a simple sequence. Please choose something less predictable."

    return None


def check_with_context(password: str, *, email: str | None = None,
                       name: str | None = None) -> str | None:
    """`check`, plus the parts that need to know whose account this is.

    Split out because the schema layer validating a request body has no user
    context, while the service layer setting the password always does. Both go
    through the same rules; this one simply knows more.
    """
    reason = check(password)
    if reason:
        return reason

    lowered = _normalise(password).lower()

    if email:
        local_part = email.split("@")[0].strip().lower()
        if local_part and _is_built_from(lowered, local_part):
            return "Password must not be built from your email address."

    if name:
        for part in re.split(r"[\s._-]+", name.strip().lower()):
            if _is_built_from(lowered, part):
                return "Password must not be built from your name."

    return None


# How much password has to remain, once the identifier is removed, for the
# password to be something other than that identifier with decoration.
#
# Five, not four, because four lets through the single most common shape of
# this: username plus a year. `sandhya2005` for sandhya@ leaves exactly four
# characters and is precisely the password an attacker targeting that account
# tries in the first hundred guesses.
_MIN_REMAINDER = 5


def _is_built_from(password: str, identifier: str) -> bool:
    """Is this password essentially the identifier with bits stapled on?

    The obvious rule -- "reject if the password contains the identifier" -- is
    too blunt and rejects passwords that are perfectly good. `Sup3rSecret!` for
    `secret@example.com` contains "secret" and is not remotely derived from it;
    telling that candidate to think of another password would be the validator
    being wrong at them.

    What actually matters is whether the identifier is the SUBSTANCE of the
    password. `secret123` is the username plus three digits and is the first
    thing guessed against that account; `Sup3rSecret!` has six other characters
    doing real work. Measuring what is left after removing the identifier
    separates the two, where a containment test cannot.
    """
    if len(identifier) < 4:
        # Too short to carry meaning -- "john", "ravi", "lee" appear inside
        # ordinary words constantly, and blocking them would reject far more
        # good passwords than bad ones.
        return False
    if identifier not in password:
        return False
    return len(password.replace(identifier, "")) < _MIN_REMAINDER


def require(password: str, *, email: str | None = None, name: str | None = None) -> None:
    """Raise 400 if the password is unacceptable. The service-layer entry point."""
    reason = check_with_context(password, email=email, name=name)
    if reason:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, reason)


def describe() -> list[str]:
    """The rules, in the wording the UI should show.

    Served to the frontend rather than duplicated there, because the previous
    arrangement -- the UI listing one policy from memory while the server
    enforced another -- is the bug this module exists to fix, and it would come
    straight back the moment the two lists were maintained separately.
    """
    return [
        f"At least {settings.PASSWORD_MIN_LENGTH} characters",
        "Not a commonly used password",
        "Not your name or email address",
        "No simple repetition or sequences",
    ]
