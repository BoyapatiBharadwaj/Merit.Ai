"""
Business logic for the admin dashboard's drill-down views: shaping
admin_repository's raw rows into the dicts the frontend tables render,
computing each exam's active/upcoming/completed bucket, and a candidate
attempt's risk score/tier plus a templated (non-AI) proctoring summary.
"""
from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models.enums import AdminDecision, AttemptStatus, EventType, ExamStatus, Severity
from app.models.exam import Exam
from app.repositories import admin_repository, attempt_repository, exam_repository, proctor_repository, user_repository
from app.services import identity_service


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Exam-level lifecycle bucket
# ---------------------------------------------------------------------------

def exam_bucket(exam: Exam, now: datetime | None = None) -> str:
    """Exam-level lifecycle bucket ("active" | "upcoming" | "completed" |
    "draft") -- independent of any one candidate, unlike
    exam_service.compute_candidate_status (which depends on a specific
    student's own attempt state). This is what the admin dashboard's
    Active/Upcoming/Completed cards and filters count against.
    """
    if exam.status == ExamStatus.DRAFT:
        return "draft"
    if exam.status == ExamStatus.CLOSED:
        return "completed"
    now = now or datetime.now(timezone.utc)
    start = _as_utc(exam.start_time)
    end = _as_utc(exam.end_time)
    if end and now > end:
        return "completed"
    if start and now < start:
        return "upcoming"
    return "active"


# ---------------------------------------------------------------------------
# Risk scoring + templated summary
# ---------------------------------------------------------------------------

RISK_WEIGHTS = {Severity.LOW: 1, Severity.MEDIUM: 3, Severity.HIGH: 5}


# Decisions that mean "this was not misconduct". A violation carrying one of
# these has been looked at by a person and cleared, and must stop counting.
_CLEARED_DECISIONS = {"dismissed", "false_positive"}


def _tier_for(score: int, attempt_status: str | None) -> str:
    if attempt_status == AttemptStatus.TERMINATED.value or score >= 15:
        return "high"
    return "medium" if score >= 5 else "low"


def risk_score_and_tier(violations: list, attempt_status: str | None = None) -> tuple[int, str]:
    """The AUTOMATED score: every violation counted, decisions ignored.

    Kept as-is so the raw signal remains visible -- an administrator reviewing a
    reviewer's judgement needs to see what the system originally flagged, not
    only what survived adjudication.
    """
    score = sum(RISK_WEIGHTS.get(v.severity, 1) for v in violations)
    return score, _tier_for(score, attempt_status)


def adjudicated_risk(violations: list, attempt_status: str | None = None) -> dict:
    """The score after a human reviewer's decisions, and the counts behind it.

    Dismissing a violation used to update `admin_decision` and nothing else. The
    risk score, the tier and the templated proctoring summary all kept counting
    it, so a candidate whose three flags a reviewer had explicitly cleared as
    false positives stayed labelled high risk -- and the label, not the
    decisions, is what the next person to open the record sees. The review
    changed the record and not the conclusion drawn from it.

    Both numbers are returned rather than one replacing the other: "automated 82,
    adjudicated 34 after 3 dismissed" is the honest summary, and collapsing it to
    a single figure loses either the reviewer's work or the original signal.
    """
    confirmed, dismissed, pending = [], 0, 0
    for violation in violations:
        decision = (getattr(violation, "admin_decision", None) or "").lower()
        if decision in _CLEARED_DECISIONS:
            dismissed += 1
        else:
            confirmed.append(violation)
            if not decision:
                pending += 1

    automated_score, automated_tier = risk_score_and_tier(violations, attempt_status)
    score = sum(RISK_WEIGHTS.get(v.severity, 1) for v in confirmed)
    return {
        "automated_score": automated_score,
        "automated_tier": automated_tier,
        "adjudicated_score": score,
        # A terminated attempt stays high however the individual events were
        # judged: the termination is its own signal, not the sum of the flags.
        "adjudicated_tier": _tier_for(score, attempt_status),
        "confirmed_count": len(confirmed) - pending,
        "dismissed_count": dismissed,
        "pending_review_count": pending,
    }


_EVENT_LABELS = {
    EventType.NO_FACE: "the candidate's face was not visible",
    EventType.MULTIPLE_FACES: "more than one face was detected",
    EventType.FACE_MISMATCH: "a face mismatch was detected",
    EventType.FULLSCREEN_EXIT: "the candidate exited fullscreen",
    EventType.TAB_SWITCH: "the candidate switched tabs",
    EventType.COPY_PASTE_ATTEMPT: "a copy/paste attempt was blocked",
    EventType.RIGHT_CLICK_ATTEMPT: "a right-click attempt was blocked",
    EventType.NOISE_DETECTED: "background noise was detected",
    EventType.NOISE_DETECTED_LOUD: "sustained loud audio was detected",
    EventType.ID_NAME_MISMATCH: "the ID card name did not match",
    EventType.SPOOF_DETECTED: "a spoofed camera feed was suspected",
    EventType.PHONE_DETECTED: "a phone was detected in view",
    EventType.BOOK_DETECTED: "a book or notes were detected in view",
    EventType.MULTIPLE_PERSONS_DETECTED: "more than one person was detected",
    EventType.LOOKING_AWAY: "the candidate looked away from the screen repeatedly",
    EventType.GAZE_DEVIATION: "gaze deviation was detected",
    EventType.EXTERNAL_MONITOR_DETECTED: "an external monitor was detected",
    EventType.SCREENSHOT_ATTEMPT: "a screenshot attempt was recorded",
    EventType.SCREEN_SHARE_STOPPED: "screen sharing was stopped mid-exam",
    EventType.LOCKDOWN_TERMINATED: "the attempt was force-closed by the lockdown system",
}

