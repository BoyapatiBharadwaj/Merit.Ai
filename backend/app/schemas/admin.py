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
    """PATCH /admin/examiners/{id} -- every field optional so the admin can
    save just a name fix or just an organization rename without resending
    everything else. Deliberately excludes email (a login credential, edited
    nowhere else in this app either) and organization_id (reassigning an
    examiner's tenant would strand or relocate their existing exams' access
    rules -- a bigger, riskier operation than this simple edit form is for).
    """
    first_name: str | None = Field(default=None, min_length=1, max_length=75)
    last_name: str | None = Field(default=None, min_length=1, max_length=75)
    organization_name: str | None = Field(default=None, max_length=150)


class ViolationDecisionUpdate(BaseModel):
    decision: str = Field(pattern="^(pending|confirmed|dismissed)$")
