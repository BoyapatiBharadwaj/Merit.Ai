"""
User management endpoints.

Access model:
  * student  -- own profile only (view, edit while unlocked, change password)
  * examiner -- read-only student directory, plus own profile (password is
                admin-managed only; examiners cannot change their own)
  * admin    -- full management of both students and examiners
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database.session import get_db
from app.api.deps import get_current_user, require_admin, require_admin_or_examiner
from app.repositories import user_repository
from app.schemas.auth import AdminResetPasswordRequest, ChangePasswordRequest, UpdateProfileRequest
from app.schemas.user import UserOut, StudentOut, StudentProfileOut, ExaminerOut
from app.models.enums import RoleName
from app.services import auth_service, identity_service, organization_service
from app.models.user import User

router = APIRouter(prefix="/users", tags=["Users"])


def _base_user_fields(user: User) -> dict:
    return {
        "id": user.id,
        "full_name": user.full_name,
        "first_name": user.first_name or "",
        "last_name": user.last_name or "",
        "email": user.email,
        "role": user.role.name,
        "is_active": user.is_active,
    }


def _me_payload(db: Session, user: User):
    """Students get the extra verification block; other roles don't have one."""
    if user.role.name != "student":
        return UserOut(**_base_user_fields(user))
    student = user.student_profile
    return StudentProfileOut(
        **_base_user_fields(user),
        student_id=student.id if student else None,
        roll_number=student.roll_number if student else None,
        **identity_service.verification_state(db, student),
    )


@router.get("/me", response_model=StudentProfileOut | UserOut)
def get_me(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return _me_payload(db, user)


@router.patch("/me", response_model=StudentProfileOut | UserOut)
def update_me(payload: UpdateProfileRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Name/email edit. Returns 403 for a student whose identity is locked --
    see identity_service for why that lock exists."""
    auth_service.update_profile(db, user, payload.first_name, payload.last_name, payload.email)
    return _me_payload(db, user)


@router.post("/me/password", status_code=200)
def change_my_password(payload: ChangePasswordRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Available to students and admins -- a password is a credential, not an
    identity claim, so it stays available even to identity-locked students.

    Examiners are the one exception: their credentials are issued and rotated
    by an admin only (see auth_service.admin_reset_password), so this route
    is deliberately blocked for that role rather than merely hidden in the
    UI -- the UI omission alone would not stop a direct API call.
    """
    if user.role.name == RoleName.EXAMINER.value:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Examiners cannot change their own password. Contact an administrator to reset it.")
    auth_service.change_password(db, user, payload.current_password, payload.new_password)
    return {"changed": True, "message": "Password updated successfully."}


@router.get("/students", response_model=list[StudentOut])
def list_students(db: Session = Depends(get_db), user: User = Depends(require_admin_or_examiner)):
    """Admins see every student; an examiner sees only their own organization.

    This used to be an unscoped `SELECT * FROM students` for both roles, which
    handed any examiner every other institution's student directory -- names,
    emails, roll numbers and identity-verification state. Scoping exams
    without scoping this would have been a fairly hollow tenancy boundary.
    """
    if user.role.name == RoleName.EXAMINER.value:
        examiner = user_repository.get_examiner_by_user_id(db, user.id)
        students = (organization_service.list_organization_students(db, examiner.organization_id)
                    if examiner and examiner.organization_id else [])
    else:
        students = user_repository.list_students(db)

    out = []
    for s in students:
        state = identity_service.verification_state(db, s)
        out.append(StudentOut(
            id=s.id, user_id=s.user_id, full_name=s.user.full_name,
            first_name=s.user.first_name or "", last_name=s.user.last_name or "",
            email=s.user.email, roll_number=s.roll_number, is_active=s.user.is_active,
            face_registered=state["face_registered"], id_verified=state["id_verified"],
            identity_locked=state["identity_locked"],
        ))
    return out


@router.get("/examiners", response_model=list[ExaminerOut])
def list_examiners(db: Session = Depends(get_db), _: User = Depends(require_admin)):
    return [
        ExaminerOut(
            id=e.id, user_id=e.user_id, full_name=e.user.full_name,
            first_name=e.user.first_name or "", last_name=e.user.last_name or "",
            email=e.user.email, organization_name=e.organization_name, is_active=e.user.is_active,
        )
        for e in user_repository.list_examiners(db)
    ]


def _load_managed_user(db: Session, user_id: int, admin: User) -> User:
    """Resolve an admin's management target, refusing self-management so an
    admin can't lock themselves out of their own account."""
    target = user_repository.get_user_by_id(db, user_id)
    if not target:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found.")
    if target.id == admin.id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "You cannot perform this action on your own account.")
    if target.role.name == "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin accounts cannot be managed from here.")
    return target


@router.post("/{user_id}/deactivate", response_model=UserOut)
def deactivate_user(user_id: int, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    target = _load_managed_user(db, user_id, admin)
    auth_service.set_active(db, target, False)
    return UserOut(**_base_user_fields(target))


@router.post("/{user_id}/activate", response_model=UserOut)
def activate_user(user_id: int, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    target = _load_managed_user(db, user_id, admin)
    auth_service.set_active(db, target, True)
    return UserOut(**_base_user_fields(target))


@router.post("/{user_id}/reset-password", status_code=200)
def reset_user_password(user_id: int, payload: AdminResetPasswordRequest,
                        db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    target = _load_managed_user(db, user_id, admin)
    auth_service.admin_reset_password(db, target, payload.new_password)
    return {"changed": True, "message": f"Password reset for {target.email}."}


@router.post("/students/{student_id}/unlock-identity", status_code=200)
def unlock_student_identity(student_id: int, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    """Admin escape hatch: clears the identity lock and the ID-verification
    flag so a student can redo verification after a legitimate name change or
    a bad initial capture. The stored face profile is left intact -- deleting
    a biometric should be an explicit, separate action."""
    student = user_repository.get_student_by_id(db, student_id)
    if not student:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Student not found.")
    try:
        student.identity_locked = False
        student.identity_locked_at = None
        student.id_verified = False
        student.id_verified_at = None
        student.id_verified_name = None
        db.commit()
    except Exception:
        db.rollback()
        raise
    return {"unlocked": True, "message": "Identity unlocked. The student must verify their ID card again."}
