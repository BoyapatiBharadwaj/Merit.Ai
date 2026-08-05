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
from app.models.enums import AdminDecision, AttemptStatus, Severity
from app.models.exam import Exam
from app.models.examiner import Examiner
from app.models.organization import ExamParticipant
from app.models.proctor_event import ProctorEvent
from app.models.student import Student
from app.models.user import User


def escape_like(value: str) -> str:
    """Neutralise LIKE wildcards in an admin search box.

    `%` and `_` are wildcards, so unescaped input becomes a pattern: searching
    for "100%" matched every row, and "%_%_%_%" is a cheap way to make the
    database scan hard. The backslash is escaped first, or escaping the other
    two would corrupt it.
    """
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _name_or_email_filter(query, search: str):
    like = f"%{escape_like(search.strip())}%"
    return query.filter(or_(User.full_name.ilike(like, escape="\\"),
                            User.email.ilike(like, escape="\\")))


def list_examiners(db: Session, search: str | None = None, organization_id: int | None = None,
                    active: bool | None = None, offset: int | None = None,
                    limit: int | None = None) -> tuple[list[Examiner], int]:
    """One page of examiners, plus the total.

    Returned the whole table before, with the browser slicing it for display --
    so an institution with a thousand staff transferred a thousand rows to show
    ten, on every filter change.
    """
    query = (db.query(Examiner)
             .join(User, Examiner.user_id == User.id)
             .options(joinedload(Examiner.user)))
    if search:
        query = _name_or_email_filter(query, search)
    if organization_id is not None:
        query = query.filter(Examiner.organization_id == organization_id)
    if active is not None:
        query = query.filter(User.is_active == active)

    total = query.order_by(None).count()
    query = query.order_by(User.full_name)
    if offset is not None:
        query = query.offset(offset)
    if limit is not None:
        query = query.limit(limit)
    return query.all(), total


def list_candidates(db: Session, search: str | None = None, organization_id: int | None = None,
                    offset: int | None = None, limit: int | None = None) -> tuple[list[Student], int]:
    query = (db.query(Student)
             .join(User, Student.user_id == User.id)
             .options(joinedload(Student.user)))
    if search:
        query = _name_or_email_filter(query, search)
    if organization_id is not None:
        query = query.filter(Student.organization_id == organization_id)

    total = query.order_by(None).count()
    query = query.order_by(User.full_name)
    if offset is not None:
        query = query.offset(offset)
    if limit is not None:
        query = query.limit(limit)
    return query.all(), total


def candidate_counts_for_examiners(db: Session, examiners: list) -> dict[int, int]:
    """Candidate counts for MANY examiners, in two queries.

    examiners_overview called candidate_ids_for_examiner inside its loop, which
    issued two queries per examiner -- a page of a hundred staff meant two
    hundred round trips to render one table, degrading linearly with exactly the
    thing an admin dashboard exists to show more of.

    Counts are computed from the same two sources as the per-examiner version:
    students enrolled in the examiner's organization, plus students invited
    directly to one of their exams.
    """
    if not examiners:
        return {}

    org_ids = {e.organization_id for e in examiners if e.organization_id}
    per_org: dict[int, int] = {}
    if org_ids:
        rows = (db.query(Student.organization_id, func.count(Student.id))
                .filter(Student.organization_id.in_(org_ids))
                .group_by(Student.organization_id).all())
        per_org = {org_id: count for org_id, count in rows}

    examiner_ids = [e.id for e in examiners]
    # DISTINCT because one student can be invited to several of an examiner's
    # exams, and counting them per invitation would inflate the figure.
    invite_rows = (db.query(Exam.examiner_id,
                            func.count(func.distinct(ExamParticipant.student_id)))
                   .join(ExamParticipant, ExamParticipant.exam_id == Exam.id)
                   .filter(Exam.examiner_id.in_(examiner_ids),
                           ExamParticipant.student_id.isnot(None))
                   .group_by(Exam.examiner_id).all())
    per_examiner_invites = {examiner_id: count for examiner_id, count in invite_rows}

    # Sum rather than union: a student both enrolled AND separately invited
    # would be counted twice here, where the per-examiner set version counted
    # them once. Slight overcount in an uncommon case, in exchange for two
    # queries instead of two hundred -- and the exact figure is available on the
    # examiner's own detail page, which loads one examiner.
    return {e.id: per_org.get(e.organization_id, 0) + per_examiner_invites.get(e.id, 0)
            for e in examiners}


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
                         exam_id: int | None = None, examiner_id: int | None = None,
                         search: str | None = None,
                         offset: int | None = None, limit: int | None = None) -> tuple[list[ProctorEvent], int]:
    """One page of violations across the platform, plus the total.

    The unbounded version of this is the worst of the admin lists: violations
    accumulate per candidate per exam forever, so it grows without limit and was
    returned in full to render fifteen rows.
    """
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
    if search and search.strip():
        # Server-side, because the page filters here. A client-side filter over
        # the current page would search fifteen rows and report "no results" for
        # a candidate sitting on page four -- worse than no search box at all.
        like = f"%{escape_like(search.strip())}%"
        query = (query
                 .join(Student, StudentExamAttempt.student_id == Student.id)
                 .join(User, Student.user_id == User.id)
                 .filter(or_(User.full_name.ilike(like, escape="\\"),
                             Exam.title.ilike(like, escape="\\"))))

    total = query.order_by(None).count()
    query = query.order_by(ProctorEvent.created_at.desc())
    if offset is not None:
        query = query.offset(offset)
    if limit is not None:
        query = query.limit(limit)
    return query.all(), total


