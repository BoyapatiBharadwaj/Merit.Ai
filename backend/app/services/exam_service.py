"""Business logic for exam, section, question creation, and publishing."""
import json
from datetime import datetime, timedelta, timezone

from fastapi import BackgroundTasks, HTTPException, status
from sqlalchemy.orm import Session

from app.models.enums import AttemptStatus, ExamStatus, ResultsReleaseMode
from app.models.examiner import Examiner
from app.repositories import exam_repository
from app.services import code_runner_service, email_service


def format_exam_time(value: datetime | None) -> str | None:
    """Render a scheduled time for an email body."""
    if value is None:
        return None
    aware = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return aware.astimezone(timezone.utc).strftime("%d %b %Y, %H:%M UTC")

# Mirrors schemas.exam.SCHEDULE_GRACE -- kept as a separate constant rather
# than a shared import because this check runs at a different layer (here we
# have DB state -- e.g. whether the exam has started -- that the schema
# can't see) even though the tolerance itself should stay the same.
SCHEDULE_GRACE = timedelta(seconds=30)


def notify_exam_address(db: Session, exam, *, background: BackgroundTasks | None = None,
                         reason: str = "added") -> bool:
    """Email the exam's nominated address with its current details."""
    if not exam.notify_email:
        return False

    examiner = db.query(Examiner).filter(Examiner.id == exam.examiner_id).first()
    examiner_name = examiner.user.full_name if examiner and examiner.user else "An examiner"
    subject, text, html = email_service.exam_published_message(
        exam_title=exam.title,
        examiner_name=examiner_name,
        starts_at=format_exam_time(exam.start_time),
        duration_minutes=exam.duration_minutes,
        status=exam.status.value if hasattr(exam.status, "value") else str(exam.status),
        reason=reason,
    )
    email_service.queue(background, to=exam.notify_email, subject=subject,
                        text_body=text, html_body=html)
    return True


def _normalize_results_release_mode(payload: dict) -> dict:
    """The schema carries results_release_mode as a plain string literal
    ("immediate" | "after_end_time"); the column is a real Python enum
    (ResultsReleaseMode), and Exam(**payload)/setattr expects an enum member
    the same way every other enum-typed column on this model does (compare
    AdminDecision(decision) in admin_service.set_violation_decision) rather
    than relying on SQLAlchemy to coerce a bare string on flush."""
    if payload.get("results_release_mode") is not None:
        payload = {**payload, "results_release_mode": ResultsReleaseMode(payload["results_release_mode"])}
    return payload


def create_exam(db: Session, examiner_id: int, payload: dict, background: BackgroundTasks | None = None):
    """Stamp the owning examiner's organization onto the exam."""
    examiner = db.query(Examiner).filter(Examiner.id == examiner_id).first()
    if examiner is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Examiner profile not found.")
    if examiner.organization_id is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Your account is not linked to an organization yet, so students would not be "
            "able to see this exam. Ask an administrator to assign your organization.")
    payload = _normalize_results_release_mode(payload)
    exam = exam_repository.create_exam(db, examiner_id, {**payload, "organization_id": examiner.organization_id})
    # An address supplied at creation counts as "added" just as much as one
    # typed in later -- the examiner should not have to guess which path sends.
    notify_exam_address(db, exam, background=background, reason="added")
    return exam


def add_section(db: Session, examiner_id: int, exam_id: int, payload: dict):
    exam = _get_editable_exam(db, examiner_id, exam_id)
    # Append to the end rather than trusting whatever the client sent.
    payload = {**payload, "order_index": exam_repository.next_section_order(db, exam.id)}
    return exam_repository.add_section(db, exam.id, payload)


def reorder_sections(db: Session, examiner_id: int, exam_id: int, section_ids: list[int]):
    """Set the section order explicitly. Draft-only, like every structural edit."""
    exam = _get_editable_exam(db, examiner_id, exam_id)
    valid_ids = {section.id for section in exam.sections}
    if set(section_ids) != valid_ids:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "The reordered list must contain exactly this exam's sections.")
    exam_repository.reorder_sections(db, exam.id, section_ids)
    return exam_repository.get_exam(db, exam.id)