_TIER_NOTES = {
    "high": " Overall risk is High -- recommend admin review before finalizing this result.",
    "medium": " Overall risk is Medium.",
    "low": " Overall risk is Low.",
}


def proctoring_summary(violations: list, risk_tier: str, attempt_status: str | None = None) -> str:
    """A templated (non-LLM) natural-language summary generated directly
    from the logged violations. Deterministic and free -- the same
    violations always produce the same sentence, and nothing here says
    anything the timeline below it doesn't already show; this just reads it
    aloud in one line for a reviewer skimming many candidates at once.
    """
    if not violations:
        return "No proctoring violations were recorded during this attempt."

    by_type: dict[EventType, dict] = {}
    for v in violations:
        entry = by_type.setdefault(v.event_type, {"count": 0, "max_severity": Severity.LOW})
        entry["count"] += 1
        if RISK_WEIGHTS[v.severity] > RISK_WEIGHTS[entry["max_severity"]]:
            entry["max_severity"] = v.severity

    # Highest-severity, most-frequent types first -- the two or three things
    # a reviewer most needs to know, not an exhaustive restatement.
    ranked = sorted(by_type.items(), key=lambda kv: (-RISK_WEIGHTS[kv[1]["max_severity"]], -kv[1]["count"]))
    parts = []
    for event_type, info in ranked[:3]:
        label = _EVENT_LABELS.get(event_type, event_type.value.replace("_", " "))
        parts.append(f"{label} ({info['count']}x)" if info["count"] > 1 else label)

    sentence = f"{len(violations)} violation{'s' if len(violations) != 1 else ''} recorded: " + "; ".join(parts) + "."
    if attempt_status == AttemptStatus.TERMINATED.value:
        sentence += " The attempt was terminated by the lockdown system before the candidate could submit normally."
    return sentence + _TIER_NOTES[risk_tier]


# ---------------------------------------------------------------------------
# Dashboard overview
# ---------------------------------------------------------------------------

def dashboard_summary(db: Session) -> dict:
    """Counts backing the admin dashboard's seven overview cards. Each reuses
    the exact same repository calls its own drill-down page is built from
    (list_examiners/list_candidates/list_all_exams/list_live_attempts/
    list_all_violations), just taking len() instead of shaping every row, so
    a card's number can never drift from what clicking into it shows.
    """
    now = datetime.now(timezone.utc)
    exams = exam_repository.list_all_exams(db)
    buckets = {"active": 0, "upcoming": 0, "completed": 0}
    for exam in exams:
        bucket = exam_bucket(exam, now)
        if bucket in buckets:
            buckets[bucket] += 1
    return {
        # The COUNT from the paginated query, not len() of what it returns.
        # list_examiners/list_candidates now return (rows, total), so len() of
        # the tuple was 2 -- always, regardless of how many examiners exist.
        # A cross-check test caught it immediately, which is the entire argument
        # for having one.
        "total_examiners": admin_repository.list_examiners(db, limit=0)[1],
        "total_candidates": admin_repository.list_candidates(db, limit=0)[1],
        "active_exams": buckets["active"],
        "upcoming_exams": buckets["upcoming"],
        "completed_exams": buckets["completed"],
        "live_sessions": len(admin_repository.list_live_attempts(db)),
        "violations_logged": admin_repository.list_all_violations(db, limit=0)[1],
    }


