"""Organization membership and exam access control."""
import re
from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session, joinedload

from app.models.enums import ExamStatus
from app.models.exam import Exam, Section
from app.models.organization import ExamParticipant, Organization, OrganizationMember
from app.models.student import Student
from app.models.user import User

# Deliberately identical for "this exam does not exist" and "this exam exists but is not yours".
NO_ACCESS_DETAIL = "Exam not available."


def normalize_email(email: str) -> str:
    return (email or "").strip().lower()


# Deliberately permissive. This is a roster key, not an identity claim.
_EMAIL_SHAPE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _looks_like_email(email: str) -> bool:
    return bool(email) and len(email) <= 150 and bool(_EMAIL_SHAPE.match(email))


# --- - ---
# Organizations -------------------------------------------------------------------------

def get_by_id(db: Session, organization_id: int) -> Organization | None:
    return db.query(Organization).filter(Organization.id == organization_id).first()


def get_or_create(db: Session, name: str, commit: bool = True) -> Organization:
    """Find an organization by name, or create it. Names are matched
    case-insensitively and stored trimmed, so "Acme", "acme" and "Acme "
    resolve to one tenant rather than three."""
    cleaned = (name or "").strip()
    if not cleaned:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Organization name is required.")

    # func.lower(...) == , NOT ilike().
    existing = (db.query(Organization)
                .filter(func.lower(Organization.name) == cleaned.lower())
                .first())
    if existing:
        return existing

    organization = Organization(name=cleaned)
    db.add(organization)
    db.flush()  # assign an id without ending the caller's transaction
    if commit:
        db.commit()
        db.refresh(organization)
    return organization


def list_organizations(db: Session) -> list[Organization]:
    return db.query(Organization).order_by(Organization.name).all()


# --- - ---
# Roster -------------------------------------------------------------------------

def link_student(db: Session, student: Student, email: str, commit: bool = True) -> Student:
    """Resolve and cache which organization a student belongs to."""
    membership = (db.query(OrganizationMember)
                  .filter(OrganizationMember.email == normalize_email(email))
                  .order_by(OrganizationMember.created_at, OrganizationMember.id)
                  .first())
    if membership is None:
        return student

    if membership.student_id is None:
        membership.student_id = student.id
        membership.linked_at = datetime.now(timezone.utc)
    if student.organization_id is None:
        student.organization_id = membership.organization_id

    if commit:
        db.commit()
        db.refresh(student)
    return student


def enrol_emails(db: Session, organization_id: int, emails: list[str],
                 invited_by_id: int | None) -> dict:
    """Add email addresses to an organization's roster."""
    added, already_present, linked, invalid, elsewhere = [], [], [], [], []

    for raw in emails:
        email = normalize_email(raw)
        if not _looks_like_email(email):
            if raw and raw.strip():
                invalid.append(raw.strip()[:150])
            continue

        existing = (db.query(OrganizationMember)
                    .filter(OrganizationMember.organization_id == organization_id,
                            OrganizationMember.email == email)
                    .first())
        if existing:
            already_present.append(email)
            continue

        member = OrganizationMember(organization_id=organization_id, email=email,
                                    invited_by_id=invited_by_id)
        db.add(member)
        db.flush()
        added.append(email)

        # If this person already registered before being enrolled, link them
        # now -- otherwise they would have to re-register to gain access.
        student = (db.query(Student)
                   .join(Student.user)
                   .filter(Student.user.has(email=email))
                   .first())
        if student:
            member.student_id = student.id
            member.linked_at = datetime.now(timezone.utc)
            if student.organization_id is None:
                student.organization_id = organization_id
            if student.organization_id == organization_id:
                linked.append(email)
            else:
                # Already a member of a different organization. The roster
                # row exists, but link_student deliberately keeps the
                # earliest membership, so this enrolment grants nothing.
                elsewhere.append(email)

    db.commit()
    return {"added": added, "already_present": already_present, "linked": linked,
            "invalid": invalid, "in_another_organization": elsewhere}


