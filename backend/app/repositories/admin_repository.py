"""
Data access for the admin dashboard's cross-organization drill-down views
(examiners, candidates, exams, live sessions, violations).

Every query here is admin-scoped by the caller (api/v1/admin.py gates the
whole router on require_admin) -- nothing in this module applies its own
tenancy filtering, unlike organization_service, because an admin is the one
role meant to see across every organization at once.
"""
from sqlalchemy import func, or_
from sqlalchemy.orm import Session, joinedload

from app.models.attempt import StudentExamAttempt
from app.models.enums import AttemptStatus
from app.models.exam import Exam
from app.models.examiner import Examiner
from app.models.organization import ExamParticipant
from app.models.proctor_event import ProctorEvent
from app.models.student import Student
from app.models.user import User


def list_examiners(db: Session, search: str | None = None, organization_id: int | None = None,
                    active: bool | None = None) -> list[Examiner]:
    query = db.query(Examiner).join(User, Examiner.user_id == User.id)
    if search:
        like = f"%{search.strip()}%"
        query = query.filter(or_(User.full_name.ilike(like), User.email.ilike(like)))
    if organization_id is not None:
        query = query.filter(Examiner.organization_id == organization_id)
    if active is not None:
        query = query.filter(User.is_active == active)
    return query.order_by(User.full_name).all()


def list_candidates(db: Session, search: str | None = None, organization_id: int | None = None) -> list[Student]:
    query = db.query(Student).join(User, Student.user_id == User.id)
    if search:
        like = f"%{search.strip()}%"
        query = query.filter(or_(User.full_name.ilike(like), User.email.ilike(like)))
    if organization_id is not None:
        query = query.filter(Student.organization_id == organization_id)
    return query.order_by(User.full_name).all()


def list_exams_for_examiners(db: Session, examiner_ids: list[int]) -> list[Exam]:
    if not examiner_ids:
        return []
    return db.query(Exam).filter(Exam.examiner_id.in_(examiner_ids)).all()


def candidate_ids_for_examiner(db: Session, examiner: Examiner) -> set[int]:
    """Distinct real student accounts connected to this examiner: enrolled in
    their organization, or added as a participant on one of their exams --
    the latter covers a per-exam invite from outside the organization (see
    organization_service.can_student_access_exam), which is a real
    "candidate" of this examiner's even though the roster never enrolled
    them. A pending invite with no account yet has no student row and is
    not counted -- this is a count of actual candidate accounts.
    """
    ids: set[int] = set()
    if examiner.organization_id:
        ids.update(sid for (sid,) in db.query(Student.id)
                   .filter(Student.organization_id == examiner.organization_id).all())
    ids.update(sid for (sid,) in db.query(ExamParticipant.student_id)
               .join(Exam, Exam.id == ExamParticipant.exam_id)
               .filter(Exam.examiner_id == examiner.id, ExamParticipant.student_id.isnot(None)).all())
    return ids


def exam_type_label(exam: Exam) -> str:
    """"Coding" if any question in the exam is a coding question (the more
    demanding kind), else "MCQ" if it has any questions at all, else "-" for
    an exam still empty of content -- an exam can mix section types, so this
    reports its most notable kind rather than every combination present."""
    has_any = False
    for section in exam.sections:
        for question in section.questions:
            has_any = True
            if question.question_type.value == "coding":
                return "Coding"
    return "MCQ" if has_any else "-"


def expected_students_for_exam(db: Session, exam: Exam) -> list[Student]:
    """Every student who could take this exam -- the exam-level analogue of
    organization_service.can_student_access_exam, returning the whole
    eligible set instead of checking one person. A restricted exam
    (participant rows present) uses exactly that allow-list, resolved to
    real accounts only; an unrestricted exam uses the organization roster.
    """
    # joinedload(user): every caller reads student.user.full_name / .email while
    # building a row, and `user` is a lazy relationship -- so without this the
    # loop emits one extra SELECT per candidate purely to fetch a name. Eager-
    # loading it here fixes that for all callers at once rather than leaving
    # each list endpoint to remember.
    base = db.query(Student).options(joinedload(Student.user))

    restricted = db.query(ExamParticipant.id).filter(ExamParticipant.exam_id == exam.id).first() is not None
    if restricted:
        ids = [sid for (sid,) in db.query(ExamParticipant.student_id)
               .filter(ExamParticipant.exam_id == exam.id, ExamParticipant.student_id.isnot(None)).all()]
        if not ids:
            return []
        return base.filter(Student.id.in_(ids)).all()
    if not exam.organization_id:
        return []
    return base.filter(Student.organization_id == exam.organization_id).all()


def violation_counts_for_attempts(db: Session, attempt_ids: list[int]) -> dict[int, int]:
    if not attempt_ids:
        return {}
    rows = (
        db.query(ProctorEvent.attempt_id, func.count(ProctorEvent.id))
        .filter(ProctorEvent.attempt_id.in_(attempt_ids))
        .group_by(ProctorEvent.attempt_id)
        .all()
    )
    return dict(rows)


def list_all_violations(db: Session, severity: str | None = None, decision: str | None = None,
                         exam_id: int | None = None, examiner_id: int | None = None) -> list[ProctorEvent]:
    query = (
        db.query(ProctorEvent)
        .join(StudentExamAttempt, ProctorEvent.attempt_id == StudentExamAttempt.id)
        .join(Exam, StudentExamAttempt.exam_id == Exam.id)
    )
    if severity:
        query = query.filter(ProctorEvent.severity == severity)
    if decision:
        query = query.filter(ProctorEvent.admin_decision == decision)
    if exam_id:
        query = query.filter(Exam.id == exam_id)
    if examiner_id:
        query = query.filter(Exam.examiner_id == examiner_id)
    return query.order_by(ProctorEvent.created_at.desc()).all()


def list_live_attempts(db: Session) -> list[StudentExamAttempt]:
    return (
        db.query(StudentExamAttempt)
        .filter(StudentExamAttempt.status == AttemptStatus.IN_PROGRESS)
        .order_by(StudentExamAttempt.started_at.desc())
        .all()
    )
