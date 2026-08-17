from pydantic import BaseModel, Field

from app.schemas.common import (
    EmailField, NameField, OptionalEmailField, OptionalNameField, PasswordField,
)


class LoginRequest(BaseModel):
    # Login deliberately does NOT use PasswordField: the policy
    # applies to passwords being SET, not to one being checked.
    email: EmailField
    password: str = Field(min_length=1, max_length=128)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str
    full_name: str
    first_name: str = ""
    last_name: str = ""
    user_id: int
    # True when the password on this account was set by
    # somebody else (an approval, an admin reset).
    must_change_password: bool = False


class RegisterStudentRequest(BaseModel):
    """Students self-register with a first/last name pair. Once their face and
    ID card are verified these become immutable (see users.update_me)."""
    first_name: NameField
    last_name: NameField
    email: EmailField
    password: PasswordField
    roll_number: str | None = Field(default=None, max_length=50)

    # Consent, sent and stored rather than checked only in the browser.
    accepted_terms: bool = False
    accepted_proctoring: bool = False
    terms_version: str | None = Field(default=None, max_length=20)


class CreateExaminerRequest(BaseModel):
    """Admin-only. Examiners never self-register, so the admin
    supplies the account's identity -- but never its password.
    """
    first_name: NameField
    last_name: NameField
    email: EmailField
    organization_name: str = Field(min_length=1, max_length=150)


class ExaminerProvisionedOut(BaseModel):
    """What POST /auth/examiners returns: enough to confirm what was created,
    and nothing that could be used to sign in as it."""
    user_id: int
    email: str
    full_name: str
    organization_name: str | None = None
    activation_sent: bool


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: PasswordField


class UpdateProfileRequest(BaseModel):
    """Name/email edits. Rejected with 403 once the student's identity is
    locked -- password is the only field they can change after that."""
    first_name: OptionalNameField
    last_name: OptionalNameField
    email: OptionalEmailField


class AdminResetPasswordRequest(BaseModel):
    new_password: PasswordField


class PasswordPolicyOut(BaseModel):
    """The rules the server actually enforces, for the UI to render."""
    min_length: int
    rules: list[str]


# --- One-time passcode flows --------------------------------------------------

class OtpRequest(BaseModel):
    """Ask for a code. Used by both signup verification and password reset;
    which one is decided by the endpoint, not by a field here, so a caller can
    never request a signup code and spend it on a password reset."""
    email: EmailField


class OtpVerifyRequest(BaseModel):
    email: EmailField
    # Length is deliberately a range rather than pinned to OTP_LENGTH.
    code: str = Field(min_length=4, max_length=12)


class OtpRequestAccepted(BaseModel):
    """Intentionally says nothing about whether the address is registered --
    see otp_service.request_code on account enumeration."""
    sent: bool = True
    message: str
    # Surfaced so the UI can render "expires in N minutes" without hard-coding
    # a value the server is free to change.
    expires_in_minutes: int
    # Same reasoning, for the two values the signup screen used to hard-code.
    code_length: int
    resend_after_seconds: int


class RegisterStudentWithOtpRequest(RegisterStudentRequest):
    """Student self-registration, gated on a code already mailed to `email`."""
    code: str = Field(min_length=4, max_length=12)


class PasswordResetConfirmRequest(BaseModel):
    email: EmailField
    code: str = Field(min_length=4, max_length=12)
    new_password: PasswordField


class AccountExistsCheck(BaseModel):
    email: EmailField


class AccountExistsOut(BaseModel):
    """Whether an active account exists for the address, by product decision."""
    exists: bool


class ActivationCheck(BaseModel):
    """Ask whether an activation link is still good, before showing the form."""
    email: EmailField
    token: str = Field(min_length=16, max_length=200)


class ActivationRequest(ActivationCheck):
    password: PasswordField
