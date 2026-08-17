"""Authentication endpoints: student self-registration,
admin-created examiner accounts, and login for all roles.
"""
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core import passwords
from app.core.config import settings
from app.core.security import create_access_token
from app.core.rate_limit import client_ip, rate_limit
from app.database.session import get_db
from app.models.otp import OtpPurpose
from app.schemas.auth import (
    ActivationCheck, ActivationRequest,
    AccountExistsCheck, AccountExistsOut,
    CreateExaminerRequest, ExaminerProvisionedOut, LoginRequest, OtpRequest, OtpRequestAccepted,
    PasswordPolicyOut, PasswordResetConfirmRequest, RegisterStudentRequest,
    RegisterStudentWithOtpRequest, TokenResponse,
)
from app.models.activity_log import ActivityType
from app.services import (
    activity_service, auth_service, email_service, examiner_provisioning_service, otp_service,
)
from app.api.deps import require_admin
from app.models.user import User

router = APIRouter(prefix="/auth", tags=["Authentication"])


def _token_response(user: User) -> TokenResponse:
    return TokenResponse(
        access_token=create_access_token(subject=str(user.id), role=user.role.name, user=user),
        role=user.role.name,
        full_name=user.full_name,
        first_name=user.first_name,
        last_name=user.last_name,
        user_id=user.id,
        must_change_password=bool(user.must_change_password),
    )


@router.get("/password-policy", response_model=PasswordPolicyOut)
def password_policy():
    """What the server enforces, so the signup form can show exactly that."""
    return PasswordPolicyOut(min_length=settings.PASSWORD_MIN_LENGTH, rules=passwords.describe())


@router.post("/register/student", response_model=TokenResponse, status_code=201,
             dependencies=[Depends(rate_limit("register"))])
def register_student(payload: RegisterStudentRequest, request: Request, db: Session = Depends(get_db)):
    """Registration WITHOUT email verification."""
    if settings.REQUIRE_EMAIL_VERIFICATION:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Email verification is required on this server. Request a verification code and "
            "register with it.",
        )
    user, _ = auth_service.register_student(
        db, payload.first_name, payload.last_name, payload.email, payload.password, payload.roll_number,
        accepted_terms=payload.accepted_terms, accepted_proctoring=payload.accepted_proctoring,
        terms_version=payload.terms_version,
    )
    activity_service.record(db, activity_type=ActivityType.SIGNED_UP, subject=user, request=request,
                            description="Created their account")
    return _token_response(user)