def update_section(db: Session, examiner_id: int, section_id: int, title: str):
    """Rename a section, draft-only."""
    section = exam_repository.get_section(db, section_id)
    if not section:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Section not found.")
    _get_editable_exam(db, examiner_id, section.exam_id)
    section.title = title.strip()
    db.commit()
    db.refresh(section)
    return section


def add_question(db: Session, examiner_id: int, section_id: int, text: str, marks: int,
                  order_index: int | None = None,
                  question_type: str = "mcq", options: list[dict] | None = None,
                  language: str | None = None, starter_code: str | None = None,
                  test_cases: list[dict] | None = None, time_limit_seconds: int = 6,
                  explanation: str | None = None):
    section = exam_repository.get_section(db, section_id)
    if not section:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Section not found.")
    _get_editable_exam(db, examiner_id, section.exam_id)

    # Server-assigned when the caller does not supply one, which was every caller.
    if order_index is None:
        order_index = exam_repository.next_question_order(db, section_id)

    if question_type in ("mcq", "multi_select"):
        options = options or []
        correct_count = sum(1 for option in options if option["is_correct"])
        if question_type == "mcq":
            if correct_count != 1:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, "Mark exactly one option as correct.")
        else:
            if correct_count < 1:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, "Mark at least one option as correct.")
        # Question and options in one transaction -- see add_question.
        question = exam_repository.add_question(
            db, section_id, text, marks, order_index, question_type=question_type,
            explanation=explanation, options=options,
        )
    else:
        if language not in code_runner_service.SUPPORTED_LANGUAGES:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unsupported language. Choose one of: {', '.join(code_runner_service.SUPPORTED_LANGUAGES)}.")
        if not test_cases:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "A coding question needs at least one test case.")
        if not any(case.get("is_sample") for case in test_cases):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "A coding question needs at least one sample test case visible to students.")
        question = exam_repository.add_question(
            db, section_id, text, marks, order_index, question_type=question_type,
            language=language, starter_code=starter_code,
            test_cases_json=json.dumps(test_cases), time_limit_seconds=time_limit_seconds,
            explanation=explanation,
        )
    return exam_repository.get_question(db, question.id)


def update_question(db: Session, examiner_id: int, question_id: int, text: str, marks: int, order_index: int,
                     question_type: str = "mcq", options: list[dict] | None = None,
                     language: str | None = None, starter_code: str | None = None,
                     test_cases: list[dict] | None = None, time_limit_seconds: int = 6,
                     explanation: str | None = None):
    question = exam_repository.get_question(db, question_id)
    if not question:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Question not found.")
    _get_editable_exam(db, examiner_id, question.section.exam_id)

    if question_type in ("mcq", "multi_select"):
        options = options or []
        correct_count = sum(1 for option in options if option["is_correct"])
        if question_type == "mcq":
            if correct_count != 1:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, "Mark exactly one option as correct.")
        else:
            if correct_count < 1:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, "Mark at least one option as correct.")
        exam_repository.replace_question(
            db, question, text, marks, order_index, question_type=question_type,
            language=None, starter_code=None, test_cases_json=None, time_limit_seconds=None,
            explanation=explanation,
        )
        exam_repository.replace_options(db, question.id, options)
    else:
        if language not in code_runner_service.SUPPORTED_LANGUAGES:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unsupported language. Choose one of: {', '.join(code_runner_service.SUPPORTED_LANGUAGES)}.")
        if not test_cases:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "A coding question needs at least one test case.")
        if not any(case.get("is_sample") for case in test_cases):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "A coding question needs at least one sample test case visible to students.")
        exam_repository.replace_question(
            db, question, text, marks, order_index, question_type=question_type,
            language=language, starter_code=starter_code, test_cases_json=json.dumps(test_cases),
            time_limit_seconds=time_limit_seconds, explanation=explanation,
        )
        # Switching an existing MCQ question to coding must not leave its old options behind.
        exam_repository.replace_options(db, question.id, [])
    return exam_repository.get_question(db, question.id)


