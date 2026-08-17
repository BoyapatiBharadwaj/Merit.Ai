"""Data access for User, Role, Student, Examiner tables."""
from datetime import datetime, timezone

from sqlalchemy.orm import Session
from app.models.user import User, Role
from app.models.student import Student
from app.models.examiner import Examiner
from app.models.enums import RoleName


def get_role_by_name(db: Session, name: str) -> Role | None:
    return db.query(Role).filter(Role.name == name).first()


def get_user_by_email(db: Session, email: str) -> User | None:
    return db.query(User).filter(User.email == email).first()


def get_user_by_id(db: Session, user_id: int) -> User | None:
    return db.query(User).filter(User.id == user_id).first()


def list_admin_emails(db: Session) -> list[str]:
    """Email addresses of every active admin."""
    rows = (
        db.query(User.email)
        .join(Role, User.role_id == Role.id)
        .filter(Role.name == RoleName.ADMIN.value, User.is_active.is_(True))
        .all()
    )
    return [email for (email,) in rows if email]


def create_user(db: Session, first_name: str, last_name: str, email: str, hashed_password: str,
                role_id: int, commit: bool = True, email_verified: bool = False) -> User:
    now = datetime.now(timezone.utc)
    user = User(email=(email or "").strip().lower(), hashed_password=hashed_password, role_id=role_id)
    user.set_name(first_name, last_name)
    # Stamped at creation, not left NULL.
    user.password_changed_at = now
    # Only ever set by a caller that actually checked a code (see
    # auth_service.register_student).
    user.email_verified_at = now if email_verified else None
    db.add(user)
    db.flush()
    if commit:
        db.commit()
        db.refresh(user)
    return user


def create_student_profile(db: Session, user_id: int, roll_number: str | None, commit: bool = True) -> Student:
    student = Student(user_id=user_id, roll_number=roll_number)
    db.add(student)
    db.flush()
    if commit:
        db.commit()
        db.refresh(student)
    return student


def create_examiner_profile(db: Session, user_id: int, organization_name: str | None, created_by_admin_id: int, commit: bool = True) -> Examiner:
    examiner = Examiner(user_id=user_id, organization_name=organization_name, created_by_admin_id=created_by_admin_id)
    db.add(examiner)
    db.flush()
    if commit:
        db.commit()
        db.refresh(examiner)
    return examiner


def get_student_by_roll_number(db: Session, roll_number: str) -> Student | None:
    """Used to reject a duplicate student ID with a 409 that names the field,
    rather than letting the unique constraint raise an IntegrityError that the
    global handler turns into an opaque 503."""
    return db.query(Student).filter(Student.roll_number == roll_number).first()


def get_student_by_user_id(db: Session, user_id: int) -> Student | None:
    return db.query(Student).filter(Student.user_id == user_id).first()


def get_examiner_by_user_id(db: Session, user_id: int) -> Examiner | None:
    return db.query(Examiner).filter(Examiner.user_id == user_id).first()


def list_students(db: Session) -> list[Student]:
    return db.query(Student).all()


def list_examiners(db: Session) -> list[Examiner]:
    return db.query(Examiner).all()


def get_student_by_id(db: Session, student_id: int) -> Student | None:
    return db.query(Student).filter(Student.id == student_id).first()


def get_examiner_by_id(db: Session, examiner_id: int) -> Examiner | None:
    return db.query(Examiner).filter(Examiner.id == examiner_id).first()