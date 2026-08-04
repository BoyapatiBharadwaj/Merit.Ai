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


def risk_score_and_tier(violations: list, attempt_status: str | None = None) -> tuple[int, str]:
    """A simple, fully transparent weighted score -- not a black-box model.
    Each violation adds 1/3/5 points by severity; a terminated attempt is
    always "high" regardless of the arithmetic, since a lockdown termination
    is itself the strongest signal this app can raise about one attempt.
    """
    score = sum(RISK_WEIGHTS.get(v.severity, 1) for v in violations)
    if attempt_status == AttemptStatus.TERMINATED.value or score >= 15:
        tier = "high"
    elif score >= 5:
        tier = "medium"
    else:
        tier = "low"
    return score, tier


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
        "total_examiners": len(admin_repository.list_examiners(db)),
        "total_candidates": len(admin_repository.list_candidates(db)),
        "active_exams": buckets["active"],
        "upcoming_exams": buckets["upcoming"],
        "completed_exams": buckets["completed"],
        "live_sessions": len(admin_repository.list_live_attempts(db)),
        "violations_logged": len(admin_repository.list_all_violations(db)),
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
        results = [r for a in attempts if (r := attempt_repository.get_result(db, a.id))]
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
                        status: str | None = None) -> list[dict]:
    active_filter = {"active": True, "disabled": False}.get(status)
    examiners = admin_repository.list_examiners(db, search=search, organization_id=organization_id, active=active_filter)
    if not examiners:
        return []

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
            "candidate_count": len(admin_repository.candidate_ids_for_examiner(db, e)),
            "is_active": e.user.is_active,
        })
    return rows


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

    rows = []
    for exam in exams:
        bucket = exam_bucket(exam, now)
        if status_filter and status_filter != "all" and bucket != status_filter:
            continue
        attempts = exam.attempts
        results = [r for a in attempts if (r := attempt_repository.get_result(db, a.id))]
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


def update_examiner(db: Session, examiner_id: int, first_name: str | None = None,
                     last_name: str | None = None, organization_name: str | None = None) -> dict:
    examiner = user_repository.get_examiner_by_id(db, examiner_id)
    if not examiner:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Examiner not found.")
    user = examiner.user
    if first_name is not None or last_name is not None:
        user.set_name(first_name if first_name is not None else user.first_name,
                      last_name if last_name is not None else user.last_name)
    if organization_name is not None:
        examiner.organization_name = organization_name.strip() or None
    db.commit()
    db.refresh(examiner)
    return {"id": examiner.id, "full_name": user.full_name, "organization_name": examiner.organization_name}


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
    results = [r for a in attempts if (r := attempt_repository.get_result(db, a.id))]
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

    rows = []
    for student in expected:
        attempt = attempts_by_student.get(student.id)
        identity = identity_service.verification_state(db, student)
        verification_label = "Verified" if identity["identity_locked"] else "Pending"

        if attempt is None:
            candidate_status = "Absent" if exam_closed else "Not Started"
            score = None
            result_label = None
            violation_count = 0
            risk_tier = None
            attempt_id = None
        else:
            events = proctor_repository.list_events_for_attempt(db, attempt.id)
            violation_count = len(events)
            _, risk_tier = risk_score_and_tier(events, attempt.status.value)
            result_row = attempt_repository.get_result(db, attempt.id)
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

def candidates_overview(db: Session, search: str | None = None, organization_id: int | None = None) -> list[dict]:
    students = admin_repository.list_candidates(db, search=search, organization_id=organization_id)
    if not students:
        return []

    all_attempt_ids = [a.id for s in students for a in s.attempts]
    violation_counts = admin_repository.violation_counts_for_attempts(db, all_attempt_ids)

    rows = []
    for student in students:
        attempts = student.attempts
        completed = sum(1 for a in attempts if attempt_repository.get_result(db, a.id))
        in_progress = sum(1 for a in attempts if a.status == AttemptStatus.IN_PROGRESS)
        rows.append({
            "id": student.id, "full_name": student.user.full_name, "email": student.user.email,
            "organization_name": student.organization.name if student.organization else None,
            "exams_taken": len(attempts), "completed_exams": completed, "in_progress_exams": in_progress,
            "total_violations": sum(violation_counts.get(a.id, 0) for a in attempts),
            "is_active": student.user.is_active,
        })
    return rows


def candidate_detail(db: Session, student_id: int) -> dict:
    student = user_repository.get_student_by_id(db, student_id)
    if not student:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Candidate not found.")

    identity = identity_service.verification_state(db, student)
    history = []
    for attempt in student.attempts:
        exam = attempt.exam
        result_row = attempt_repository.get_result(db, attempt.id)
        events = proctor_repository.list_events_for_attempt(db, attempt.id)
        _, risk_tier = risk_score_and_tier(events, attempt.status.value)
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
        "id": student.id, "full_name": student.user.full_name, "email": student.user.email,
        "roll_number": student.roll_number,
        "organization_name": student.organization.name if student.organization else None,
        "is_active": student.user.is_active,
        "face_registered": identity["face_registered"], "id_verified": identity["id_verified"],
        "identity_locked": identity["identity_locked"],
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
                         exam_id: int | None = None, examiner_id: int | None = None) -> list[dict]:
    events = admin_repository.list_all_violations(db, severity=severity, decision=decision,
                                                   exam_id=exam_id, examiner_id=examiner_id)
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
    return rows


def set_violation_decision(db: Session, event_id: int, decision: str) -> dict:
    event = proctor_repository.get_event(db, event_id)
    if not event:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Violation not found.")
    event.admin_decision = AdminDecision(decision)
    db.commit()
    db.refresh(event)
    return {"id": event.id, "admin_decision": event.admin_decision.value}
