"""
Admin dashboard drill-down endpoints: examiners, candidates, exams, live
sessions, and violations across every organization.

Every route here is require_admin -- this is deliberately the one corner of
the API that sees across tenants at once. Every other read path in this app
(organization_service, the examiner's own dashboards) stays scoped to one
organization or one examiner on purpose; this router is where an admin's
platform-wide view lives instead of being bolted onto those scoped routers.
"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import require_admin
from app.database.session import get_db
from app.schemas.admin import ExaminerUpdateRequest
from app.services import admin_service, organization_service

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

@router.get("/examiners")
def list_examiners(search: str | None = None, organization_id: int | None = None,
                    status: str | None = Query(default=None, pattern="^(active|disabled)$"),
                    db: Session = Depends(get_db), _=Depends(require_admin)):
    return admin_service.examiners_overview(db, search=search, organization_id=organization_id, status=status)


@router.get("/examiners/{examiner_id}")
def get_examiner_detail(examiner_id: int, db: Session = Depends(get_db), _=Depends(require_admin)):
    return admin_service.examiner_detail(db, examiner_id)


@router.get("/examiners/{examiner_id}/exams")
def get_examiner_exams(examiner_id: int, status: str | None = None,
                        db: Session = Depends(get_db), _=Depends(require_admin)):
    return admin_service.examiner_exams(db, examiner_id, status_filter=status)


@router.patch("/examiners/{examiner_id}")
def update_examiner(examiner_id: int, payload: ExaminerUpdateRequest,
                    db: Session = Depends(get_db), _=Depends(require_admin)):
    return admin_service.update_examiner(db, examiner_id, **payload.model_dump(exclude_unset=True))


@router.delete("/examiners/{examiner_id}", status_code=204)
def delete_examiner(examiner_id: int, db: Session = Depends(get_db), _=Depends(require_admin)):
    admin_service.delete_examiner(db, examiner_id)


# ---------------------------------------------------------------------------
# Candidates
# ---------------------------------------------------------------------------

@router.get("/candidates")
def list_candidates(search: str | None = None, organization_id: int | None = None,
                    db: Session = Depends(get_db), _=Depends(require_admin)):
    return admin_service.candidates_overview(db, search=search, organization_id=organization_id)


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


@router.get("/violations")
def get_violations(severity: str | None = None, decision: str | None = None,
                   exam_id: int | None = None, examiner_id: int | None = None,
                   db: Session = Depends(get_db), _=Depends(require_admin)):
    return admin_service.violations_overview(
        db, severity=severity, decision=decision, exam_id=exam_id, examiner_id=examiner_id,
    )
