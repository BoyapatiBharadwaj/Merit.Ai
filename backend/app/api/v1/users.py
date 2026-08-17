"""User management endpoints."""
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.database.session import get_db
from app.api.deps import get_current_user, require_admin, require_admin_or_examiner, require_student
from app.repositories import proctor_repository, user_repository
from app.schemas.auth import AdminResetPasswordRequest, ChangePasswordRequest, UpdateProfileRequest
from app.schemas.user import UserOut, StudentOut, StudentProfileOut, ExaminerOut
from app.models.enums import RoleName
from app.models.activity_log import ActivityType
from app.services import (activity_service, admin_service, auth_service, biometric_service, identity_service, organization_service)
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


@router.get("/me/biometrics")
def my_biometric_status(db: Session = Depends(get_db), user: User = Depends(require_student)):
    """What biometric data is held about the caller, and under which consent."""
    student = user.student_profile
    if not student:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No student profile.")
    return biometric_service.consent_status(db, student)


@router.delete("/me/biometrics")
def erase_my_biometrics(db: Session = Depends(get_db), user: User = Depends(require_student)):
    """Erase the caller's own face embedding, face photo and ID-card image."""
    student = user.student_profile
    if not student:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No student profile.")
    removed = biometric_service.erase_student_biometrics(db, student, reason="student_request")
    activity_service.record(db, activity_type=ActivityType.BIOMETRICS_ERASED, subject=user,
                            description="Erased their own face and ID data")
    return {
        "erased": removed,
        "message": ("Your face and ID-card data have been deleted. Your exam results and history are "
                    "unaffected. You'll need to register your face again before your next proctored exam."),
    }


@router.delete("/{user_id}/biometrics")
def erase_student_biometrics(user_id: int, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    """Admin-initiated erasure, for acting on a deletion request out-of-band."""
    target = user_repository.get_user_by_id(db, user_id)
    if not target or not target.student_profile:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No student found for that user id.")
    removed = biometric_service.erase_student_biometrics(
        db, target.student_profile, reason=f"admin:{admin.id}",
    )
    activity_service.record(db, activity_type=ActivityType.BIOMETRICS_ERASED,
                            subject=target, actor=admin)
    return {"erased": removed, "student_id": target.student_profile.id}


@router.patch("/me", response_model=StudentProfileOut | UserOut)
def update_me(payload: UpdateProfileRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Name/email edit. Returns 403 for a student whose identity is locked --
    see identity_service for why that lock exists."""
    auth_service.update_profile(db, user, payload.first_name, payload.last_name, payload.email)
    return _me_payload(db, user)


@router.post("/me/password", status_code=200)
def change_my_password(payload: ChangePasswordRequest, request: Request, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Admins and examiners. NOT students."""
    if user.role.name == RoleName.STUDENT.value:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Passwords are managed by your administrator. If you've forgotten yours, "
            "use the 'Forgot password' link to get a reset code by email.")
    auth_service.change_password(db, user, payload.current_password, payload.new_password)
    activity_service.record(db, activity_type=ActivityType.PASSWORD_CHANGED, subject=user, request=request)
    return {"changed": True, "message": "Password updated successfully."}


@router.get("/students", response_model=list[StudentOut])
def list_students(db: Session = Depends(get_db), user: User = Depends(require_admin_or_examiner)):
    """Admins see every student; an examiner sees only their own organization."""
    if user.role.name == RoleName.EXAMINER.value:
        examiner = user_repository.get_examiner_by_user_id(db, user.id)
        students = (organization_service.list_organization_students(db, examiner.organization_id)
                    if examiner and examiner.organization_id else [])
    else:
        students = user_repository.list_students(db)

    # One face-profile query for the whole directory rather than one per
    # student -- see proctor_repository.students_with_face_profiles.
    face_ids = proctor_repository.students_with_face_profiles(db, [s.id for s in students])
    out = []
    for s in students:
        state = identity_service.verification_state(db, s, face_registered_ids=face_ids)
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
def deactivate_user(user_id: int, request: Request, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    target = _load_managed_user(db, user_id, admin)
    auth_service.set_active(db, target, False)
    activity_service.record(db, activity_type=ActivityType.ACCOUNT_DISABLED,
                            subject=target, actor=admin, request=request)
    return UserOut(**_base_user_fields(target))


@router.post("/{user_id}/activate", response_model=UserOut)
def activate_user(user_id: int, request: Request, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    target = _load_managed_user(db, user_id, admin)
    auth_service.set_active(db, target, True)
    activity_service.record(db, activity_type=ActivityType.ACCOUNT_ENABLED,
                            subject=target, actor=admin, request=request)
    return UserOut(**_base_user_fields(target))


@router.delete("/{user_id}", status_code=200)
def delete_user(user_id: int, request: Request, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    """Permanently delete a student or examiner account."""
    target = _load_managed_user(db, user_id, admin)
    # Checked here, not only on the Examiners page.
    admin_service.assert_deletable(db, target)
    activity_service.record(
        db, activity_type=ActivityType.ACCOUNT_DELETED, subject=target, actor=admin, request=request,
        description=f"Account deleted by {admin.email}",
        context={"role": target.role.name, "email": target.email},
    )
    summary = {"deleted": True, "user_id": target.id, "email": target.email, "role": target.role.name}
    db.delete(target)
    db.commit()
    return summary


@router.post("/{user_id}/reset-password", status_code=200)
def reset_user_password(user_id: int, payload: AdminResetPasswordRequest, request: Request,
                        db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    """Set a new password for a managed account."""
    target = _load_managed_user(db, user_id, admin)
    auth_service.admin_reset_password(db, target, payload.new_password)
    activity_service.record(db, activity_type=ActivityType.PASSWORD_RESET_BY_ADMIN,
                            subject=target, actor=admin, request=request)
    return {
        "changed": True,
        "message": f"Password reset for {target.email}.",
        "email": target.email,
        # Shown once, then gone. See the docstring.
        "password": payload.new_password,
    }


@router.post("/students/{student_id}/unlock-identity", status_code=200)
def unlock_student_identity(student_id: int, request: Request, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    """Admin escape hatch: clears the identity lock and the ID-verification flag so a student
    can redo verification after a legitimate name change or a bad initial capture.
    """
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
    activity_service.record(db, activity_type=ActivityType.IDENTITY_UNLOCKED,
                            subject=student.user, actor=admin, request=request)
    return {"unlocked": True, "message": "Identity unlocked. The student must verify their ID card again."}
