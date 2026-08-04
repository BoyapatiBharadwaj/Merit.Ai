from pydantic import BaseModel, EmailStr, Field


class LoginRequest(BaseModel):
    email: EmailStr
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
    first_name: str = Field(min_length=1, max_length=75)
    last_name: str = Field(min_length=1, max_length=75)
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    roll_number: str | None = Field(default=None, max_length=50)


class CreateExaminerRequest(BaseModel):
    """Admin-only. Examiners never self-register, so the admin supplies every
    field including the initial password."""
    first_name: str = Field(min_length=1, max_length=75)
    last_name: str = Field(min_length=1, max_length=75)
    email: EmailStr
    organization_name: str = Field(min_length=1, max_length=150)
    password: str = Field(min_length=8, max_length=128)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)


class UpdateProfileRequest(BaseModel):
    """Name/email edits. Rejected with 403 once the student's identity is
    locked -- password is the only field they can change after that."""
    first_name: str | None = Field(default=None, min_length=1, max_length=75)
    last_name: str | None = Field(default=None, min_length=1, max_length=75)
    email: EmailStr | None = None


class AdminResetPasswordRequest(BaseModel):
    new_password: str = Field(min_length=8, max_length=128)


# --- One-time passcode flows --------------------------------------------------

class OtpRequest(BaseModel):
    """Ask for a code. Used by both signup verification and password reset;
    which one is decided by the endpoint, not by a field here, so a caller can
    never request a signup code and spend it on a password reset."""
    email: EmailStr


class OtpVerifyRequest(BaseModel):
    email: EmailStr
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


class RegisterStudentWithOtpRequest(RegisterStudentRequest):
    """Student self-registration, gated on a code already mailed to `email`.

    Subclasses the existing request rather than replacing it so the two share
    one definition of what a student registration is; the plain endpoint stays
    available for deployments running with EMAIL_ENABLED=false.
    """
    code: str = Field(min_length=4, max_length=12)


class PasswordResetConfirmRequest(BaseModel):
    email: EmailStr
    code: str = Field(min_length=4, max_length=12)
    new_password: str = Field(min_length=8, max_length=128)