def remove_member(db: Session, organization_id: int, member_id: int) -> None:
    """Remove a roster entry and, if it was the source of that student's membership, revoke it."""
    member = (db.query(OrganizationMember)
              .filter(OrganizationMember.id == member_id,
                      OrganizationMember.organization_id == organization_id)
              .first())
    if not member:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Roster entry not found.")

    student = member.student
    db.delete(member)
    db.flush()

    if student is not None and student.organization_id == organization_id:
        # Fall back to any other organization that still lists them, so removing a secondary
        # enrolment does not strip a student of their primary one.
        fallback = (db.query(OrganizationMember)
                    .filter(OrganizationMember.email == normalize_email(student.user.email))
                    .order_by(OrganizationMember.created_at, OrganizationMember.id)
                    .first())
        student.organization_id = fallback.organization_id if fallback else None

    db.commit()


def list_members(db: Session, organization_id: int) -> list[OrganizationMember]:
    return (db.query(OrganizationMember)
            .filter(OrganizationMember.organization_id == organization_id)
            .order_by(OrganizationMember.email)
            .all())


# ---------------------------------------------------------------------------
# Access control -- the single authority
# ---------------------------------------------------------------------------

def is_invited(db: Session, email: str) -> bool:
    """Has anyone enrolled this address, anywhere?"""
    address = normalize_email(email)
    if not address:
        return False

    member = db.query(OrganizationMember.id).filter(OrganizationMember.email == address).first()
    if member is not None:
        return True
    participant = db.query(ExamParticipant.id).filter(ExamParticipant.email == address).first()
    return participant is not None


def can_student_access_exam(db: Session, student: Student, exam: Exam) -> bool:
    """Is this student permitted to see and sit this exam?"""
    if student is None or exam is None:
        return False
    if exam.organization_id is None:
        return False

    restricted = (db.query(ExamParticipant.id)
                  .filter(ExamParticipant.exam_id == exam.id)
                  .first() is not None)

    if restricted:
        # Match on id OR email. The email arm is what makes a pending invite work the moment its
        # owner registers, without depending on a resolution step having run first.
        return (db.query(ExamParticipant.id)
                .filter(ExamParticipant.exam_id == exam.id,
                        or_(ExamParticipant.student_id == student.id,
                            ExamParticipant.email == normalize_email(student.user.email)))
                .first() is not None)

    if student.organization_id is None:
        return False
    return exam.organization_id == student.organization_id


def require_student_access(db: Session, student: Student, exam: Exam) -> None:
    """can_student_access_exam, but raising the deliberately vague 404."""
    if not can_student_access_exam(db, student, exam):
        raise HTTPException(status.HTTP_404_NOT_FOUND, NO_ACCESS_DETAIL)


def examiner_can_view_student(db: Session, examiner, student: Student) -> bool:
    """May this examiner view this student's identity materials (registered face photo, ID card photo)?"""
    if examiner is None or student is None:
        return False
    if examiner.organization_id is not None and student.organization_id == examiner.organization_id:
        return True
    return (db.query(ExamParticipant.id)
            .join(Exam, Exam.id == ExamParticipant.exam_id)
            .filter(Exam.examiner_id == examiner.id,
                    or_(ExamParticipant.student_id == student.id,
                        ExamParticipant.email == normalize_email(student.user.email)))
            .first() is not None)


def list_accessible_published_exams(db: Session, student: Student) -> list[Exam]:
    """The student-facing exam list, filtered at the database rather than in Python -- a cohort
    of thousands should not load every exam in the system to discard most of them.
    """
    if student is None:
        return []

    restricted_exam_ids = db.query(ExamParticipant.exam_id).distinct().subquery()
    # Same id-or-email match as can_student_access_exam, so the list and the
    # gate cannot disagree about a pending invite.
    allowed_for_student = (db.query(ExamParticipant.exam_id)
                           .filter(or_(ExamParticipant.student_id == student.id,
                                       ExamParticipant.email == normalize_email(student.user.email)))
                           .subquery())

    # Two independent routes onto this list, mirroring can_student_access_exam.
    return (db.query(Exam)
            # serialize_exam_for_candidate reads exam.sections[*].questions (for
            # question_count/exam_total_marks) for every row in this list.
            .options(joinedload(Exam.sections).joinedload(Section.questions))
            .filter(Exam.status == ExamStatus.PUBLISHED)
            .filter(or_(
                and_(Exam.organization_id == student.organization_id,
                     Exam.id.notin_(db.query(restricted_exam_ids.c.exam_id))),
                Exam.id.in_(db.query(allowed_for_student.c.exam_id)),
            ))
            .order_by(Exam.created_at.desc())
            .all())


