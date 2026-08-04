"""
Authentication endpoints: student self-registration, admin-created examiner
accounts, and login for all roles.
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.security import create_access_token
from app.core.rate_limit import rate_limit
from app.database.session import get_db
from app.schemas.auth import LoginRequest, TokenResponse, RegisterStudentRequest, CreateExaminerRequest
from app.services import auth_service
from app.api.deps import require_admin
from app.models.user import User

router = APIRouter(prefix="/auth", tags=["Authentication"])


def _token_response(user: User) -> TokenResponse:
    return TokenResponse(
        access_token=create_access_token(subject=str(user.id), role=user.role.name),
        role=user.role.name,
        full_name=user.full_name,
        first_name=user.first_name,
        last_name=user.last_name,
        user_id=user.id,
    )


@router.post("/register/student", response_model=TokenResponse, status_code=201,
             dependencies=[Depends(rate_limit("register"))])
def register_student(payload: RegisterStudentRequest, db: Session = Depends(get_db)):
    user, _ = auth_service.register_student(
        db, payload.first_name, payload.last_name, payload.email, payload.password, payload.roll_number
    )
    return _token_response(user)


@router.post("/examiners", response_model=TokenResponse, status_code=201)
def create_examiner(payload: CreateExaminerRequest, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    """Admin-only: create a new examiner account. Examiners never self-register."""
    user, _ = auth_service.create_examiner(
        db, admin.id, payload.first_name, payload.last_name, payload.email, payload.password, payload.organization_name
    )
    return _token_response(user)


@router.post("/login", response_model=TokenResponse, dependencies=[Depends(rate_limit("login"))])
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    _, user = auth_service.authenticate(db, payload.email, payload.password)
    return _token_response(user)
