"""
Authentication endpoints: student self-registration, admin-created examiner
accounts, and login for all roles.
"""
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import create_access_token
from app.core.rate_limit import rate_limit
from app.database.session import get_db
from app.models.otp import OtpPurpose
from app.schemas.auth import (
    CreateExaminerRequest, LoginRequest, OtpRequest, OtpRequestAccepted,
    PasswordResetConfirmRequest, RegisterStudentRequest, RegisterStudentWithOtpRequest,
    TokenResponse,
)
from app.models.activity_log import ActivityType
from app.services import activity_service, auth_service, email_service, otp_service
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
def register_student(payload: RegisterStudentRequest, request: Request, db: Session = Depends(get_db)):
    user, _ = auth_service.register_student(
        db, payload.first_name, payload.last_name, payload.email, payload.password, payload.roll_number
    )
    activity_service.record(db, activity_type=ActivityType.SIGNED_UP, subject=user, request=request,
                            description="Created their account")
    return _token_response(user)


@router.post("/examiners", response_model=TokenResponse, status_code=201)
def create_examiner(payload: CreateExaminerRequest, request: Request, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    """Admin-only: create a new examiner account. Examiners never self-register."""
    user, _ = auth_service.create_examiner(
        db, admin.id, payload.first_name, payload.last_name, payload.email, payload.password, payload.organization_name
    )
    activity_service.record(db, activity_type=ActivityType.ACCOUNT_CREATED_BY_ADMIN,
                            subject=user, actor=admin, request=request)
    return _token_response(user)


@router.post("/login", response_model=TokenResponse, dependencies=[Depends(rate_limit("login"))])
def login(payload: LoginRequest, request: Request, db: Session = Depends(get_db)):
    try:
        _, user = auth_service.authenticate(db, payload.email, payload.password)
    except HTTPException:
        # Recorded against the ADDRESS, not a user -- a failed attempt on an
        # address with no account is exactly the entry worth keeping, and
        # requiring a real User row would silently drop those.
        activity_service.record(
            db, activity_type=ActivityType.LOGIN_FAILED,
            subject_email=payload.email.strip().lower(), request=request,
        )
        raise
    activity_service.record(db, activity_type=ActivityType.LOGGED_IN, subject=user, request=request)
    return _token_response(user)


# ------------------------------------------------------------------------------
# One-time passcode flows
#
# All four endpoints are rate limited per IP on top of the per-address cooldown
# inside otp_service: the cooldown stops one mailbox being flooded, the rate
# limit stops one source cycling through many addresses. Neither substitutes
# for the other.
# ------------------------------------------------------------------------------

def _require_email_capability() -> None:
    """Refuse to run an email-gated flow on a deployment that cannot send email.

    Without this the endpoints would happily return "check your inbox" on a
    stack with EMAIL_ENABLED=false, and the user would wait forever for a
    message that was never going to be sent. The existing ForgotPassword page
    made exactly this point about not promising undeliverable email; a 503 that
    names the missing configuration keeps that promise on the API side.
    """
    if not email_service.is_enabled():
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Email delivery is not configured on this server, so codes cannot be sent. "
            "An administrator can enable it by setting EMAIL_ENABLED and the SMTP_* values.",
        )


@router.post("/otp/signup/request", response_model=OtpRequestAccepted,
             dependencies=[Depends(rate_limit("otp_request"))])
def request_signup_code(payload: OtpRequest, background: BackgroundTasks, db: Session = Depends(get_db)):
    """Email a verification code to an address about to be registered."""
    _require_email_capability()
    otp_service.request_code(db, email=payload.email, purpose=OtpPurpose.SIGNUP, background=background)
    return OtpRequestAccepted(
        message="If that address can receive mail, a verification code is on its way.",
        expires_in_minutes=settings.OTP_TTL_MINUTES,
    )


@router.post("/register/student/verified", response_model=TokenResponse, status_code=201,
             dependencies=[Depends(rate_limit("register"))])
def register_student_verified(payload: RegisterStudentWithOtpRequest, request: Request, db: Session = Depends(get_db)):
    """Student self-registration with the emailed code checked first.

    Order matters: the code is verified BEFORE the account is created, so a
    failed registration (duplicate email, say) cannot burn a valid code, and a
    valid code cannot be spent without an account resulting from it.
    """
    _require_email_capability()
    otp_service.verify_code(db, email=payload.email, purpose=OtpPurpose.SIGNUP, code=payload.code)
    user, _ = auth_service.register_student(
        db, payload.first_name, payload.last_name, payload.email, payload.password, payload.roll_number
    )
    activity_service.record(db, activity_type=ActivityType.SIGNED_UP, subject=user, request=request,
                            description="Created their account (email verified by code)")
    return _token_response(user)


@router.post("/password-reset/request", response_model=OtpRequestAccepted,
             dependencies=[Depends(rate_limit("otp_request"))])
def request_password_reset(payload: OtpRequest, background: BackgroundTasks, db: Session = Depends(get_db)):
    """Email a reset code.

    Responds identically whether or not the address has an account -- see
    otp_service.request_code. A code is genuinely issued either way, so even
    the response timing does not distinguish the two cases.
    """
    _require_email_capability()
    otp_service.request_code(db, email=payload.email, purpose=OtpPurpose.PASSWORD_RESET, background=background)
    return OtpRequestAccepted(
        message="If an account exists for that address, a reset code is on its way.",
        expires_in_minutes=settings.OTP_TTL_MINUTES,
    )


@router.post("/password-reset/confirm", dependencies=[Depends(rate_limit("otp_verify"))])
def confirm_password_reset(payload: PasswordResetConfirmRequest, request: Request, db: Session = Depends(get_db)):
    """Verify the code and set the new password.

    Reports success even when no account exists for the address, for the same
    enumeration reason as the request step -- the code was still valid, so
    saying "that worked" reveals nothing beyond what the caller already knew,
    while "no such user" would confirm the address is unregistered.
    """
    _require_email_capability()
    otp_service.verify_code(db, email=payload.email, purpose=OtpPurpose.PASSWORD_RESET, code=payload.code)
    auth_service.reset_password_by_email(db, payload.email, payload.new_password)
    activity_service.record(db, activity_type=ActivityType.PASSWORD_RESET_BY_EMAIL,
                            subject_email=payload.email.strip().lower(), request=request)
    return {"reset": True, "message": "Your password has been updated. You can sign in with it now."}
