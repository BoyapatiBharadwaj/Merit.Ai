"""Admin dashboard drill-down endpoints: examiners, candidates, exams, live sessions, and
violations across every organization.
"""
import csv
import io
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request, Response
from sqlalchemy.orm import Session

from app.api.deps import require_admin
from app.models.activity_log import ActivityType
from app.models.user import User
from app.core import redis_client
from app.core.config import settings
from app.database.session import get_db
from app.schemas.pagination import Page, PageParams, build_page
from app.schemas.admin import CandidateUpdateRequest, ExaminerUpdateRequest, ReverificationRequest
from app.services import activity_service, admin_service, email_service, organization_service

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


# --- - ---
# Examiners -------------------------------------------------------------------------

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
    """Edit an examiner, and record what actually changed."""
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


# --- - ---
# Candidates -------------------------------------------------------------------------

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


# --- - ---
# Exams -------------------------------------------------------------------------

@router.patch("/candidates/{student_id}")
def update_candidate(student_id: int, payload: CandidateUpdateRequest, request: Request,
                     db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    """Correct a candidate's name, email or roll number."""
    result = admin_service.update_candidate(db, student_id, **payload.model_dump(exclude_unset=True))
    before = result.pop("previous", {})

    # Record what actually changed, not what was submitted. An audit entry
    # listing every field on the form tells a later reader nothing.
    changes = [
        f"{label}: {before.get(key) or 'none'} -> {result.get(key) or 'none'}"
        for key, label in (("first_name", "First name"), ("last_name", "Last name"),
                           ("email", "Email"), ("roll_number", "Roll number"))
        if before.get(key) != result.get(key)
    ]
    if changes:
        activity_service.record(
            db, activity_type=ActivityType.CANDIDATE_UPDATED,
            subject=admin_service.candidate_user(db, student_id), actor=admin, request=request,
            description="; ".join(changes),
        )
    if result.get("reverification_required"):
        activity_service.record(
            db, activity_type=ActivityType.REVERIFICATION_REQUIRED,
            subject=admin_service.candidate_user(db, student_id), actor=admin, request=request,
            description="Identity unlocked and re-verification required after an admin edit",
        )
    return result


@router.post("/candidates/{student_id}/require-reverification")
def require_candidate_reverification(student_id: int, payload: ReverificationRequest,
                                     request: Request, background: BackgroundTasks,
                                     db: Session = Depends(get_db),
                                     admin: User = Depends(require_admin)):
    """Ask a candidate to re-register their face and re-submit their ID card."""
    result = admin_service.require_candidate_reverification(
        db, student_id, reason=payload.reason, requested_by_id=admin.id,
    )
    activity_service.record(
        db, activity_type=ActivityType.REVERIFICATION_REQUIRED,
        subject=admin_service.candidate_user(db, student_id), actor=admin, request=request,
        description=(result.get("reason") or "No reason given"),
    )

    emailed = False
    if payload.notify and result.get("email") and email_service.is_enabled():
        subject, text, html = email_service.reverification_message(
            full_name=result.get("full_name") or "there",
            reason=result.get("reason"),
        )
        email_service.queue(background, to=result["email"], subject=subject,
                            text_body=text, html_body=html)
        emailed = True

    # Reported rather than assumed. "We told them" and "we recorded it and told nobody" must not
    # look the same to the administrator who pressed the button.
    return {**result, "emailed": emailed}


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
    """One page of violations across the platform."""
    rows, total = admin_service.violations_overview(
        db, severity=severity, decision=decision, exam_id=exam_id, examiner_id=examiner_id,
        search=search, offset=params.offset, limit=params.page_size,
    )
    return build_page(rows, total, params)


# --- - ---
# Activity trail ----------------------------------------------------------------------------

@router.get("/users/{user_id}/activity")
def user_activity(user_id: int, limit: int = 100, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    """One account's security and identity timeline, newest first."""
    return activity_service.list_for_user(db, user_id, limit=min(limit, 500))


@router.get("/activity")
def recent_activity(limit: int = 200, activity_type: str | None = None,
                    db: Session = Depends(get_db), _: User = Depends(require_admin)):
    """Platform-wide feed, optionally filtered to one event type."""
    return activity_service.list_recent(db, limit=min(limit, 500), activity_type=activity_type)


# ------------------------------------------------------------------------------
# Review queue, organizations, exports
# ------------------------------------------------------------------------------

@router.get("/review-queue", response_model=Page[dict])
def get_review_queue(params: PageParams = Depends(), db: Session = Depends(get_db),
                     _=Depends(require_admin)):
    """What still needs a human decision, worst and oldest first."""
    rows, total = admin_service.review_queue(db, offset=params.offset, limit=params.page_size)
    return build_page(rows, total, params)


@router.get("/organizations/overview", response_model=list[dict])
def get_organizations_overview(db: Session = Depends(get_db), _=Depends(require_admin)):
    """Every organization and what is in it."""
    return admin_service.organizations_overview(db)


@router.get("/export/{kind}")
def export_csv(kind: str, request: Request, search: str | None = None,
               organization_id: int | None = None, severity: str | None = None,
               decision: str | None = None,
               db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    """A filtered CSV of one collection."""
    filters = {}
    if kind in ("candidates", "examiners"):
        if search:
            filters["search"] = search
        if organization_id is not None:
            filters["organization_id"] = organization_id
    elif kind == "violations":
        if severity:
            filters["severity"] = severity
        if decision:
            filters["decision"] = decision

    columns, rows = admin_service.export_rows(db, kind, **filters)

    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow(row)

    activity_service.record(
        db, activity_type=ActivityType.DATA_EXPORTED, subject=admin, actor=admin, request=request,
        description=f"Exported {len(rows)} {kind} row(s)"
        + (f" matching '{search}'" if search else ""),
    )

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
    return Response(
        content=buffer.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="meritai-{kind}-{stamp}.csv"'},
    )


@router.get("/settings")
def get_platform_settings(db: Session = Depends(get_db), _=Depends(require_admin)):
    """The configuration an administrator can see, and where it comes from."""
    from app.services import email_service

    return {
        "environment": settings.ENVIRONMENT,
        "authentication": {
            "require_email_verification": settings.REQUIRE_EMAIL_VERIFICATION,
            "require_consent_on_signup": settings.REQUIRE_CONSENT_ON_SIGNUP,
            "password_min_length": settings.PASSWORD_MIN_LENGTH,
            "session_minutes": settings.ACCESS_TOKEN_EXPIRE_MINUTES,
            "attempt_token_grace_minutes": settings.ATTEMPT_TOKEN_GRACE_MINUTES,
        },
        "proctoring": {
            "strike_limit": settings.LOCKDOWN_STRIKE_LIMIT,
            "face_match_tolerance": settings.FACE_MATCH_TOLERANCE,
            "risk_weights": {severity.value: weight
                             for severity, weight in admin_service.RISK_WEIGHTS.items()},
        },
        "email": {
            # Whether it works, not how it is authenticated.
            "configured": email_service.is_enabled(),
            "otp_length": settings.OTP_LENGTH,
            "otp_ttl_minutes": settings.OTP_TTL_MINUTES,
        },
        "scaling": {
            # A live check, not a config echo: rate limiting, OTP state and locks are
            # all Redis-backed now (app/core/redis_client.py), so "shared_state"
            # reports whether Redis is actually reachable right now rather than
            # merely configured -- an admin looking at this page during an incident
            # wants to know that, not that a REDIS_URL is set somewhere.
            "shared_state": redis_client.is_available(),
            "trusted_proxies": len(settings.trusted_proxies),
        },
        "editable": False,
        "note": "Read-only. These come from the deployment's environment; changing them "
                "requires an operator and a restart.",
    }