@router.post("/examiners", response_model=ExaminerProvisionedOut, status_code=201)
def create_examiner(payload: CreateExaminerRequest, request: Request, background: BackgroundTasks,
                    db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    """Admin-only: create a new examiner account. Examiners never self-register."""
    user, activation_sent = examiner_provisioning_service.provision_examiner(
        db, admin_id=admin.id, first_name=payload.first_name, last_name=payload.last_name,
        email=payload.email, organization_name=payload.organization_name, background=background,
    )
    activity_service.record(db, activity_type=ActivityType.ACCOUNT_CREATED_BY_ADMIN,
                            subject=user, actor=admin, request=request)
    return ExaminerProvisionedOut(
        user_id=user.id, email=user.email, full_name=user.full_name,
        organization_name=payload.organization_name, activation_sent=activation_sent,
    )


@router.post("/examiners/{user_id}/resend-activation")
def resend_examiner_activation(user_id: int, request: Request, background: BackgroundTasks,
                               db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    """Admin-only recovery path for an activation link that
    expired, or an email that never arrived the first time.
    """
    sent = examiner_provisioning_service.resend_activation(db, user_id, background=background)
    activity_service.record(db, activity_type=ActivityType.ACCOUNT_CREATED_BY_ADMIN, actor=admin,
                            request=request, description=f"Resent an activation email (user {user_id})")
    return {"activation_sent": sent}


_STAFF_ROLES = {"admin", "examiner"}


def _notify_staff_login(background: BackgroundTasks, user: User, request: Request) -> None:
    """Email a staff member that their account was just signed into."""
    if not settings.NOTIFY_STAFF_ON_LOGIN:
        return
    if (user.role.name or "").lower() not in _STAFF_ROLES:
        return
    if not email_service.is_enabled():
        return

    subject, text, html = email_service.staff_login_message(
        full_name=user.full_name,
        role_label=user.role.name,
        when=datetime.now(timezone.utc).strftime("%d %b %Y, %H:%M UTC"),
        ip=client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    email_service.queue(background, to=user.email, subject=subject,
                        text_body=text, html_body=html)


@router.post("/login", response_model=TokenResponse, dependencies=[Depends(rate_limit("login"))])
def login(payload: LoginRequest, request: Request, background: BackgroundTasks,
          db: Session = Depends(get_db)):
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
    _notify_staff_login(background, user, request)
    return _token_response(user)


# --- - ---
# One-time passcode flows All four endpoints are rate limited per
# IP on top of the per-address cooldown inside otp_service.

def _require_email_capability() -> None:
    """Refuse to run an email-gated flow on a deployment that cannot send email."""
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
        code_length=settings.OTP_LENGTH,
        resend_after_seconds=settings.OTP_RESEND_COOLDOWN_SECONDS,
    )


@router.post("/register/student/verified", response_model=TokenResponse, status_code=201,
             dependencies=[Depends(rate_limit("register"))])
def register_student_verified(payload: RegisterStudentWithOtpRequest, request: Request, db: Session = Depends(get_db)):
    """Student self-registration with the emailed code checked first."""
    _require_email_capability()
    try:
        otp_service.verify_code(db, email=payload.email, purpose=OtpPurpose.SIGNUP,
                                code=payload.code, commit=False)
        user, _ = auth_service.register_student(
            db, payload.first_name, payload.last_name, payload.email, payload.password,
            payload.roll_number, email_verified=True, commit=False,
            accepted_terms=payload.accepted_terms, accepted_proctoring=payload.accepted_proctoring,
            terms_version=payload.terms_version,
        )
        db.commit()
        db.refresh(user)
    except Exception:
        # Rolls back the consumption along with everything else, so the code is
        # still there when the candidate fixes their student ID and retries.
        db.rollback()
        raise
    activity_service.record(db, activity_type=ActivityType.SIGNED_UP, subject=user, request=request,
                            description="Created their account (email verified by code)")
    return _token_response(user)


@router.post("/password-reset/check-account", response_model=AccountExistsOut,
             dependencies=[Depends(rate_limit("otp_request"))])
def check_account_exists(payload: AccountExistsCheck, db: Session = Depends(get_db)):
    """Does an active account exist for this address?"""
    from app.repositories import user_repository

    user = user_repository.get_user_by_email(db, payload.email.strip().lower())
    return AccountExistsOut(exists=bool(user and user.is_active))


@router.post("/password-reset/request", response_model=OtpRequestAccepted,
             dependencies=[Depends(rate_limit("otp_request"))])
def request_password_reset(payload: OtpRequest, background: BackgroundTasks, db: Session = Depends(get_db)):
    """Email a reset code."""
    _require_email_capability()
    otp_service.request_code(db, email=payload.email, purpose=OtpPurpose.PASSWORD_RESET, background=background)
    return OtpRequestAccepted(
        message="If an account exists for that address, a reset code is on its way.",
        expires_in_minutes=settings.OTP_TTL_MINUTES,
        code_length=settings.OTP_LENGTH,
        resend_after_seconds=settings.OTP_RESEND_COOLDOWN_SECONDS,
    )


@router.post("/password-reset/confirm", dependencies=[Depends(rate_limit("otp_verify"))])
def confirm_password_reset(payload: PasswordResetConfirmRequest, request: Request, db: Session = Depends(get_db)):
    """Verify the code and set the new password."""
    _require_email_capability()
    otp_service.verify_code(db, email=payload.email, purpose=OtpPurpose.PASSWORD_RESET, code=payload.code)
    auth_service.reset_password_by_email(db, payload.email, payload.new_password)
    activity_service.record(db, activity_type=ActivityType.PASSWORD_RESET_BY_EMAIL,
                            subject_email=payload.email.strip().lower(), request=request)
    return {"reset": True, "message": "Your password has been updated. You can sign in with it now."}


# --- - ---
# Activation An account somebody else created has no password its owner has ever chosen.

@router.post("/activate/check", dependencies=[Depends(rate_limit("otp_verify"))])
def check_activation(payload: ActivationCheck, db: Session = Depends(get_db)):
    """Is this link still good? Does not consume it."""
    try:
        otp_service.verify_code(
            db, email=payload.email, purpose=OtpPurpose.ACTIVATION,
            code=payload.token, consume=False,
        )
    except HTTPException:
        # Deliberately 200 with valid=false rather than an error status.
        return {"valid": False}
    return {"valid": True}


@router.post("/activate", response_model=TokenResponse,
             dependencies=[Depends(rate_limit("otp_verify"))])
def activate(payload: ActivationRequest, request: Request, db: Session = Depends(get_db)):
    """Set the first password on an account and sign in."""
    user = auth_service.activate_account(
        db, email=payload.email, token=payload.token, new_password=payload.password,
    )
    activity_service.record(db, activity_type=ActivityType.PASSWORD_CHANGED,
                            subject=user, request=request)
    return _token_response(user)