# ---------------------------------------------------------------------------
# Per-exam participant list
# ---------------------------------------------------------------------------

def add_exam_participants(db: Session, exam: Exam, emails: list[str],
                          added_by_id: int | None) -> dict:
    """Add emails to an exam's allow-list."""
    added, already_present, invalid, not_in_organization = [], [], [], []

    roster_emails = {m.email for m in list_members(db, exam.organization_id)}

    # Grandfathering, before anything is added. An exam with no participant rows is open to the
    # whole organization; the FIRST row flips it into allow-list mode.
    was_unrestricted = not db.query(ExamParticipant.id).filter(
        ExamParticipant.exam_id == exam.id).first()
    if was_unrestricted:
        emails = list(emails) + _emails_of_students_with_attempts(db, exam)

    for raw in emails:
        email = normalize_email(raw)
        if not _looks_like_email(email):
            if raw and raw.strip():
                invalid.append(raw.strip()[:150])
            continue

        existing = (db.query(ExamParticipant)
                    .filter(ExamParticipant.exam_id == exam.id,
                            ExamParticipant.email == email)
                    .first())
        if existing:
            already_present.append(email)
            continue

        participant = ExamParticipant(exam_id=exam.id, email=email, added_by_id=added_by_id)
        student = (db.query(Student).join(Student.user)
                   .filter(Student.user.has(email=email)).first())
        if student is not None:
            participant.student_id = student.id
            participant.linked_at = datetime.now(timezone.utc)
        db.add(participant)
        added.append(email)

        if email not in roster_emails:
            not_in_organization.append(email)

    db.commit()
    return {"added": added, "already_present": already_present, "invalid": invalid,
            "not_in_organization": not_in_organization}


def _emails_of_students_with_attempts(db: Session, exam: Exam) -> list[str]:
    """Addresses of everyone who has started this exam and not been reset."""
    from app.models.attempt import StudentExamAttempt

    rows = (db.query(User.email)
            .join(Student, Student.user_id == User.id)
            .join(StudentExamAttempt, StudentExamAttempt.student_id == Student.id)
            .filter(StudentExamAttempt.exam_id == exam.id,
                    StudentExamAttempt.archived_at.is_(None))
            .distinct()
            .all())
    return [normalize_email(email) for (email,) in rows if email]


def remove_exam_participant(db: Session, exam: Exam, participant_id: int) -> None:
    participant = (db.query(ExamParticipant)
                   .filter(ExamParticipant.id == participant_id,
                           ExamParticipant.exam_id == exam.id)
                   .first())
    if not participant:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Participant not found.")

    # Removing someone who has already sat, or is sitting,
    # this exam is not an invitation being withdrawn.
    if participant.email in set(_emails_of_students_with_attempts(db, exam)):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This candidate has already started this exam, so they cannot be removed from the "
            "list -- it would cut off an attempt in progress or hide a result from the person "
            "who earned it. Reset their attempt first if you need to undo it.",
        )
    db.delete(participant)
    db.commit()


def clear_exam_participants(db: Session, exam: Exam) -> None:
    """Reopen the exam to the whole organization."""
    db.query(ExamParticipant).filter(ExamParticipant.exam_id == exam.id).delete()
    db.commit()


def link_exam_participants(db: Session, student: Student, email: str, commit: bool = True) -> None:
    """Fill in student_id on any pending invites for this address."""
    pending = (db.query(ExamParticipant)
               .filter(ExamParticipant.email == normalize_email(email),
                       ExamParticipant.student_id.is_(None))
               .all())
    for participant in pending:
        participant.student_id = student.id
        participant.linked_at = datetime.now(timezone.utc)
    if commit and pending:
        db.commit()


def list_exam_participants(db: Session, exam_id: int) -> list[ExamParticipant]:
    return (db.query(ExamParticipant)
            .filter(ExamParticipant.exam_id == exam_id)
            .order_by(ExamParticipant.id)
            .all())


def list_organization_students(db: Session, organization_id: int) -> list[Student]:
    """Students with real accounts in this organization -- what an examiner
    picks from when restricting an exam."""
    return (db.query(Student)
            .filter(Student.organization_id == organization_id)
            .join(Student.user)
            .order_by(Student.id)
            .all())
