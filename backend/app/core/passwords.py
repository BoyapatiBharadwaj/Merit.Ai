"""The password policy. One definition, used by every path that sets a password."""
import re
import unicodedata

from fastapi import HTTPException, status

from app.core.config import settings

# bcrypt's hard limit. Everything past this byte is discarded by the KDF, so a
# password accepted beyond it would be quietly weaker than it appears.
MAX_PASSWORD_BYTES = 72

# Deliberately short. This is not a substitute for a breach corpus.
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
    """NFKC, so visually identical passwords compare and hash consistently."""
    return unicodedata.normalize("NFKC", value or "")


def _is_repetitive(password: str) -> bool:
    """One character, or one short unit, repeated to reach the length floor."""
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
    """Context-free checks. Returns a reason, or None if the password passes."""
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
    """`check`, plus the parts that need to know whose account this is."""
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


# How much password has to remain, once the identifier is removed, for the password to be
# something other than that identifier with decoration.
_MIN_REMAINDER = 5


def _is_built_from(password: str, identifier: str) -> bool:
    """Is this password essentially the identifier with bits stapled on?"""
    if len(identifier) < 4:
        # Too short to carry meaning -- "john", "ravi", "lee" appear inside ordinary words
        # constantly, and blocking them would reject far more good passwords than bad ones.
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
    """The rules, in the wording the UI should show."""
    return [
        f"At least {settings.PASSWORD_MIN_LENGTH} characters",
        "Not a commonly used password",
        "Not your name or email address",
        "No simple repetition or sequences",
    ]
