"""
Admin dashboard drill-down endpoints: examiners, candidates, exams, live
sessions, and violations across every organization.

Every route here is require_admin -- this is deliberately the one corner of
the API that sees across tenants at once. Every other read path in this app
(organization_service, the examiner's own dashboards) stays scoped to one
organization or one examiner on purpose; this router is where an admin's
platform-wide view lives instead of being bolted onto those scoped routers.
"""
import csv
import io
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request, Response
from sqlalchemy.orm import Session

from app.api.deps import require_admin
from app.models.activity_log import ActivityType
from app.models.user import User
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

@router.patch("/candidates/{student_id}")
def update_candidate(student_id: int, payload: CandidateUpdateRequest, request: Request,
                     db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    """Correct a candidate's name, email or roll number.

    Editing the name or email of a VERIFIED candidate unlocks their identity
    and flags them for re-verification -- see admin_service.update_candidate for
    why that is not optional. The response reports it so the UI can say so
    rather than letting the administrator discover it from a support ticket.
    """
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
    """Ask a candidate to re-register their face and re-submit their ID card.

    Deliberately NOT the same as erasing their biometrics, which already exists
    at DELETE /users/{id}/biometrics. The stored face and ID stay on file:
    the moment you doubt the evidence is exactly the moment you need to keep
    it, because it is what any review will look at.
    """
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

    # Reported rather than assumed. "We told them" and "we recorded it and told
    # nobody" must not look the same to the administrator who pressed the
    # button -- they will act very differently depending on which happened.
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


# ------------------------------------------------------------------------------
# Review queue, organizations, exports
# ------------------------------------------------------------------------------

@router.get("/review-queue", response_model=Page[dict])
def get_review_queue(params: PageParams = Depends(), db: Session = Depends(get_db),
                     _=Depends(require_admin)):
    """What still needs a human decision, worst and oldest first.

    The violations table shows everything newest-first, which buries an
    unreviewed high-severity flag from last week under a week of routine tab
    switches. Nothing surfaced what was still undecided -- and an undecided flag
    counts against the candidate until someone clears it.
    """
    rows, total = admin_service.review_queue(db, offset=params.offset, limit=params.page_size)
    return build_page(rows, total, params)


@router.get("/organizations/overview", response_model=list[dict])
def get_organizations_overview(db: Session = Depends(get_db), _=Depends(require_admin)):
    """Every organization and what is in it.

    Organizations decide which candidates an examiner sees and which exams a
    student may sit, and there was no page showing which existed.
    """
    return admin_service.organizations_overview(db)


@router.get("/export/{kind}")
def export_csv(kind: str, request: Request, search: str | None = None,
               organization_id: int | None = None, severity: str | None = None,
               decision: str | None = None,
               db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    """A filtered CSV of one collection.

    Recorded in the activity trail, because an export is the moment a slice of
    the platform's data leaves it -- forwarded, stored on a laptop, forgotten
    about. "Who took a copy of the candidate list, and when?" should have an
    answer, and without this it did not.

    Identity photographs, ID card images and violation screenshots are never
    included. They are the most sensitive material here, they are served through
    individually authorised endpoints for a reason, and a CSV is precisely the
    artefact that escapes those controls.
    """
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
    """The configuration an administrator can see, and where it comes from.

    The settings page said the platform was configured through `.env` and left
    it at that -- so an administrator asking "is email actually working?" or
    "how many strikes end an exam here?" had to read a file on a server they may
    not have access to. These are read-only for now, which is the honest state:
    making them writable means persisting overrides and reloading them across
    every worker, and a settings page that appears to save and does not would be
    worse than one that explains itself.

    NO SECRETS. Not the signing key, not the SMTP password, not the database
    URL. `email_configured` is a boolean rather than the address and credentials
    behind it, because the useful question is "does it work" and the rest is
    exactly what should not travel through an API response into a browser.
    """
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
            "shared_state": bool(settings.REDIS_URL.strip()),
            "trusted_proxies": len(settings.trusted_proxies),
        },
        "editable": False,
        "note": "Read-only. These come from the deployment's environment; changing them "
                "requires an operator and a restart.",
    }