def exams_overview(db: Session, status_filter: str | None = None, search: str | None = None) -> list[dict]:
    """Platform-wide exam list behind the Active/Upcoming/Completed overview
    cards -- the same per-exam row shape as examiner_exams below, but across
    every examiner at once (with that examiner's name attached), since these
    three cards drill into "every exam on the platform in this bucket," not
    one examiner's. Kept as its own function rather than a shared helper with
    examiner_exams -- the two loops are similar but not identical (search,
    examiner_name, no examiner_id gate here), and examiner_exams is already
    covered by passing tests this shouldn't risk disturbing.
    """
    exams = exam_repository.list_all_exams(db)
    now = datetime.now(timezone.utc)
    all_attempt_ids = [a.id for exam in exams for a in exam.attempts]
    violation_counts = admin_repository.violation_counts_for_attempts(db, all_attempt_ids)
    # Batched for the same reason violation_counts is, immediately above: the
    # per-attempt lookup this replaces ran once per candidate per exam, so one
    # dashboard render cost hundreds of queries on a real cohort.
    results_by_attempt = attempt_repository.results_for_attempts(db, all_attempt_ids)

    rows = []
    for exam in exams:
        bucket = exam_bucket(exam, now)
        if status_filter and status_filter != "all" and bucket != status_filter:
            continue
        if search:
            s = search.strip().lower()
            if s not in exam.title.lower() and s not in exam.examiner.user.full_name.lower():
                continue
        attempts = exam.attempts
        results = [r for a in attempts if (r := results_by_attempt.get(a.id))]
        percentages = [r.percentage for r in results]
        rows.append({
            "id": exam.id, "title": exam.title, "type": admin_repository.exam_type_label(exam),
            "examiner_id": exam.examiner_id, "examiner_name": exam.examiner.user.full_name,
            "scheduled_date": exam.start_time,
            "enrolled": len(admin_repository.expected_students_for_exam(db, exam)),
            "completed": len(results),
            "violations": sum(violation_counts.get(a.id, 0) for a in attempts),
            "average_score": round(sum(percentages) / len(percentages), 1) if percentages else None,
            "status": bucket,
        })
    return rows


# ---------------------------------------------------------------------------
# Examiners
# ---------------------------------------------------------------------------

def examiners_overview(db: Session, search: str | None = None, organization_id: int | None = None,
                        status: str | None = None, offset: int | None = None,
                        limit: int | None = None) -> tuple[list[dict], int]:
    active_filter = {"active": True, "disabled": False}.get(status)
    examiners, total = admin_repository.list_examiners(
        db, search=search, organization_id=organization_id, active=active_filter,
        offset=offset, limit=limit)
    if not examiners:
        return [], total

    # ONE batched query pair instead of two per examiner. This loop used to call
    # candidate_ids_for_examiner per row, so a page of a hundred staff issued
    # two hundred round trips to render one table.
    candidate_counts = admin_repository.candidate_counts_for_examiners(db, examiners)

    exams = admin_repository.list_exams_for_examiners(db, [e.id for e in examiners])
    now = datetime.now(timezone.utc)
    buckets: dict[int, dict[str, int]] = {e.id: {"active": 0, "upcoming": 0, "completed": 0} for e in examiners}
    for exam in exams:
        bucket = exam_bucket(exam, now)
        if bucket in buckets[exam.examiner_id]:
            buckets[exam.examiner_id][bucket] += 1

    rows = []
    for e in examiners:
        counts = buckets[e.id]
        rows.append({
            "id": e.id, "user_id": e.user_id, "full_name": e.user.full_name, "email": e.user.email,
            "organization_id": e.organization_id, "organization_name": e.organization_name,
            "active_exams": counts["active"], "upcoming_exams": counts["upcoming"], "completed_exams": counts["completed"],
            "candidate_count": candidate_counts.get(e.id, 0),
            "is_active": e.user.is_active,
        })
    return rows, total


def examiner_detail(db: Session, examiner_id: int) -> dict:
    examiner = user_repository.get_examiner_by_id(db, examiner_id)
    if not examiner:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Examiner not found.")

    exams = exam_repository.list_exams_for_examiner(db, examiner_id)
    now = datetime.now(timezone.utc)
    counts = {"active": 0, "upcoming": 0, "completed": 0, "draft": 0}
    attempt_ids: list[int] = []
    for exam in exams:
        counts[exam_bucket(exam, now)] += 1
        attempt_ids.extend(a.id for a in exam.attempts)

    return {
        "id": examiner.id, "user_id": examiner.user_id, "full_name": examiner.user.full_name,
        "email": examiner.user.email, "organization_name": examiner.organization_name,
        "is_active": examiner.user.is_active,
        "total_exams": len(exams), "active_exams": counts["active"], "upcoming_exams": counts["upcoming"],
        "completed_exams": counts["completed"],
        "total_candidates": len(admin_repository.candidate_ids_for_examiner(db, examiner)),
        "total_violations": sum(admin_repository.violation_counts_for_attempts(db, attempt_ids).values()),
    }


def examiner_exams(db: Session, examiner_id: int, status_filter: str | None = None) -> list[dict]:
    examiner = user_repository.get_examiner_by_id(db, examiner_id)
    if not examiner:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Examiner not found.")

    exams = exam_repository.list_exams_for_examiner(db, examiner_id)
    now = datetime.now(timezone.utc)
    all_attempt_ids = [a.id for exam in exams for a in exam.attempts]
    violation_counts = admin_repository.violation_counts_for_attempts(db, all_attempt_ids)
    # Batched for the same reason violation_counts is, immediately above: the
    # per-attempt lookup this replaces ran once per candidate per exam, so one
    # dashboard render cost hundreds of queries on a real cohort.
    results_by_attempt = attempt_repository.results_for_attempts(db, all_attempt_ids)

    rows = []
    for exam in exams:
        bucket = exam_bucket(exam, now)
        if status_filter and status_filter != "all" and bucket != status_filter:
            continue
        attempts = exam.attempts
        results = [r for a in attempts if (r := results_by_attempt.get(a.id))]
        percentages = [r.percentage for r in results]
        rows.append({
            "id": exam.id, "title": exam.title, "type": admin_repository.exam_type_label(exam),
            "scheduled_date": exam.start_time,
            "enrolled": len(admin_repository.expected_students_for_exam(db, exam)),
            "completed": len(results),
            "violations": sum(violation_counts.get(a.id, 0) for a in attempts),
            "average_score": round(sum(percentages) / len(percentages), 1) if percentages else None,
            "status": bucket,
        })
    return rows