def add_questions_bulk(db: Session, examiner_id: int, section_id: int, questions: list[dict]) -> dict:
    """Import a batch of questions, all or nothing."""
    section = exam_repository.get_section(db, section_id)
    if not section:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Section not found.")
    _get_editable_exam(db, examiner_id, section.exam_id)

    if not questions:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No questions to import.")

    # Validate everything before writing anything, and
    # report EVERY problem rather than only the first.
    problems = []
    for position, item in enumerate(questions, start=1):
        reason = _describe_question_problem(item)
        if reason:
            problems.append(f"Question {position}: {reason}")
    if problems:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, {
            "code": "bulk_import_invalid",
            "message": f"{len(problems)} question(s) could not be imported. Nothing was saved.",
            "problems": problems[:50],
        })

    start_order = exam_repository.next_question_order(db, section_id)
    try:
        created = []
        for offset, item in enumerate(questions):
            question = exam_repository.add_question(
                db, section_id, item["text"], item["marks"], start_order + offset,
                question_type=item.get("question_type", "mcq"),
                explanation=item.get("explanation"),
                options=item.get("options") or [],
                commit=False,
            )
            created.append(question)
        db.commit()
    except Exception:
        db.rollback()
        raise
    return {"imported": len(created), "section_id": section_id}


def _describe_question_problem(item: dict) -> str | None:
    """Why this row cannot be imported, or None if it is fine."""
    if not (item.get("text") or "").strip():
        return "the question text is empty."
    if not isinstance(item.get("marks"), int) or item["marks"] < 1:
        return "marks must be a whole number of at least 1."

    question_type = item.get("question_type", "mcq")
    if question_type not in ("mcq", "multi_select"):
        return "bulk import supports multiple-choice questions only."

    options = item.get("options") or []
    if len(options) < 2:
        return "at least two options are needed."
    if any(not (option.get("text") or "").strip() for option in options):
        return "every option needs text."

    correct = sum(1 for option in options if option.get("is_correct"))
    if question_type == "mcq" and correct != 1:
        return f"exactly one option must be correct (found {correct})."
    if question_type == "multi_select" and correct < 1:
        return "at least one option must be correct."
    return None


def delete_question(db: Session, examiner_id: int, question_id: int):
    question = exam_repository.get_question(db, question_id)
    if not question:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Question not found.")
    _get_editable_exam(db, examiner_id, question.section.exam_id)
    exam_repository.delete_question(db, question)


def delete_section(db: Session, examiner_id: int, section_id: int) -> dict:
    """Delete a section and everything in it. Draft-only."""
    section = exam_repository.get_section(db, section_id)
    if not section:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Section not found.")
    exam = _get_editable_exam(db, examiner_id, section.exam_id)

    if len(exam.sections) <= 1:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "An exam needs at least one section. Add another section first, or delete the exam itself.",
        )

    removed = {"section_id": section.id, "title": section.title, "questions_deleted": len(section.questions)}
    exam_repository.delete_section(db, section)
    return removed


def reorder_questions(db: Session, examiner_id: int, section_id: int, question_ids: list[int]):
    section = exam_repository.get_section(db, section_id)
    if not section:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Section not found.")
    _get_editable_exam(db, examiner_id, section.exam_id)
    valid_ids = {q.id for q in section.questions}
    if set(question_ids) != valid_ids:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The reordered list must contain exactly the questions already in this section.")
    exam_repository.reorder_questions(db, section_id, question_ids)


def delete_exam(db: Session, examiner_id: int, exam_id: int):
    """Permanently removes a drafted exam and everything under it (sections, questions, options)."""
    exam = _get_editable_exam(db, examiner_id, exam_id)
    exam_repository.delete_exam(db, exam)


