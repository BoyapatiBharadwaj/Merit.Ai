"""Schemas for organizations, student rosters and per-exam participant lists."""
from datetime import datetime

from pydantic import BaseModel, Field, field_validator


class OrganizationOut(BaseModel):
    id: int
    name: str
    is_active: bool

    class Config:
        from_attributes = True


class RosterMemberOut(BaseModel):
    id: int
    email: str
    # None while the invitation is outstanding -- the person has been enrolled
    # but has not registered yet. A normal state, shown as "Invited" in the UI.
    student_id: int | None
    student_name: str | None = None
    linked_at: datetime | None
    created_at: datetime | None

    class Config:
        from_attributes = True


class RosterEnrolRequest(BaseModel):
    """Accepts a pasted list as readily as a single address."""
    emails: list[str] = Field(min_length=1, max_length=2000)

    @field_validator("emails", mode="before")
    @classmethod
    def split_pasted_text(cls, value):
        if isinstance(value, str):
            value = value.replace(",", "\n").replace(";", "\n").split()
        return value


class RosterEnrolResult(BaseModel):
    added: list[str]
    already_present: list[str]
    # Subset of `added` that matched an existing account and now has access
    # immediately, as opposed to holding an outstanding invitation.
    linked: list[str]
    # Entries that were not addresses at all -- a pasted header row, a stray name column.
    invalid: list[str] = []
    # Enrolled here, but their account already belongs to
    # another organization, so this grants them nothing.
    in_another_organization: list[str] = []


class OrganizationStudentOut(BaseModel):
    """A student with a real account, for the exam-restriction picker."""
    id: int
    full_name: str
    email: str
    roll_number: str | None

    class Config:
        from_attributes = True


class ExamParticipantAdd(BaseModel):
    """Emails to add to one exam's allow-list. Same pasted-block handling as
    the org roster -- an examiner should never have to reformat a column they
    copied out of a spreadsheet."""
    emails: list[str] = Field(min_length=1, max_length=5000)

    @field_validator("emails", mode="before")
    @classmethod
    def split_pasted_text(cls, value):
        if isinstance(value, str):
            value = value.replace(",", "\n").replace(";", "\n").split()
        return value


class ExamParticipantAddResult(BaseModel):
    added: list[str]
    already_present: list[str]
    invalid: list[str] = []
    # Added to the exam, but not on the organization roster -- so they still
    # cannot take it. Being on an exam's list is necessary, not sufficient.
    not_in_organization: list[str] = []


class ExamParticipantOut(BaseModel):
    id: int
    email: str
    student_id: int | None
    student_name: str | None = None
    # False when this address is not on the organization roster, which means
    # the invite alone will not let them in.
    in_organization: bool = True

    class Config:
        from_attributes = True


class ExamAccessOut(BaseModel):
    exam_id: int
    organization_id: int | None
    organization_name: str | None
    restricted: bool
    participants: list[ExamParticipantOut] = []
