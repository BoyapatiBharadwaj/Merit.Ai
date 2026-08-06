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
    """Render a scheduled time for an email body.

    UTC and explicitly labelled as such. The alternative -- rendering in the
    server's local timezone -- produces a string that is wrong for most
    recipients and, worse, gives no hint that it might be: "starts at 09:00"
    with no zone is the kind of detail someone misses an exam over. The app
    itself localises times in the browser, where the reader's zone is actually
    known; an email has no such luxury.
    """
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
    """Email the exam's nominated address with its current details.

    Called whenever that address is SET or CHANGED -- not only at publish. An
    examiner who types an address into the exam has just told the platform "tell
    this person about this exam", and waiting until publish to act on it means
    the confirmation arrives long after the moment they expected it, or never,
    if the exam stays a draft.

    Sends the exam's details as they stand right now, draft or published, so the
    recipient gets something meaningful rather than a bare "you've been added".
    Returns whether a message was queued, so callers can log or test it.
    """
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
    """Stamp the owning examiner's organization onto the exam.

    This is the only writer of Exam.organization_id, and it is what makes the
    exam visible to that organization's students and nobody else. An examiner
    with no organization cannot create exams at all: the alternative is a NULL
    organization_id, which can_student_access_exam treats as inaccessible, so
    the exam would be invisible to everyone and the examiner would have no way
    to find out why.
    """
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
    #
    # Every section arrived with order_index 0, because the create form had no
    # field for it and the schema defaulted it. Section.questions is ordered by
    # order_index, so with every section at 0 the tie was broken by whatever the
    # database returned -- the display order of an exam's sections was
    # effectively arbitrary, and could differ between two page loads.
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
    """Rename a section, draft-only.

    Reuses _get_editable_exam's owner + DRAFT gate rather than adding a
    second rule: a section title is part of the paper a candidate sees, so it
    freezes at publish for exactly the same reason questions and options do.
    Anything looser would let an examiner relabel a section under students
    who are mid-attempt.
    """
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

    # Server-assigned when the caller does not supply one, which was every
    # caller: the builder and the bulk importer both sent 0 for every question,
    # so questions inside a section had no meaningful order at all until
    # somebody happened to drag one.
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
        # Question and options in one transaction -- see add_question. The loop
        # that used to live here committed each option separately, so a failure
        # part-way through left an MCQ whose correct answer might not exist.
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
        # Switching an existing MCQ question to coding must not leave its old
        # options behind -- they'd otherwise be orphaned rows with no UI ever
        # showing them again, but still sitting in the table.
        exam_repository.replace_options(db, question.id, [])
    return exam_repository.get_question(db, question.id)


def add_questions_bulk(db: Session, examiner_id: int, section_id: int, questions: list[dict]) -> dict:
    """Import a batch of questions, all or nothing.

    The importer used to POST one request per question from the browser. Any
    failure part-way through -- one malformed row, a dropped connection at
    question 40 of 60 -- left the exam holding whatever had already succeeded,
    with no record of where it stopped. The examiner's options were to hunt for
    the boundary by eye or delete everything and start again, and a paper that
    is silently missing its last twenty questions is the kind of thing nobody
    notices until candidates are sitting it.

    One request, one transaction. Every question is validated first, so a bad
    row is reported with its position and nothing is written; then all of them
    are inserted together.
    """
    section = exam_repository.get_section(db, section_id)
    if not section:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Section not found.")
    _get_editable_exam(db, examiner_id, section.exam_id)

    if not questions:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No questions to import.")

    # Validate everything before writing anything, and report EVERY problem
    # rather than only the first -- an examiner fixing a sixty-question import
    # one error per attempt is a worse experience than the partial writes.
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
    """Delete a section and everything in it. Draft-only.

    Same owner + DRAFT gate as every other structural edit (_get_editable_exam):
    once an exam is published a candidate may be mid-attempt against that exact
    paper, and removing a section under them would invalidate their attempt's
    question_order and their answers along with it.

    Refuses to delete the LAST section. An exam with no sections cannot be
    published (publish_exam requires at least one question) and shows as an
    empty shell in the builder -- so this would leave the examiner in a state
    whose only exit is deleting the exam. Deleting the exam is a separate,
    clearly-labelled action; a section delete should not become one by accident.

    Returns what was removed so the UI can confirm it concretely rather than
    just closing a dialog.
    """
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
    """Permanently removes a drafted exam and everything under it (sections,
    questions, options). Reuses _get_editable_exam's owner + DRAFT-only gate
    deliberately: a published exam may already have student attempts and
    results riding on it, so deleting it is not offered at all -- an
    examiner who no longer wants a published exam available should close it
    instead (see the exam status lifecycle), not delete it out from under
    students who already sat it."""
    exam = _get_editable_exam(db, examiner_id, exam_id)
    exam_repository.delete_exam(db, exam)


