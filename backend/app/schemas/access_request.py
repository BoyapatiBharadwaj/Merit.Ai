from datetime import datetime

from pydantic import BaseModel, EmailStr, Field


class AccessRequestCreate(BaseModel):
    """Public payload from the marketing site's "Request examiner access" form."""
    first_name: str = Field(min_length=1, max_length=75)
    last_name: str = Field(min_length=1, max_length=75)
    email: EmailStr
    organization_name: str = Field(min_length=2, max_length=150)
    # Long enough to be a real sentence -- this is the field an admin actually
    # reads when deciding whether to grant access.
    purpose: str = Field(min_length=10, max_length=2000)


class AccessRequestOut(BaseModel):
    id: int
    first_name: str
    last_name: str
    full_name: str
    email: EmailStr
    organization_name: str
    purpose: str
    status: str
    review_note: str | None = None
    created_at: datetime | None = None
    reviewed_at: datetime | None = None
    created_user_id: int | None = None

    class Config:
        from_attributes = True


class AccessRequestApprove(BaseModel):
    """Approving mints the examiner account and emails an activation link."""
    review_note: str | None = Field(default=None, max_length=255)


class AccessRequestReject(BaseModel):
    review_note: str | None = Field(default=None, max_length=255)