def examiner_user(db: Session, examiner_id: int):
    """The User behind an examiner profile -- the activity trail records people,
    not profile rows."""
    examiner = user_repository.get_examiner_by_id(db, examiner_id)
    return examiner.user if examiner else None


def update_examiner(db: Session, examiner_id: int, first_name: str | None = None,
                     last_name: str | None = None, organization_name: str | None = None,
                     organization_id: int | None = None) -> dict:
    """Edit an examiner, moving them between organizations properly.

    This used to write `organization_name` only -- a display string -- while
    `organization_id` is the authoritative tenancy field that decides which
    students they see, which exams are theirs, and which roster they draw from.
    So an administrator could type a new organization, watch it save, and have
    moved nobody: the examiner stayed in the old tenant while the interface said
    otherwise. Every downstream count and filter kept using the old one.

    Setting the name now resolves it to a real organization row and moves the
    foreign key with it, so the two can no longer disagree.
    """
    from app.services import organization_service

    examiner = user_repository.get_examiner_by_id(db, examiner_id)
    if not examiner:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Examiner not found.")
    user = examiner.user
    before = {"organization_id": examiner.organization_id,
              "organization_name": examiner.organization_name}

    if first_name is not None or last_name is not None:
        user.set_name(first_name if first_name is not None else user.first_name,
                      last_name if last_name is not None else user.last_name)

    try:
        if organization_id is not None:
            organization = organization_service.get_by_id(db, organization_id)
            if organization is None:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, "That organization does not exist.")
            examiner.organization_id = organization.id
            examiner.organization_name = organization.name
        elif organization_name is not None:
            name = organization_name.strip()
            if name:
                organization = organization_service.get_or_create(db, name, commit=False)
                examiner.organization_id = organization.id
                examiner.organization_name = organization.name
            else:
                # Clearing the label clears the tenancy too, rather than leaving
                # a blank name pointing at a real organization.
                examiner.organization_id = None
                examiner.organization_name = None
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(examiner)
    return {
        "id": examiner.id, "full_name": user.full_name,
        "organization_id": examiner.organization_id,
        "organization_name": examiner.organization_name,
        # Returned so the caller can record what actually changed rather than
        # what was requested -- see the audit note in the admin router.
        "previous": before,
    }


def delete_examiner(db: Session, examiner_id: int) -> None:
    """Hard-deletes an examiner account -- but only when doing so destroys
    nothing real. If any of their exams has even one candidate attempt, this
    refuses and points at deactivation instead: cascading that delete would
    silently erase actual assessment history (scores, violations, results),
    which is exactly the kind of data loss this codebase avoids elsewhere
    (see delete_exam's draft-only gate, and AttemptReset's own docstring).
    An examiner with zero attempts anywhere -- e.g. never got past a draft --
    has nothing at stake, so the cascade (Examiner -> Exams -> Sections/
    Questions) is safe to let through.
    """
    from app.models.attempt import StudentExamAttempt  # local import: avoids a module-load cycle with attempt.py

    examiner = user_repository.get_examiner_by_id(db, examiner_id)
    if not examiner:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Examiner not found.")

    has_attempts = (
        db.query(StudentExamAttempt.id)
        .join(Exam, StudentExamAttempt.exam_id == Exam.id)
        .filter(Exam.examiner_id == examiner_id)
        .first() is not None
    )
    if has_attempts:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "This examiner has exams with real candidate attempts and cannot be deleted. Disable the account instead.",
        )
    db.delete(examiner.user)  # cascades: User -> Examiner -> Exams -> Sections/Questions/ExamParticipants
    db.commit()


# ---------------------------------------------------------------------------
# Exams (admin view of one exam + its enrolled candidates)
# ---------------------------------------------------------------------------

