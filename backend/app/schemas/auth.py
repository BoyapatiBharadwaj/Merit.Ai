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
