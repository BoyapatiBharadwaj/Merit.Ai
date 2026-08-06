from pydantic import BaseModel, EmailStr


class UserOut(BaseModel):
    id: int
    full_name: str
    first_name: str = ""
    last_name: str = ""
    email: EmailStr
    role: str
    is_active: bool

    class Config:
        from_attributes = True


class StudentProfileOut(UserOut):
    """GET /users/me for a student -- adds the verification state the Profile
    page and the exam-entry gate both depend on."""
    student_id: int | None = None
    roll_number: str | None = None
    face_registered: bool = False
    id_verified: bool = False
    identity_locked: bool = False
    exam_ready: bool = False
    # An administrator has asked this candidate to prove their identity again.
    # Carried on /users/me because it is the candidate's own business: without
    # it the Profile page would show two green ticks and a blocked exam gate,
    # with nothing anywhere explaining the contradiction.
    reverification_required: bool = False
    reverification_reason: str | None = None


class StudentOut(BaseModel):
    id: int
    user_id: int
    full_name: str
    first_name: str = ""
    last_name: str = ""
    email: EmailStr
    roll_number: str | None = None
    face_registered: bool = False
    id_verified: bool = False
    identity_locked: bool = False
    is_active: bool = True

    class Config:
        from_attributes = True


class ExaminerOut(BaseModel):
    id: int
    user_id: int
    full_name: str
    first_name: str = ""
    last_name: str = ""
    email: EmailStr
    organization_name: str | None = None
    is_active: bool = True

    class Config:
        from_attributes = True