def exam_admin_detail(db: Session, exam_id: int) -> dict:
    exam = exam_repository.get_exam(db, exam_id)
    if not exam:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Exam not found.")

    now = datetime.now(timezone.utc)
    expected = admin_repository.expected_students_for_exam(db, exam)
    attempts = exam.attempts
    attempt_ids = [a.id for a in attempts]
    results_by_attempt = attempt_repository.results_for_attempts(db, attempt_ids)
    results = [r for a in attempts if (r := results_by_attempt.get(a.id))]
    percentages = [r.percentage for r in results]
    violation_counts = admin_repository.violation_counts_for_attempts(db, attempt_ids)
    total_marks = sum(q.marks for s in exam.sections for q in s.questions)

    return {
        "id": exam.id, "title": exam.title, "examiner_name": exam.examiner.user.full_name,
        "examiner_id": exam.examiner_id, "type": admin_repository.exam_type_label(exam),
        "scheduled_date": exam.start_time, "duration_minutes": exam.duration_minutes,
        "total_marks": total_marks, "pass_percentage": exam.pass_percentage,
        "status": exam_bucket(exam, now),
        "enrolled": len(expected), "attended": len(attempts),
        "submitted": len(results),
        "absent": max(len(expected) - len(attempts), 0),
        "violations": sum(violation_counts.values()),
        "average_score": round(sum(percentages) / len(percentages), 1) if percentages else None,
    }


def exam_enrolled_students(db: Session, exam_id: int, *, exam_status: str | None = None,
                            verification: str | None = None, result: str | None = None,
                            risk: str | None = None, search: str | None = None) -> list[dict]:
    exam = exam_repository.get_exam(db, exam_id)
    if not exam:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Exam not found.")

    now = datetime.now(timezone.utc)
    exam_closed = exam_bucket(exam, now) == "completed"
    expected = admin_repository.expected_students_for_exam(db, exam)
    attempts_by_student = {a.student_id: a for a in exam.attempts}
    # Both maps built once, outside the loop. Previously each candidate row cost
    # one result query AND one proctor-events query -- and proctor_events is the
    # fastest-growing table here, so a 500-candidate exam made this the most
    # expensive page in the admin app by a wide margin.
    _attempt_ids = [a.id for a in exam.attempts]
    results_by_attempt = attempt_repository.results_for_attempts(db, _attempt_ids)
    events_by_attempt = proctor_repository.events_for_attempts(db, _attempt_ids)
    face_ids = proctor_repository.students_with_face_profiles(db, [s.id for s in expected])

    rows = []
    for student in expected:
        attempt = attempts_by_student.get(student.id)
        identity = identity_service.verification_state(db, student, face_registered_ids=face_ids)
        verification_label = "Verified" if identity["identity_locked"] else "Pending"

        if attempt is None:
            candidate_status = "Absent" if exam_closed else "Not Started"
            score = None
            result_label = None
            violation_count = 0
            risk_tier = None
            attempt_id = None
        else:
            events = events_by_attempt.get(attempt.id, [])
            violation_count = len(events)
            risk_tier = adjudicated_risk(events, attempt.status.value)["adjudicated_tier"]
            result_row = results_by_attempt.get(attempt.id)
            if attempt.status == AttemptStatus.TERMINATED:
                candidate_status = "Terminated"
            elif attempt.status == AttemptStatus.IN_PROGRESS:
                candidate_status = "In Progress"
            else:
                candidate_status = "Completed"
            score = round(result_row.percentage, 1) if result_row else None
            result_label = None
            if result_row is not None:
                result_label = "Passed" if result_row.percentage >= exam.pass_percentage else "Failed"
            attempt_id = attempt.id

        rows.append({
            "student_id": student.id, "attempt_id": attempt_id,
            "name": student.user.full_name, "candidate_id": student.roll_number, "email": student.user.email,
            "verification": verification_label, "exam_status": candidate_status,
            "score": score, "result": result_label, "violations": violation_count, "risk": risk_tier,
        })

    if search:
        s = search.strip().lower()
        rows = [r for r in rows if s in r["name"].lower() or s in r["email"].lower()]
    if exam_status:
        wanted = exam_status.replace("_", " ").lower()
        rows = [r for r in rows if r["exam_status"].lower() == wanted]
    if verification:
        rows = [r for r in rows if r["verification"].lower() == verification.lower()]
    if result:
        rows = [r for r in rows if (r["result"] or "").lower() == result.lower()]
    if risk:
        rows = [r for r in rows if (r["risk"] or "") == risk.lower()]
    return rows


# ---------------------------------------------------------------------------
# Candidates
# ---------------------------------------------------------------------------

def candidates_overview(db: Session, search: str | None = None, organization_id: int | None = None,
                        offset: int | None = None, limit: int | None = None) -> tuple[list[dict], int]:
    students, total = admin_repository.list_candidates(
        db, search=search, organization_id=organization_id, offset=offset, limit=limit)
    if not students:
        return [], total

    all_attempt_ids = [a.id for s in students for a in s.attempts]
    violation_counts = admin_repository.violation_counts_for_attempts(db, all_attempt_ids)
    results_by_attempt = attempt_repository.results_for_attempts(db, all_attempt_ids)

    rows = []
    for student in students:
        attempts = student.attempts
        completed = sum(1 for a in attempts if a.id in results_by_attempt)
        in_progress = sum(1 for a in attempts if a.status == AttemptStatus.IN_PROGRESS)
        rows.append({
            "id": student.id, "full_name": student.user.full_name, "email": student.user.email,
            "organization_name": student.organization.name if student.organization else None,
            "exams_taken": len(attempts), "completed_exams": completed, "in_progress_exams": in_progress,
            "total_violations": sum(violation_counts.get(a.id, 0) for a in attempts),
            "is_active": student.user.is_active,
        })
    return rows, total


