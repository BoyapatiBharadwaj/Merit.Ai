"""Request bodies for the admin dashboard's drill-down endpoints."""
from pydantic import BaseModel, Field

from app.schemas.common import OptionalEmailField, OptionalNameField


class ExaminerUpdateRequest(BaseModel):
    """PATCH /admin/examiners/{id} -- every field optional so the admin can save just a name fix
    without resending everything else.
    """
    organization_id: int | None = None
    first_name: str | None = Field(default=None, min_length=1, max_length=75)
    last_name: str | None = Field(default=None, min_length=1, max_length=75)
    organization_name: str | None = Field(default=None, max_length=150)


class ViolationDecisionUpdate(BaseModel):
    decision: str = Field(pattern="^(pending|confirmed|dismissed)$")


class CandidateUpdateRequest(BaseModel):
    """PATCH /admin/candidates/{id} -- correcting a candidate's account details."""
    first_name: OptionalNameField = None
    last_name: OptionalNameField = None
    email: OptionalEmailField = None
    roll_number: str | None = Field(default=None, max_length=50)


class ReverificationRequest(BaseModel):
    """POST /admin/candidates/{id}/require-reverification."""
    reason: str | None = Field(default=None, max_length=500)
    notify: bool = True
