"""Field types shared by every schema that accepts a name, an email or a password."""
import re
import unicodedata
from typing import Annotated

from pydantic import AfterValidator, BeforeValidator, EmailStr, Field

from app.core import passwords

# Matches the users.email column. A longer address is a clean 422 naming the
# field rather than an IntegrityError surfacing as an opaque 503.
MAX_EMAIL_LENGTH = 150
MAX_NAME_LENGTH = 75


def _normalise_name(value: str) -> str:
    """Trim, collapse internal runs of whitespace, and normalise Unicode."""
    if not isinstance(value, str):
        return value
    value = unicodedata.normalize("NFKC", value)
    value = "".join(ch for ch in value if unicodedata.category(ch)[0] != "C")
    return re.sub(r"\s+", " ", value).strip()


def _reject_empty_name(value: str) -> str:
    if not value:
        raise ValueError("Name cannot be blank or only spaces.")
    return value


def _normalise_email(value):
    """Strip and lowercase BEFORE validation, so the stored form is canonical."""
    return value.strip().lower() if isinstance(value, str) else value


def _check_password(value: str) -> str:
    reason = passwords.check(value)
    if reason:
        raise ValueError(reason)
    return value


NameField = Annotated[
    str,
    BeforeValidator(_normalise_name),
    AfterValidator(_reject_empty_name),
    Field(min_length=1, max_length=MAX_NAME_LENGTH),
]

OptionalNameField = Annotated[
    str | None,
    BeforeValidator(lambda v: _normalise_name(v) if v is not None else None),
    Field(default=None, max_length=MAX_NAME_LENGTH),
]

EmailField = Annotated[
    EmailStr,
    BeforeValidator(_normalise_email),
    Field(max_length=MAX_EMAIL_LENGTH),
]

OptionalEmailField = Annotated[
    EmailStr | None,
    BeforeValidator(lambda v: _normalise_email(v) if v is not None else None),
    Field(default=None, max_length=MAX_EMAIL_LENGTH),
]

# The policy itself lives in app/core/passwords.py.
PasswordField = Annotated[
    str,
    AfterValidator(_check_password),
    Field(min_length=1, max_length=128),
]