def candidate_detail(db: Session, student_id: int) -> dict:
    student = user_repository.get_student_by_id(db, student_id)
    if not student:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Candidate not found.")

    identity = identity_service.verification_state(db, student)
    _attempt_ids = [a.id for a in student.attempts]
    results_by_attempt = attempt_repository.results_for_attempts(db, _attempt_ids)
    events_by_attempt = proctor_repository.events_for_attempts(db, _attempt_ids)

    history = []
    for attempt in student.attempts:
        exam = attempt.exam
        result_row = results_by_attempt.get(attempt.id)
        events = events_by_attempt.get(attempt.id, [])
        risk_tier = adjudicated_risk(events, attempt.status.value)["adjudicated_tier"]
        result_label = None
        if result_row is not None:
            result_label = "Passed" if result_row.percentage >= exam.pass_percentage else "Failed"
        history.append({
            "attempt_id": attempt.id, "exam_id": exam.id, "exam_title": exam.title,
            "examiner_name": exam.examiner.user.full_name,
            "started_at": attempt.started_at, "submitted_at": attempt.submitted_at,
            "status": attempt.status.value,
            "score": round(result_row.percentage, 1) if result_row else None,
            "result": result_label, "violations": len(events), "risk": risk_tier,
        })
    history.sort(key=lambda h: h["started_at"] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)

    return {
        "id": student.id,
        # The USER id, not just the student profile id. Every account action
        # (activate, reset password, delete) is keyed on the user, and the page
        # had no way to reach it -- which is part of why the account panel was
        # never built despite the endpoints existing.
        "user_id": student.user_id,
        "full_name": student.user.full_name, "email": student.user.email,
        "roll_number": student.roll_number,
        "organization_name": student.organization.name if student.organization else None,
        "is_active": student.user.is_active,
        "face_registered": identity["face_registered"], "id_verified": identity["id_verified"],
        "identity_locked": identity["identity_locked"],
        # Surfaced so the admin detail page can show an outstanding request
        # instead of two green ticks that no longer mean the candidate can sit
        # anything -- the state that made this feature necessary in the first
        # place.
        "reverification_required": identity["reverification_required"],
        "reverification_reason": identity["reverification_reason"],
        "reverification_required_at": identity["reverification_required_at"],
        "total_exams": len(history),
        "completed_exams": sum(1 for h in history if h["score"] is not None),
        "in_progress_exams": sum(1 for h in history if h["status"] == AttemptStatus.IN_PROGRESS.value),
        "total_violations": sum(h["violations"] for h in history),
        "history": history,
    }


# ---------------------------------------------------------------------------
# Live sessions + violations
# ---------------------------------------------------------------------------

