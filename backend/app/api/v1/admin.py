"""
Admin dashboard drill-down endpoints: examiners, candidates, exams, live
sessions, and violations across every organization.

Every route here is require_admin -- this is deliberately the one corner of
the API that sees across tenants at once. Every other read path in this app
(organization_service, the examiner's own dashboards) stays scoped to one
organization or one examiner on purpose; this router is where an admin's
platform-wide view lives instead of being bolted onto those scoped routers.
"""
from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.api.deps import require_admin
from app.models.activity_log import ActivityType
from app.models.user import User
from app.database.session import get_db
from app.schemas.pagination import Page, PageParams, build_page
from app.schemas.admin import ExaminerUpdateRequest
from app.services import activity_service, admin_service, organization_service

router = APIRouter(prefix="/admin", tags=["Admin Dashboard"])


@router.get("/organizations")
def list_organizations(db: Session = Depends(get_db), _=Depends(require_admin)):
    """Id + name only, for the Examiners/Candidates pages' organization
    filter dropdown."""
    return [{"id": o.id, "name": o.name} for o in organization_service.list_organizations(db)]


@router.get("/summary")
def get_dashboard_summary(db: Session = Depends(get_db), _=Depends(require_admin)):
    """Counts for the seven admin overview cards."""
    return admin_service.dashboard_summary(db)


# ---------------------------------------------------------------------------
# Exams (platform-wide list, for the Active/Upcoming/Completed cards)
# ---------------------------------------------------------------------------

@router.get("/exams")
def list_exams(status: str | None = None, search: str | None = None,
               db: Session = Depends(get_db), _=Depends(require_admin)):
    return admin_service.exams_overview(db, status_filter=status, search=search)


# ---------------------------------------------------------------------------
# Examiners
# ---------------------------------------------------------------------------

@router.get("/examiners", response_model=Page[dict])
def list_examiners(params: PageParams = Depends(), search: str | None = None,
                   organization_id: int | None = None,
                   status: str | None = Query(default=None, pattern="^(active|disabled)$"),
                   db: Session = Depends(get_db), _=Depends(require_admin)):
    """One page of examiners. See app/schemas/pagination.py for why these lists
    are paginated at all -- the whole table used to be sent and sliced in the
    browser."""
    rows, total = admin_service.examiners_overview(
        db, search=search, organization_id=organization_id, status=status,
        offset=params.offset, limit=params.page_size)
    return build_page(rows, total, params)


@router.get("/examiners/{examiner_id}")
def get_examiner_detail(examiner_id: int, db: Session = Depends(get_db), _=Depends(require_admin)):
    return admin_service.examiner_detail(db, examiner_id)


@router.get("/examiners/{examiner_id}/exams")
def get_examiner_exams(examiner_id: int, status: str | None = None,
                        db: Session = Depends(get_db), _=Depends(require_admin)):
    return admin_service.examiner_exams(db, examiner_id, status_filter=status)


@router.patch("/examiners/{examiner_id}")
def update_examiner(examiner_id: int, payload: ExaminerUpdateRequest, request: Request,
                    db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    """Edit an examiner, and record what actually changed.

    The audit trail covered account creation, password resets and deletions but
    not edits -- so "who moved this examiner into our organization, and when?"
    was unanswerable, which is exactly the question an unexpected tenancy change
    provokes. The BEFORE values come back from the service rather than being
    read here, so what is recorded is what changed rather than what was asked
    for.
    """
    result = admin_service.update_examiner(db, examiner_id, **payload.model_dump(exclude_unset=True))
    before = result.pop("previous", {})
    if before.get("organization_id") != result.get("organization_id"):
        activity_service.record(
            db, activity_type=ActivityType.EXAMINER_UPDATED,
            subject=admin_service.examiner_user(db, examiner_id), actor=admin, request=request,
            description=(f"Organization changed from "
                         f"{before.get('organization_name') or 'none'} to "
                         f"{result.get('organization_name') or 'none'}"),
        )
    return result


@router.delete("/examiners/{examiner_id}", status_code=204)
def delete_examiner(examiner_id: int, db: Session = Depends(get_db), _=Depends(require_admin)):
    admin_service.delete_examiner(db, examiner_id)


# ---------------------------------------------------------------------------
# Candidates
# ---------------------------------------------------------------------------

@router.get("/candidates", response_model=Page[dict])
def list_candidates(params: PageParams = Depends(), search: str | None = None,
                    organization_id: int | None = None,
                    db: Session = Depends(get_db), _=Depends(require_admin)):
    rows, total = admin_service.candidates_overview(
        db, search=search, organization_id=organization_id,
        offset=params.offset, limit=params.page_size)
    return build_page(rows, total, params)


@router.get("/candidates/{student_id}")
def get_candidate_detail(student_id: int, db: Session = Depends(get_db), _=Depends(require_admin)):
    return admin_service.candidate_detail(db, student_id)


# ---------------------------------------------------------------------------
# Exams
# ---------------------------------------------------------------------------

@router.get("/exams/{exam_id}")
def get_exam_detail(exam_id: int, db: Session = Depends(get_db), _=Depends(require_admin)):
    return admin_service.exam_admin_detail(db, exam_id)


@router.get("/exams/{exam_id}/students")
def get_exam_students(exam_id: int, exam_status: str | None = None, verification: str | None = None,
                      result: str | None = None, risk: str | None = None, search: str | None = None,
                      db: Session = Depends(get_db), _=Depends(require_admin)):
    return admin_service.exam_enrolled_students(
        db, exam_id, exam_status=exam_status, verification=verification, result=result, risk=risk, search=search,
    )


# ---------------------------------------------------------------------------
# Live sessions + violations
# ---------------------------------------------------------------------------

@router.get("/live-sessions")
def get_live_sessions(db: Session = Depends(get_db), _=Depends(require_admin)):
    return admin_service.live_sessions(db)


@router.get("/violations", response_model=Page[dict])
def get_violations(params: PageParams = Depends(), severity: str | None = None,
                   decision: str | None = None, exam_id: int | None = None,
                   examiner_id: int | None = None, search: str | None = None,
                   db: Session = Depends(get_db), _=Depends(require_admin)):
    """One page of violations across the platform.

    The worst of the unbounded lists: violations accumulate per candidate per
    exam and never stop, so this grew without limit and was returned in full to
    render fifteen rows.
    """
    rows, total = admin_service.violations_overview(
        db, severity=severity, decision=decision, exam_id=exam_id, examiner_id=examiner_id,
        search=search, offset=params.offset, limit=params.page_size,
    )
    return build_page(rows, total, params)


# ------------------------------------------------------------------------------
# Activity trail
# ------------------------------------------------------------------------------

@router.get("/users/{user_id}/activity")
def user_activity(user_id: int, limit: int = 100, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    """One account's security and identity timeline, newest first.

    Keyed on user id rather than student/examiner id so a single endpoint serves
    both the candidate and examiner drill-downs -- the events (login, password,
    account status) are identical for either role.
    """
    return activity_service.list_for_user(db, user_id, limit=min(limit, 500))


@router.get("/activity")
def recent_activity(limit: int = 200, activity_type: str | None = None,
                    db: Session = Depends(get_db), _: User = Depends(require_admin)):
    """Platform-wide feed, optionally filtered to one event type."""
    return activity_service.list_recent(db, limit=min(limit, 500), activity_type=activity_type)
