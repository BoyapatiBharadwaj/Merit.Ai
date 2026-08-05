"""Request bodies for the admin dashboard's drill-down endpoints.

Responses from app/api/v1/admin.py are deliberately plain dicts rather than
Pydantic models -- see attempts.py's /my, /exam/{id} etc. for the existing
precedent of ad hoc aggregation shapes in this codebase. These endpoints
assemble many small, page-specific fields (table rows, summary cards) where
a strict schema per view would be a lot of ceremony for shapes that only
admin_service.py itself reads back.
"""
from pydantic import BaseModel, Field


class ExaminerUpdateRequest(BaseModel):
    """PATCH /admin/examiners/{id} -- every field optional so the admin can save
    just a name fix without resending everything else.

    `organization_id` is now accepted. The previous note said reassigning a
    tenant was too risky for this form, and excluded it -- but the form still
    took `organization_name`, which the service wrote straight to a display
    string while leaving the authoritative `organization_id` untouched. So the
    riskier operation was not prevented, only made ineffective and invisible: an
    administrator typed a new organization, saw it save, and had moved nobody.
    Accepting the id and resolving the name to a real row makes the two agree.

    Email stays excluded -- it is a login credential and is edited nowhere else.
    """
    organization_id: int | None = None
    first_name: str | None = Field(default=None, min_length=1, max_length=75)
    last_name: str | None = Field(default=None, min_length=1, max_length=75)
    organization_name: str | None = Field(default=None, max_length=150)


class ViolationDecisionUpdate(BaseModel):
    decision: str = Field(pattern="^(pending|confirmed|dismissed)$")