def publish_exam(db: Session, examiner_id: int, exam_id: int, background: BackgroundTasks | None = None):
    """Publish, then notify the nominated address."""
    exam = _get_editable_exam(db, examiner_id, exam_id)
    if not exam.sections or not any(section.questions for section in exam.sections):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Cannot publish an exam with no questions.")
    published = exam_repository.publish_exam(db, exam)

    notify_exam_address(db, published, background=background, reason="published")
    return published


def _get_owned_exam(db: Session, examiner_id: int, exam_id: int):
    """Owner check without the DRAFT requirement -- for lifecycle transitions,
    which by definition act on exams that are past draft."""
    exam = exam_repository.get_exam(db, exam_id)
    if not exam or exam.examiner_id != examiner_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Exam not found.")
    return exam


def close_exam(db: Session, examiner_id: int, exam_id: int, *, force: bool = False):
    """Stop accepting new attempts, keeping everything already sat."""
    exam = _get_owned_exam(db, examiner_id, exam_id)
    if exam.status == ExamStatus.DRAFT:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "This exam is still a draft, so there is nothing to close.")
    if exam.status == ExamStatus.CLOSED:
        return exam

    live = [a for a in exam.attempts
            if a.status == AttemptStatus.IN_PROGRESS and a.archived_at is None]
    if live and not force:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"{len(live)} candidate{'s are' if len(live) != 1 else ' is'} still sitting this exam. "
            "Closing now stops anyone new from starting; those already writing keep their time. "
            "Confirm to close anyway.",
        )
    return exam_repository.set_exam_status(db, exam, ExamStatus.CLOSED)


def reopen_exam(db: Session, examiner_id: int, exam_id: int):
    """Put a closed exam back on the candidate list."""
    exam = _get_owned_exam(db, examiner_id, exam_id)
    if exam.status != ExamStatus.CLOSED:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Only a closed exam can be reopened.")
    return exam_repository.set_exam_status(db, exam, ExamStatus.PUBLISHED)


def update_exam_details(db: Session, examiner_id: int, exam_id: int, payload: dict,
                         background: BackgroundTasks | None = None):
    """Full edit of every exam field (title, duration, pass mark, schedule, etc.) -- draft only,
    reusing _get_editable_exam's owner+draft gate.
    """
    exam = _get_editable_exam(db, examiner_id, exam_id)
    previous_email = exam.notify_email
    updated = exam_repository.update_exam(db, exam, _normalize_results_release_mode(payload))
    # Only on a genuine change. Re-saving the exam with the same address must not re-notify.
    if updated.notify_email and updated.notify_email != previous_email:
        notify_exam_address(db, updated, background=background, reason="added")
    return updated


def has_exam_started(exam, now: datetime | None = None) -> bool:
    """Whether the exam's start gate has already opened -- the line the schedule-edit rule hinges on."""
    if exam.status != ExamStatus.PUBLISHED:
        return False
    if exam.start_time is None:
        return True
    now = now or datetime.now(timezone.utc)
    return now >= _as_utc(exam.start_time)


def _same_instant(a: datetime | None, b: datetime | None) -> bool:
    """True if two possibly-None, possibly-naive/aware datetimes represent the same instant."""
    if a is None or b is None:
        return a is None and b is None
    return _as_utc(a) == _as_utc(b)


def _validate_schedule_change(start_time: datetime | None, end_time: datetime | None, now: datetime, *, start_is_locked: bool) -> None:
    if not start_is_locked and start_time is not None:
        if _as_utc(start_time) < now - SCHEDULE_GRACE:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Start date and time cannot be earlier than the current date and time.")
    if end_time is not None:
        effective_start = _as_utc(start_time) if start_time is not None else now
        if _as_utc(end_time) <= effective_start:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "End date and time must be after the start date and time.")
        if _as_utc(end_time) < now - SCHEDULE_GRACE:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "End date and time cannot be earlier than the current date and time.")


