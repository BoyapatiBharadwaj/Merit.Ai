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
    """The ONLY place a password hash is written.

    Stamping password_changed_at alongside the hash is what makes a password
    change revoke every existing session (see api/deps._reject_if_password_changed).
    Keeping the two together in one function is deliberate: they were previously
    going to be three separate assignments in three separate flows, and a reset
    path that updated the hash without the timestamp would look completely
    correct while silently leaving the attacker's token working.
    """
    user.hashed_password = hash_password(new_password)
    user.password_changed_at = datetime.now(timezone.utc)
    # `chosen_by_owner` is required of every caller for the same reason the
    # timestamp is written here rather than at the call sites: an admin reset
    # and a self-service change are the same column write and opposite facts
    # about who knows the secret. While must_change_password is true, at least
    # two people can sign in, so nothing the account does is attributable to
    # its owner alone -- and the login response carries the flag so the app can
    # insist on a change before anything else happens.
    user.must_change_password = not chosen_by_owner


def _require_invited(db: Session, email: str) -> None:
    """In invite-only mode, refuse an address nobody has enrolled.

    Signup was open to anyone on the internet who found the URL. For a public
    trial that is correct; for an institution running degree examinations it
    means the candidate list is whoever happened to sign up, and an examiner
    checking "is this the right cohort?" has no answer. REGISTRATION_MODE lets
    the deployment decide, and the default stays open so that trying the
    software out does not first require seeding a roster.

    "Invited" means an examiner has already put the address on an exam roster or
    an organization roster -- both of which are keyed on email precisely because
    they are written before the account exists. So the invite this checks is the
    one the institution already had to create; there is no second list to keep
    in sync, which is the usual way an allow-list drifts out of date.
    """
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
    """Create a student account.

    `commit=False` lets the caller fold this into a larger transaction --
    specifically the OTP signup endpoint, which must consume the code and create
    the account together or do neither.
    """
    email = (email or "").strip().lower()
    _require_invited(db, email)
    passwords.require(password, email=email, name=f"{first_name} {last_name}")
    if user_repository.get_user_by_email(db, email):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Email already registered.")
    # students.roll_number is unique, but only the email was ever checked. A
    # repeated student ID therefore reached the INSERT, raised IntegrityError,
    # and surfaced through the global handler as a generic 503 -- telling the
    # candidate the server was broken when the actual problem was one field they
    # could have corrected themselves.
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
        # Claim any standing invitation for this address. Enrolment normally
        # happens before the student signs up, so this is the usual path into
        # an organization; enrol_emails handles the reverse order (enrolled
        # after registering). No match simply means no organization yet, which
        # grants access to nothing rather than to everything.
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
        # examiners who type the same name join the same organization and see
        # each other's cohort, which is the intended behaviour -- it is how a
        # department with several examiners shares one student roster.
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
    """A real bcrypt hash of a value nobody can supply, hashed once per process.

    Verified against when the address has no account, so the two branches take
    the same time. Computed lazily and cached because bcrypt is deliberately
    slow -- doing it per request would make the decoy path measurably SLOWER
    than the real one and reintroduce the same oracle with the sign flipped.
    """
    return hash_password(secrets.token_urlsafe(32))


def authenticate(db: Session, email: str, password: str):
    user = user_repository.get_user_by_email(db, (email or "").strip().lower())

    # Verify against a decoy when there is no account, instead of returning
    # early. `if not user or not verify_password(...)` short-circuits, so an
    # unknown address answered in about a millisecond while a known one spent
    # ~100ms in bcrypt first -- a difference an attacker can measure remotely,
    # which turns the login form into a "does this person have an account here?"
    # oracle. On an exam platform that leaks the roster: whether a named
    # individual is a candidate at a given institution.
    #
    # The generic message was already right; it was the response TIME that
    # answered the question the message refused to.
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
    """Set a new password for `email`. Returns whether an account was found.

    Called only after otp_service.verify_code has already proved the caller
    controls that mailbox, which is what stands in for the current-password
    check `change_password` makes -- the whole premise of a reset is that the
    user no longer has the old password.

    Returns a bool rather than raising on an unknown address, deliberately. The
    endpoint above it reports success either way, because a reset form that
    404s on unregistered addresses is an account-enumeration oracle -- the same
    reason otp_service.request_code stays silent. A caller that genuinely needs
    to branch on existence still can; the HTTP layer chooses not to.
    """
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
    """Admin override -- no current-password check, because the whole point is
    that the user has lost access to it.

    Flagged as not-owner-chosen: the administrator typed this password and
    therefore knows it, so the account is forced through a change on next
    sign-in. Until then two people hold the credential, and the flag is what
    stops that state from being invisible.
    """
    try:
        _set_password(target, new_password, chosen_by_owner=False)
        db.commit()
    except Exception:
        db.rollback()
        raise


def activate_account(db: Session, *, email: str, token: str, new_password: str) -> User:
    """Turn an activation link into a password the owner chose.

    Deliberately one transaction with the token consumption. The token is marked
    spent with commit=False and the password write rides the same commit, so the
    two outcomes that would be wrong -- a burnt token with the old password
    still in place, or a set password with the link still live -- are both
    unreachable. This is the same reasoning as the verified-registration path,
    which had exactly that bug before it was folded together.

    Activating also verifies the address. It has to: the token only reached
    somebody who can read that mailbox, which is precisely what verification
    establishes. Making them prove it twice would be theatre.
    """
    email = (email or "").strip().lower()
    user = user_repository.get_user_by_email(db, email)

    # An unknown address and a wrong token get the same rejection. Saying "no
    # such account" here would turn the activation endpoint into the
    # enumeration oracle that request_code and reset_password_by_email are both
    # written to avoid.
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
            # Rewritten to the SAME sentence the unknown-address branch above
            # uses. otp_service phrases its rejection for a typed code ("that
            # code is invalid"), which is both wrong wording for a link and --
            # more importantly -- a different string. Two different rejections
            # from one endpoint is exactly the oracle the generic message
            # elsewhere exists to close: an attacker submitting a junk token
            # could tell a real account from an imaginary one by which sentence
            # came back. A test caught this by asserting the two were equal.
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
            # set_email, not a bare assignment: changing the address clears the
            # verification, which was proof about the OLD one. Assigning
            # directly would let someone verify an address they control, switch
            # to one they do not, and keep the verified state on it.
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