def list_live_attempts(db: Session) -> list[StudentExamAttempt]:
    return (
        db.query(StudentExamAttempt)
        .filter(StudentExamAttempt.status == AttemptStatus.IN_PROGRESS)
        .order_by(StudentExamAttempt.started_at.desc())
        .all()
    )


def review_queue(db: Session, *, offset: int, limit: int) -> tuple[list[ProctorEvent], int]:
    """Violations waiting on a human, worst and oldest first.

    The general violations table shows everything in reverse chronological
    order, which is the wrong order for a reviewer: the newest event is rarely
    the most important, and an unreviewed high-severity flag from last week
    sinks below a week of routine tab switches. Nothing surfaced what still
    needed a decision, so in practice nothing got decided -- and an
    undecided flag counts against the candidate (see adjudicated_risk).

    Ordered by severity, then age: the oldest untouched high-severity event is
    the one a candidate has been waiting on longest.
    """
    from sqlalchemy import case

    severity_rank = case(
        (ProctorEvent.severity == Severity.HIGH, 0),
        (ProctorEvent.severity == Severity.MEDIUM, 1),
        else_=2,
    )
    query = (
        db.query(ProctorEvent)
        .join(StudentExamAttempt, ProctorEvent.attempt_id == StudentExamAttempt.id)
        .join(Exam, StudentExamAttempt.exam_id == Exam.id)
        .options(joinedload(ProctorEvent.attempt)
                 .joinedload(StudentExamAttempt.student)
                 .joinedload(Student.user))
        .filter(ProctorEvent.admin_decision == AdminDecision.PENDING)
    )
    total = query.order_by(None).count()
    rows = (query.order_by(severity_rank, ProctorEvent.created_at.asc())
            .offset(offset).limit(limit).all())
    return rows, total


def organizations_overview(db: Session) -> list[dict]:
    """Every organization with the counts an administrator needs to act on.

    Organizations are the tenancy boundary -- they decide which candidates an
    examiner sees and which exams a student can sit -- and there was no page
    showing which existed or what was in them. Aggregated in four grouped
    queries rather than a loop per organization.
    """
    from app.models.organization import Organization, OrganizationMember

    orgs = db.query(Organization).order_by(Organization.name).all()
    if not orgs:
        return []

    def _counts(query):
        return {key: value for key, value in query.all()}

    examiners = _counts(db.query(Examiner.organization_id, func.count(Examiner.id))
                        .group_by(Examiner.organization_id))
    students = _counts(db.query(Student.organization_id, func.count(Student.id))
                       .group_by(Student.organization_id))
    exams = _counts(db.query(Exam.organization_id, func.count(Exam.id))
                    .group_by(Exam.organization_id))
    # Roster entries with no student account yet: people invited but not
    # registered. Counted separately because "50 on the roster, 12 registered"
    # is the number an administrator chasing enrolment actually wants.
    pending = _counts(db.query(OrganizationMember.organization_id, func.count(OrganizationMember.id))
                      .filter(OrganizationMember.student_id.is_(None))
                      .group_by(OrganizationMember.organization_id))

    return [{
        "id": org.id,
        "name": org.name,
        "examiner_count": examiners.get(org.id, 0),
        "candidate_count": students.get(org.id, 0),
        "exam_count": exams.get(org.id, 0),
        "pending_invites": pending.get(org.id, 0),
        "created_at": org.created_at,
    } for org in orgs]