def live_sessions(db: Session) -> list[dict]:
    attempts = admin_repository.list_live_attempts(db)
    now = datetime.now(timezone.utc)
    rows = []
    for a in attempts:
        started = _as_utc(a.started_at)
        elapsed_minutes = int((now - started).total_seconds() // 60) if started else None
        rows.append({
            "attempt_id": a.id, "student_id": a.student_id, "student_name": a.student.user.full_name,
            "exam_id": a.exam_id, "exam_title": a.exam.title, "examiner_name": a.exam.examiner.user.full_name,
            "started_at": a.started_at, "elapsed_minutes": elapsed_minutes,
        })
    return rows


def violations_overview(db: Session, *, severity: str | None = None, decision: str | None = None,
                         exam_id: int | None = None, examiner_id: int | None = None,
                         search: str | None = None,
                         offset: int | None = None, limit: int | None = None) -> tuple[list[dict], int]:
    events, total = admin_repository.list_all_violations(
        db, severity=severity, decision=decision, exam_id=exam_id, examiner_id=examiner_id,
        search=search, offset=offset, limit=limit)
    rows = []
    for e in events:
        attempt = e.attempt
        rows.append({
            "id": e.id, "attempt_id": attempt.id, "student_id": attempt.student_id,
            "student_name": attempt.student.user.full_name, "exam_id": attempt.exam_id,
            "exam_title": attempt.exam.title, "examiner_name": attempt.exam.examiner.user.full_name,
            "event_type": e.event_type.value, "severity": e.severity.value, "description": e.description,
            "has_screenshot": bool(e.screenshot_path), "admin_decision": e.admin_decision.value,
            "created_at": e.created_at,
        })
    return rows, total


def set_violation_decision(db: Session, event_id: int, decision: str) -> dict:
    event = proctor_repository.get_event(db, event_id)
    if not event:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Violation not found.")
    event.admin_decision = AdminDecision(decision)
    db.commit()
    db.refresh(event)
    return {"id": event.id, "admin_decision": event.admin_decision.value}


def review_queue(db: Session, *, offset: int, limit: int) -> tuple[list[dict], int]:
    """The reviewer's worklist: what still needs a decision, worst first."""
    events, total = admin_repository.review_queue(db, offset=offset, limit=limit)
    now = datetime.now(timezone.utc)
    rows = []
    for event in events:
        attempt = event.attempt
        created = event.created_at
        if created is not None and created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        rows.append({
            "id": event.id,
            "attempt_id": attempt.id,
            "student_id": attempt.student_id,
            "student_name": attempt.student.user.full_name if attempt.student and attempt.student.user else None,
            "exam_id": attempt.exam_id,
            "exam_title": attempt.exam.title,
            "event_type": event.event_type.value,
            "severity": event.severity.value,
            "description": event.description,
            "has_screenshot": bool(event.screenshot_path),
            "created_at": event.created_at,
            # How long this candidate has been waiting on a decision. An
            # undecided flag counts against them (see adjudicated_risk), so
            # age is not cosmetic -- it is how long someone has carried an
            # unresolved accusation.
            "waiting_hours": round((now - created).total_seconds() / 3600, 1) if created else None,
        })
    return rows, total


def organizations_overview(db: Session) -> list[dict]:
    return admin_repository.organizations_overview(db)


# --- exports ------------------------------------------------------------------
#
# Deliberately narrow. Every export below is a flat table of facts an
# administrator already sees on screen -- no identity photographs, no ID card
# images, no face embeddings, no proctoring screenshots. Those are the most
# sensitive things this platform holds, they are served through individually
# authorised endpoints for a reason, and a CSV is exactly the artefact that ends
# up forwarded, stored on a laptop and forgotten about.

CANDIDATE_EXPORT_COLUMNS = ["id", "full_name", "email", "organization_name",
                            "exams_taken", "completed_exams", "in_progress_exams",
                            "total_violations", "is_active"]
EXAMINER_EXPORT_COLUMNS = ["id", "full_name", "email", "organization_name",
                           "active_exams", "upcoming_exams", "completed_exams",
                           "candidate_count", "is_active"]
VIOLATION_EXPORT_COLUMNS = ["id", "attempt_id", "student_name", "exam_title", "examiner_name",
                            "event_type", "severity", "description", "admin_decision", "created_at"]


def export_rows(db: Session, kind: str, **filters) -> tuple[list[str], list[dict]]:
    """Column order and rows for one export.

    Returns the columns explicitly rather than deriving them from the first row:
    a dict's key order is an implementation detail, and a CSV whose columns
    shift between exports is one nobody can build a spreadsheet against.
    """
    if kind == "candidates":
        rows, _ = candidates_overview(db, **filters)
        return CANDIDATE_EXPORT_COLUMNS, rows
    if kind == "examiners":
        rows, _ = examiners_overview(db, **filters)
        return EXAMINER_EXPORT_COLUMNS, rows
    if kind == "violations":
        rows, _ = violations_overview(db, **filters)
        return VIOLATION_EXPORT_COLUMNS, rows
    raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unknown export '{kind}'.")


# ==============================================================================
# Candidate account administration
#
# Deleting, deactivating and resetting a candidate's password already live in
# api/v1/users.py, which owns those verbs for every role. What was missing was
# the two things that are specific to a CANDIDATE: editing the details their
# identity was verified against, and asking them to verify again.
# ==============================================================================

def _candidate(db: Session, student_id: int):
    student = user_repository.get_student_by_id(db, student_id)
    if not student:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Candidate not found.")
    return student


def candidate_user(db: Session, student_id: int):
    """The User row behind a candidate, for activity_service.record.

    Mirrors examiner_user. The audit trail is keyed on users, not on role
    profiles, so that "everything that happened to this person" is one query
    rather than a union across profile tables.
    """
    student = user_repository.get_student_by_id(db, student_id)
    return student.user if student else None


def update_candidate(db: Session, student_id: int, *, first_name: str | None = None,
                     last_name: str | None = None, email: str | None = None,
                     roll_number: str | None = None) -> dict:
    """Edit a candidate's account, and report the consequence honestly.

    The delicate part is not the write, it is what the write means. A verified
    candidate's name was matched against the name printed on their ID card, and
    `identity_locked` records that this happened. Quietly renaming such an
    account would leave a "verified" badge attached to a name nobody has ever
    checked -- which is worse than no badge, because the badge is what an
    examiner relies on when deciding whether the person on the webcam is the
    person enrolled.

    So editing a locked account unlocks it AND flags it for re-verification,
    and the return value says so. The caller is expected to surface that; the
    admin UI does. The alternative designs were both worse: refusing the edit
    entirely makes a genuine typo unfixable without a second ceremony, and
    editing silently is the failure described above.

    Returns the previous values alongside the new ones so the audit entry can
    record what actually changed rather than what was submitted.
    """
    student = _candidate(db, student_id)
    user = student.user
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Candidate has no user account.")

    previous = {
        "first_name": user.first_name, "last_name": user.last_name,
        "email": user.email, "roll_number": student.roll_number,
    }

    identity_fields_changed = False

    if first_name is not None or last_name is not None:
        new_first = (first_name if first_name is not None else user.first_name) or ""
        new_last = (last_name if last_name is not None else user.last_name) or ""
        if (new_first, new_last) != (user.first_name, user.last_name):
            user.set_name(new_first, new_last)
            identity_fields_changed = True

    if email is not None:
        new_email = email.strip().lower()
        if new_email != (user.email or "").lower():
            existing = user_repository.get_user_by_email(db, new_email)
            if existing and existing.id != user.id:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, "That email is already in use.")
            # set_email clears email_verified_at, which is correct: the proof
            # was about the OLD address.
            user.set_email(new_email)
            identity_fields_changed = True

    if roll_number is not None:
        new_roll = roll_number.strip() or None
        if new_roll != student.roll_number:
            if new_roll:
                clash = user_repository.get_student_by_roll_number(db, new_roll)
                if clash and clash.id != student.id:
                    raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                        "That roll number belongs to another candidate.")
            student.roll_number = new_roll

    # Only name and email were matched against the ID card. A roll number was
    # not, so changing it alone must not cost the candidate a re-verification
    # they did nothing to deserve.
    unlocked = False
    reverification_required = False
    if identity_fields_changed and student.identity_locked:
        student.identity_locked = False
        student.identity_locked_at = None
        unlocked = True
        identity_service.require_reverification(
            db, student, reason="Your name or email was corrected by an administrator.",
            requested_by_id=None, commit=False,
        )
        reverification_required = True

    try:
        db.commit()
        db.refresh(student)
        db.refresh(user)
    except Exception:
        db.rollback()
        raise

    return {
        "student_id": student.id,
        "user_id": user.id,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "full_name": user.full_name,
        "email": user.email,
        "roll_number": student.roll_number,
        "identity_unlocked": unlocked,
        "reverification_required": reverification_required,
        "previous": previous,
    }


