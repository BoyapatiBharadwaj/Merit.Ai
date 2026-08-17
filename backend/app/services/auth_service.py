"""Business logic for registration, login, and account administration."""
import secrets
from datetime import datetime, timezone
from functools import lru_cache

from sqlalchemy.orm import Session
from fastapi import HTTPException, status

from app.core import passwords
from app.core.config import settings
from app.core.security import hash_password, verify_password, create_access_token
from app.repositories import user_repository
from app.models.enums import RoleName
from app.models.user import User
from app.models.otp import OtpPurpose
from app.services import organization_service, otp_service


def _set_password(user: User, new_password: str, *, chosen_by_owner: bool = True) -> None:
    """The ONLY place a password hash is written."""
    user.hashed_password = hash_password(new_password)
    user.password_changed_at = datetime.now(timezone.utc)
    # `chosen_by_owner` is required of every caller for the same reason the timestamp is written
    # here rather than at the call sites.
    user.must_change_password = not chosen_by_owner


def _require_invited(db: Session, email: str) -> None:
    """In invite-only mode, refuse an address nobody has enrolled."""
    if (settings.REGISTRATION_MODE or "open").strip().lower() != "invite":
        return
    if organization_service.is_invited(db, email):
        return
    raise HTTPException(
        status.HTTP_403_FORBIDDEN,
        "This address has not been invited. Registration on this server is limited to "
        "candidates an examiner has already added to an exam or organization. "
        "Contact your institution if you believe this is a mistake.",
    )


def register_student(db: Session, first_name: str, last_name: str, email: str, password: str,
                     roll_number: str | None, *, email_verified: bool = False, commit: bool = True,
                     accepted_terms: bool = False, accepted_proctoring: bool = False,
                     terms_version: str | None = None):
    """Create a student account."""
    email = (email or "").strip().lower()
    _require_invited(db, email)
    passwords.require(password, email=email, name=f"{first_name} {last_name}")
    if user_repository.get_user_by_email(db, email):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Email already registered.")
    # students.roll_number is unique, but only the email was ever checked.
    if roll_number and roll_number.strip():
        roll_number = roll_number.strip()
        if user_repository.get_student_by_roll_number(db, roll_number):
            raise HTTPException(status.HTTP_409_CONFLICT,
                                "That student ID is already registered.")
    else:
        roll_number = None
    if settings.REQUIRE_CONSENT_ON_SIGNUP and not (accepted_terms and accepted_proctoring):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "You must accept the Terms of Service and the proctoring notice to create an account.",
        )
    role = user_repository.get_role_by_name(db, RoleName.STUDENT.value)
    if not role:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Student role not seeded.")
    try:
        user = user_repository.create_user(db, first_name, last_name, email, hash_password(password),
                                           role.id, commit=False, email_verified=email_verified)
        now = datetime.now(timezone.utc)
        if accepted_terms:
            user.terms_accepted_at = now
            user.terms_version = terms_version
        if accepted_proctoring:
            user.proctoring_consent_at = now
        student = user_repository.create_student_profile(db, user.id, roll_number, commit=False)
        db.flush()  # student.id must exist before the roster row can point at it
        # Claim any standing invitation for this address. Enrolment normally happens before the
        # student signs up, so this is the usual path into an organization.
        organization_service.link_student(db, student, email, commit=False)
        # Also resolve any per-exam invites waiting on this address, so the
        # examiner's list shows "Registered" rather than a stale "Invited".
        organization_service.link_exam_participants(db, student, email, commit=False)
        if commit:
            db.commit()
            db.refresh(user)
            db.refresh(student)
        return user, student
    except Exception:
        if commit:
            db.rollback()
        raise


def create_examiner(db: Session, admin_user_id: int, first_name: str, last_name: str, email: str, password: str,
                     organization_name: str | None, commit: bool = True):
    """commit=False lets a caller (e.g. access_request_service.approve) fold
    this account creation into a larger transaction, so the account and
    whatever else the caller commits alongside it succeed or fail together
    instead of the account silently existing while the rest rolls back."""
    email = (email or "").strip().lower()
    _require_invited(db, email)
    passwords.require(password, email=email, name=f"{first_name} {last_name}")
    if user_repository.get_user_by_email(db, email):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Email already registered.")
    role = user_repository.get_role_by_name(db, RoleName.EXAMINER.value)
    if not role:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Examiner role not seeded.")
    try:
        user = user_repository.create_user(db, first_name, last_name, email, hash_password(password), role.id, commit=False)
        examiner = user_repository.create_examiner_profile(db, user.id, organization_name, admin_user_id, commit=False)
        # Resolve the typed organization name to a real tenant row. Two
        # examiners who type the same name join the same organization and
        # see each other's cohort, which is the intended behaviour.
        if organization_name and organization_name.strip():
            organization = organization_service.get_or_create(db, organization_name, commit=False)
            examiner.organization_id = organization.id
        if commit:
            db.commit()
            db.refresh(user)
            db.refresh(examiner)
        return user, examiner
    except Exception:
        db.rollback()
        raise


