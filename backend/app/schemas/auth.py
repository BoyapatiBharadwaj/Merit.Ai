from pydantic import BaseModel, Field

from app.schemas.common import (
    EmailField, NameField, OptionalEmailField, OptionalNameField, PasswordField,
)


class LoginRequest(BaseModel):
    # Login deliberately does NOT use PasswordField: the policy applies to
    # passwords being SET, not to one being checked. Running the new-password
    # rules here would lock out every account created before the policy changed,
    # and would leak the policy to anyone probing the login endpoint.
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


class RegisterStudentRequest(BaseModel):
    """Students self-register with a first/last name pair. Once their face and
    ID card are verified these become immutable (see users.update_me)."""
    first_name: NameField
    last_name: NameField
    email: EmailField
    password: PasswordField
    roll_number: str | None = Field(default=None, max_length=50)

    # Consent, sent and stored rather than checked only in the browser. The two
    # checkboxes were never in the payload, so any direct API caller registered
    # without agreeing -- and nothing recorded the agreement of those who did.
    accepted_terms: bool = False
    accepted_proctoring: bool = False
    terms_version: str | None = Field(default=None, max_length=20)


class CreateExaminerRequest(BaseModel):
    """Admin-only. Examiners never self-register, so the admin supplies every
    field including the initial password."""
    first_name: NameField
    last_name: NameField
    email: EmailField
    organization_name: str = Field(min_length=1, max_length=150)
    password: PasswordField


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
    """The rules the server actually enforces, for the UI to render.

    Served rather than duplicated in the frontend: the registration screen used
    to list uppercase/number/special from memory while the server checked only
    length, so the instructions and the enforcement disagreed. Two hand-kept
    lists would drift again.
    """
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
    # Length is deliberately a range rather than pinned to OTP_LENGTH: the
    # setting is configurable, and a schema that hard-codes 6 would start
    # rejecting valid codes the moment someone raises it.
    code: str = Field(min_length=4, max_length=12)


class OtpRequestAccepted(BaseModel):
    """Intentionally says nothing about whether the address is registered --
    see otp_service.request_code on account enumeration."""
    sent: bool = True
    message: str
    # Surfaced so the UI can render "expires in N minutes" without hard-coding
    # a value the server is free to change.
    expires_in_minutes: int
    # Same reasoning, for the two values the signup screen used to hard-code:
    # a six-digit placeholder and a 60-second resend cooldown, neither of which
    # tracked the server settings they were describing.
    code_length: int
    resend_after_seconds: int


class RegisterStudentWithOtpRequest(RegisterStudentRequest):
    """Student self-registration, gated on a code already mailed to `email`.

    Subclasses the existing request rather than replacing it so the two share
    one definition of what a student registration is; the plain endpoint stays
    available for deployments running with EMAIL_ENABLED=false.
    """
    code: str = Field(min_length=4, max_length=12)


class PasswordResetConfirmRequest(BaseModel):
    email: EmailField
    code: str = Field(min_length=4, max_length=12)
    new_password: PasswordField