def require_candidate_reverification(db: Session, student_id: int, *, reason: str | None,
                                     requested_by_id: int | None) -> dict:
    """Ask a candidate to re-register their face and re-submit their ID card.

    Refuses when there is nothing to re-verify. A candidate who has never
    completed verification is already blocked by the ordinary gate, and marking
    them would produce a confusing second message about redoing something they
    have not done once.
    """
    student = _candidate(db, student_id)
    state = identity_service.verification_state(db, student)
    if not (state["face_registered"] or state["id_verified"]):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "This candidate has not completed identity verification yet, so there is nothing to "
            "re-verify. They are already blocked from proctored exams until they do.",
        )
    if state["reverification_required"]:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "This candidate has already been asked to verify again.")

    identity_service.require_reverification(
        db, student, reason=reason, requested_by_id=requested_by_id,
    )
    return {
        "student_id": student.id,
        "user_id": student.user_id,
        "email": student.user.email if student.user else None,
        "full_name": student.user.full_name if student.user else None,
        "reason": student.reverification_reason,
        "requested_at": student.reverification_required_at,
    }


def assert_deletable(db: Session, target) -> None:
    """Refuse to delete an account whose assessment record would go with it.

    This guard existed only on DELETE /admin/examiners/{id}. The admin UI also
    deletes through DELETE /users/{id} -- the route that handles both roles --
    which had no such check, so the protection was route-dependent: the same
    examiner the Examiners page refused to delete could be deleted from the
    candidate/user path, and a candidate with graded results could always be
    deleted from anywhere.

    Candidates are the more important half. An examiner's departure costs the
    platform an author; a candidate's deletion destroys submitted answers,
    marks and the proctoring evidence behind them -- the assessment record
    itself, which is the one thing an examination platform exists to keep. The
    cascade is deliberate and correct for a genuine erasure request; it is the
    wrong default for "this person left".

    Deactivating is offered instead because it achieves what the administrator
    almost always actually wants (the account stops working) without destroying
    what nobody asked to destroy.
    """
    # Local import, matching delete_examiner above: attempt.py imports from
    # this module's dependency graph, so a top-level import reintroduces a
    # module-load cycle.
    from app.models.attempt import StudentExamAttempt

    role = getattr(getattr(target, "role", None), "name", None)

    if role == "student":
        student = target.student_profile
        if student is None:
            return
        has_attempts = (
            db.query(StudentExamAttempt.id)
            .filter(StudentExamAttempt.student_id == student.id)
            .first() is not None
        )
        if has_attempts:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "This candidate has sat at least one exam, so deleting the account would destroy "
                "their answers, marks and proctoring record along with it. Disable the account "
                "instead. If this is a data-erasure request, remove their biometric data first "
                "— that is deletable on its own and leaves the assessment record intact.",
            )
        return

    if role == "examiner":
        examiner = target.examiner_profile
        if examiner is None:
            return
        has_attempts = (
            db.query(StudentExamAttempt.id)
            .join(Exam, StudentExamAttempt.exam_id == Exam.id)
            .filter(Exam.examiner_id == examiner.id)
            .first() is not None
        )
        if has_attempts:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "This examiner has exams with real candidate attempts and cannot be deleted. "
                "Disable the account instead.",
            )
