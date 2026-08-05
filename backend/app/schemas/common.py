"""
Field types shared by every schema that accepts a name, an email or a password.

These exist because the same three fields were redeclared in a dozen schemas
with slightly different constraints, and the differences were all bugs:

  * `min_length=1` on a name accepts "   ". Pydantic measures the raw string;
    `User.set_name` then strips it to empty and stores a blank name on a real
    account. Every downstream use -- the ID-card OCR matcher, PDF certificates,
    the examiner's candidate list -- inherits a person with no name.
  * `EmailStr` carries no length limit, while `users.email` is VARCHAR(150). A
    long address passed validation and failed at the database as a 503.
  * Emails were lowercased in some services and not others, so whether
    `Alice@x.com` and `alice@x.com` were the same account depended on which code
    path created them.

Declaring them once means a fix lands everywhere rather than in whichever
schema someone remembered.
"""
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
    """Trim, collapse internal runs of whitespace, and normalise Unicode.

    Deliberately does NOT restrict the character set. Apostrophes, hyphens and
    non-Latin scripts are ordinary in real names -- O'Neill, Sagar-Fenton,
    ಸಂಧ್ಯಾ -- and a validator that "sanitised" them would reject the users this
    platform is being built for. Only control characters go, because those are
    never part of a name and do turn up in copy-pasted input.
    """
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
    """Strip and lowercase BEFORE validation, so the stored form is canonical.

    Applied as a BeforeValidator so EmailStr sees the same string that will be
    written to the database -- otherwise the uniqueness check and the insert can
    disagree about whether two addresses are the same.
    """
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

# The policy itself lives in app/core/passwords.py -- see that module for why it
# is a length floor plus a blocklist rather than the character-class rules the
# registration screen used to promise and nothing enforced.
PasswordField = Annotated[
    str,
    AfterValidator(_check_password),
    Field(min_length=1, max_length=128),
]