def update_exam_schedule(db: Session, examiner_id: int, exam_id: int, start_time: datetime | None, end_time: datetime | None):
    """Schedule-only edit, available in every exam state except closed."""
    exam = exam_repository.get_exam(db, exam_id)
    if not exam:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Exam not found.")
    if exam.examiner_id != examiner_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You do not own this exam.")
    if exam.status == ExamStatus.CLOSED:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "This exam is closed and its schedule can no longer be changed.")

    now = datetime.now(timezone.utc)
    started = has_exam_started(exam, now)
    if started:
        if not _same_instant(start_time, exam.start_time):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "This exam has already started, so its start date and time can no longer be changed. "
                "You can still adjust the end date and time.")
        start_time = exam.start_time  # keep the exact stored value, not just an equivalent instant

    _validate_schedule_change(start_time, end_time, now, start_is_locked=started)
    return exam_repository.update_exam(db, exam, {"start_time": start_time, "end_time": end_time})


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


# Attempt states that mean "this candidate is done with this
# exam" -- lumped together as "completed" on the dashboard.
_FINISHED_ATTEMPT_STATUSES = (AttemptStatus.SUBMITTED, AttemptStatus.AUTO_SUBMITTED, AttemptStatus.TERMINATED)


def compute_candidate_status(exam, attempt, now: datetime | None = None) -> str:
    """The single source of truth for which of the four dashboard buckets
    (upcoming / ongoing / completed / missed) an exam falls into for one
    candidate, derived purely from timestamps and attempt state.
    """
    now = now or datetime.now(timezone.utc)
    if attempt is not None:
        if attempt.status in _FINISHED_ATTEMPT_STATUSES:
            return "completed"
        if attempt.status == AttemptStatus.IN_PROGRESS:
            return "ongoing"

    end = _as_utc(exam.end_time) if exam.end_time else None
    if end and now > end:
        return "missed"

    start = _as_utc(exam.start_time) if exam.start_time else None
    if start and now < start:
        return "upcoming"

    # No attempt yet, and the window (if any) is currently open.
    return "ongoing"


def serialize_exam_for_candidate(exam, attempt, result, now: datetime | None = None) -> dict:
    """`/exams/available`'s response shape: every ExamOut field (so nothing
    that already reads this endpoint breaks) plus the computed status and a
    flattened view of the candidate's own attempt, so the dashboard (and the
    pre-exam page, which has no other student-safe way to read exam
    metadata -- GET /exams/{id} is examiner/admin-only) can build everything
    from one list instead of cross-referencing several endpoints."""
    now = now or datetime.now(timezone.utc)
    question_count = sum(len(section.questions) for section in exam.sections)
    total_marks = sum(question.marks for section in exam.sections for question in section.questions)
    return {
        "id": exam.id,
        "title": exam.title,
        "description": exam.description,
        "instructions": exam.instructions,
        "duration_minutes": exam.duration_minutes,
        "pass_percentage": exam.pass_percentage,
        "status": exam.status.value,
        "randomize_questions": exam.randomize_questions,
        "proctoring_enabled": exam.proctoring_enabled,
        # What this exam actually asks for, resolved server-side.
        "requires": {
            "camera": exam.requires("camera"),
            "microphone": exam.requires("microphone"),
            "screen_share": exam.requires("screen_share"),
            "fullscreen": exam.requires("fullscreen"),
        },
        "results_released": exam.results_released,
        "release_results_at": exam.release_results_at,
        "start_time": exam.start_time,
        "end_time": exam.end_time,
        "question_count": question_count,
        "exam_total_marks": total_marks,
        "candidate_status": compute_candidate_status(exam, attempt, now),
        "attempt_id": attempt.id if attempt else None,
        "attempt_status": attempt.status.value if attempt else None,
        "started_at": attempt.started_at if attempt else None,
        "submitted_at": attempt.submitted_at if attempt else None,
        "scored_marks": result.scored_marks if result else None,
        "total_marks": result.total_marks if result else None,
        "percentage": result.percentage if result else None,
    }


def _get_editable_exam(db: Session, examiner_id: int, exam_id: int):
    exam = exam_repository.get_exam(db, exam_id)
    if not exam:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Exam not found.")
    if exam.examiner_id != examiner_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You do not own this exam.")
    if exam.status != ExamStatus.DRAFT:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Published exams cannot be changed.")
    return exam