def publish_exam(db: Session, examiner_id: int, exam_id: int, background: BackgroundTasks | None = None):
    """Publish, then notify the nominated address.

    The notification hangs off publish rather than create on purpose. A draft
    is a work in progress that an examiner may build over days and never
    finish; publishing is the single moment the exam becomes real to
    candidates, so it is the only point at which "an exam has been scheduled"
    is true enough to email about.
    """
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
    """Stop accepting new attempts, keeping everything already sat.

    ExamStatus.CLOSED existed in the database from the beginning with no
    endpoint and no button, so a published exam stayed visible to candidates
    forever -- the only way to take one down was to leave it and hope the
    end_time was set. An examiner who forgot one had no way to correct it.

    Refuses by default while anyone is still writing. Closing an exam out from
    under a live candidate is the participant-list bug in a different costume,
    and it should take a deliberate second action rather than happening because
    the examiner did not know somebody was still in the room. `force=True`
    closes anyway; attempts already underway are finalised by their own
    deadline as usual, so nobody's work is discarded either way.
    """
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
    """Put a closed exam back on the candidate list.

    Only from CLOSED, and only back to PUBLISHED -- never to DRAFT. Returning a
    sat exam to draft would unlock its questions for editing while results
    referencing those exact questions already exist, which would silently change
    what a graded candidate was asked.
    """
    exam = _get_owned_exam(db, examiner_id, exam_id)
    if exam.status != ExamStatus.CLOSED:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Only a closed exam can be reopened.")
    return exam_repository.set_exam_status(db, exam, ExamStatus.PUBLISHED)


def update_exam_details(db: Session, examiner_id: int, exam_id: int, payload: dict,
                         background: BackgroundTasks | None = None):
    """Full edit of every exam field (title, duration, pass mark, schedule,
    etc.) -- draft only, reusing _get_editable_exam's owner+draft gate. Once
    published, structural fields are frozen and only the schedule can still
    move, through the narrower, state-aware update_exam_schedule below."""
    exam = _get_editable_exam(db, examiner_id, exam_id)
    previous_email = exam.notify_email
    updated = exam_repository.update_exam(db, exam, _normalize_results_release_mode(payload))
    # Only on a genuine change. Re-saving the exam with the same address must
    # not re-notify -- an examiner tweaking the pass mark three times should not
    # send three emails to the same person.
    if updated.notify_email and updated.notify_email != previous_email:
        notify_exam_address(db, updated, background=background, reason="added")
    return updated


def has_exam_started(exam, now: datetime | None = None) -> bool:
    """Whether the exam's start gate has already opened -- the line the
    schedule-edit rule hinges on. A draft has never started (nobody can be
    mid-attempt). A published exam with no start_time is open the instant it
    is published (see compute_candidate_status's "no window -> ongoing"
    branch), so it counts as started immediately. Otherwise it's started
    once `now` reaches the configured start_time."""
    if exam.status != ExamStatus.PUBLISHED:
        return False
    if exam.start_time is None:
        return True
    now = now or datetime.now(timezone.utc)
    return now >= _as_utc(exam.start_time)


def _same_instant(a: datetime | None, b: datetime | None) -> bool:
    """True if two possibly-None, possibly-naive/aware datetimes represent
    the same instant -- used to tell "the frontend resent the start time
    unchanged" (fine, even once locked) apart from "the frontend tried to
    move it" (blocked once the exam has started)."""
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
    """Schedule-only edit, available in every exam state except closed --
    unlike update_exam_details this deliberately does NOT require the exam
    to still be a draft, since the whole point is letting an examiner adjust
    dates after publishing. What it allows depends on whether the exam has
    already started (see has_exam_started):

      * not started (still a draft, or published with a start time still in
        the future): both start and end may be changed freely.
      * started (published, and either past its start time or never had
        one): the start time is frozen -- candidates may already be
        mid-attempt -- but the end time can still be extended or otherwise
        adjusted, e.g. to grant extra time after a technical issue.
    """
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


# Attempt states that mean "this candidate is done with this exam" -- lumped
# together as "completed" on the dashboard. TERMINATED (lockdown strike limit
# reached) is included here too: it is not a clean pass, but it is finished
# and scored, so it belongs with the other finished states rather than
# lingering as "ongoing" or "missed" forever.
_FINISHED_ATTEMPT_STATUSES = (AttemptStatus.SUBMITTED, AttemptStatus.AUTO_SUBMITTED, AttemptStatus.TERMINATED)


def compute_candidate_status(exam, attempt, now: datetime | None = None) -> str:
    """The single source of truth for which of the four dashboard buckets
    (upcoming / ongoing / completed / missed) an exam falls into for one
    candidate, derived purely from timestamps and attempt state -- no manual
    status field to fall out of sync.

    Order matters: attempt state is checked before the time window, so a
    student who is mid-attempt when the window closes still sees "Ongoing"
    (their own attempt deadline, not the exam's publish window, governs
    submission -- see attempt_service.remaining_seconds) rather than being
    told they missed the exam they are actively taking.
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
        # What this exam actually asks for, resolved server-side. The page used
        # to demand camera, microphone, screen share and fullscreen from every
        # candidate regardless, then say the exam was not proctored.
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