@lru_cache(maxsize=1)
def _decoy_hash() -> str:
    """A real bcrypt hash of a value nobody can supply, hashed once per process."""
    return hash_password(secrets.token_urlsafe(32))


def authenticate(db: Session, email: str, password: str):
    user = user_repository.get_user_by_email(db, (email or "").strip().lower())

    # Verify against a decoy when there is no account, instead of returning early.
    if user is None:
        verify_password(password, _decoy_hash())
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password.")

    if not verify_password(password, user.hashed_password):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password.")
    if not user.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Account is deactivated.")

    token = create_access_token(subject=str(user.id), role=user.role.name, user=user)
    return token, user


def change_password(db: Session, user: User, current_password: str, new_password: str) -> None:
    """Self-service password change. Deliberately re-checks the current
    password even though the caller is already authenticated -- a stolen
    session should not be enough to lock the real owner out."""
    if not verify_password(current_password, user.hashed_password):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Current password is incorrect.")
    if verify_password(new_password, user.hashed_password):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "New password must be different from the current one.")
    try:
        _set_password(user, new_password)
        db.commit()
    except Exception:
        db.rollback()
        raise


def reset_password_by_email(db: Session, email: str, new_password: str) -> bool:
    """Set a new password for `email`. Returns whether an account was found."""
    user = user_repository.get_user_by_email(db, email.lower())
    if not user:
        return False
    if not user.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Account is deactivated.")
    try:
        _set_password(user, new_password)
        db.commit()
    except Exception:
        db.rollback()
        raise
    return True


def admin_reset_password(db: Session, target: User, new_password: str) -> None:
    """Admin override -- no current-password check, because the
    whole point is that the user has lost access to it.
    """
    try:
        _set_password(target, new_password, chosen_by_owner=False)
        db.commit()
    except Exception:
        db.rollback()
        raise


def activate_account(db: Session, *, email: str, token: str, new_password: str) -> User:
    """Turn an activation link into a password the owner chose."""
    email = (email or "").strip().lower()
    user = user_repository.get_user_by_email(db, email)

    # An unknown address and a wrong token get the same rejection.
    generic = "That activation link is invalid or has expired. Ask for a new one."
    if not user:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, generic)
    if not user.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Account is deactivated.")

    passwords.require(new_password, email=email, name=user.full_name)

    try:
        try:
            otp_service.verify_code(
                db, email=email, purpose=OtpPurpose.ACTIVATION, code=token, commit=False,
            )
        except HTTPException as exc:
            # Rewritten to the SAME sentence the unknown-address branch above uses. otp_service
            # phrases its rejection for a typed code ("that code is invalid"), which is both
            # wrong wording for a link and.
            if exc.status_code == status.HTTP_400_BAD_REQUEST:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, generic) from exc
            raise
        _set_password(user, new_password, chosen_by_owner=True)
        if user.email_verified_at is None:
            user.email_verified_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(user)
    except Exception:
        db.rollback()
        raise
    return user


def update_profile(db: Session, user: User, first_name: str | None, last_name: str | None, email: str | None) -> User:
    """Name/email edit, blocked once a student's identity has been locked by
    face + ID verification. Admins and examiners are never locked."""
    student = user.student_profile
    if student and student.identity_locked:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Your name and email are locked because your face and ID card have been verified. "
            "You can still change your password. Contact an administrator if a correction is needed.",
        )

    if email is not None:
        email = email.strip().lower()
        existing = user_repository.get_user_by_email(db, email)
        if existing and existing.id != user.id:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "That email is already in use.")

    try:
        if first_name is not None or last_name is not None:
            user.set_name(
                first_name if first_name is not None else user.first_name,
                last_name if last_name is not None else user.last_name,
            )
        if email is not None:
            # set_email, not a bare assignment: changing the address clears the verification,
            # which was proof about the OLD one.
            user.set_email(email)
        db.commit()
        db.refresh(user)
        return user
    except Exception:
        db.rollback()
        raise


def set_active(db: Session, target: User, is_active: bool) -> User:
    try:
        target.is_active = is_active
        db.commit()
        db.refresh(target)
        return target
    except Exception:
        db.rollback()
        raise
