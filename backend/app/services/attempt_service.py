"""Business logic for starting, answering, and submitting exam attempts."""
import json
import random
from datetime import datetime, timedelta, timezone
from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.repositories import attempt_repository, exam_repository, proctor_repository
from app.models.enums import AttemptStatus, ExamStatus, QuestionType
from app.models.student import Student
from app.services import admin_service, code_runner_service, identity_service, organization_service


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def remaining_seconds(attempt) -> int:
    deadline = _as_utc(attempt.started_at) + timedelta(minutes=attempt.exam.duration_minutes)
    return max(0, int((deadline - datetime.now(timezone.utc)).total_seconds()))


def _expired_auto_submit_error(attempt) -> HTTPException:
    """A structured 400, not just a string message. The client-side exam
    timer normally auto-submits before this is ever reached, but that timer
    only runs while the tab is open and foregrounded -- a student who closes
    the laptop mid-exam and comes back after the deadline (or never comes
    back and an examiner loads the attempt list first) hits this instead.
    The `code`/`attempt_id` let the frontend redirect straight to the report
    with a "your exam was auto-submitted" notice rather than dead-ending on
    an error banner for an exam that is, in fact, already finished."""
    return HTTPException(status.HTTP_400_BAD_REQUEST, detail={
        "code": "attempt_expired_auto_submitted",
        "message": "Your exam time expired and your attempt was automatically submitted.",
        "attempt_id": attempt.id,
    })


def finalize_if_expired(db: Session, attempt):
    """Opportunistically auto-submits an in-progress attempt whose own
    deadline (started_at + duration) has already passed.

    This is the safety net behind the client-side timer: it runs on every
    read of an attempt (list views, result, report), so an attempt left
    "in_progress" by an inactive student is finalized the moment *anyone* --
    that student, their examiner, or an admin -- next looks at it, with no
    background scheduler required. Idempotent and cheap for the (overwhelming
    majority) not-yet-expired case.
    """
    if attempt.status == AttemptStatus.IN_PROGRESS and remaining_seconds(attempt) <= 0:
        attempt = attempt_repository.mark_attempt_submitted(db, attempt, AttemptStatus.AUTO_SUBMITTED)
        _compute_and_store_result(db, attempt)
    return attempt


def start_attempt(db: Session, student_id: int, exam_id: int):
    exam = exam_repository.get_exam(db, exam_id)
    now = datetime.now(timezone.utc)
    if not exam or exam.status != ExamStatus.PUBLISHED:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Exam not available.")

    # THE access check, not a duplicate of the list filter.
    #
    # This endpoint takes an exam id straight from the client, so filtering
    # /exams/available alone would have been cosmetic: a student could still
    # sit any exam in the system by naming its id. Enforcing it here is what
    # actually closes that, and it is enforced *before* the identity gate
    # below so an outsider cannot use the difference between "verify your ID
    # first" and "exam not available" to probe which exam ids exist.
    student = db.query(Student).filter(Student.id == student_id).first()
    organization_service.require_student_access(db, student, exam)

    # Identity gate, deliberately *after* the access check. Run first (as the
    # route used to), the distinct 403 "verify your ID" versus 404 "not
    # available" told an outsider which exam ids were real.
    if exam.proctoring_enabled:
        identity_service.require_exam_ready(db, student)

    if exam.start_time and now < _as_utc(exam.start_time):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "This exam has not started yet.")
    if exam.end_time and now > _as_utc(exam.end_time):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "This exam is closed.")

    existing = attempt_repository.get_existing_attempt(db, student_id, exam_id)
    if existing:
        if existing.status == AttemptStatus.IN_PROGRESS:
            if remaining_seconds(existing) <= 0:
                finalize_if_expired(db, existing)
                raise _expired_auto_submit_error(existing)
            return existing, [int(x) for x in existing.question_order.split(",")]
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "You have already attempted this exam.")

    question_ids = exam_repository.get_all_question_ids_for_exam(db, exam_id)
    if not question_ids:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Exam has no questions.")
    if exam.randomize_questions:
        random.shuffle(question_ids)

    attempt = attempt_repository.create_attempt(
        db, student_id, exam_id, ",".join(map(str, question_ids)),
        option_order_json=_build_option_order(exam),
    )
    return attempt, question_ids


def _build_option_order(exam) -> str | None:
    """Computes this attempt's fixed per-question option display order, if
    the exam has randomize_options enabled -- one independent shuffle per
    attempt, so candidates sitting side by side see their MCQ/multi_select
    options in different positions and can't just copy "the answer in slot
    B". Grading is always by option id (see _grade_mcq_answer /
    _grade_multi_select_answer), never by position, so this affects display
    only. Returns None (not an empty JSON object) when randomization is off
    or the exam has no options-bearing questions, so callers can treat
    "no stored order" and "randomization disabled" the same way: fall back
    to the options' natural (authored) order."""
    if not exam.randomize_options:
        return None
    order: dict[str, list[int]] = {}
    for section in exam.sections:
        for question in section.questions:
            if question.question_type in (QuestionType.MCQ, QuestionType.MULTI_SELECT) and question.options:
                option_ids = [option.id for option in question.options]
                random.shuffle(option_ids)
                order[str(question.id)] = option_ids
    return json.dumps(order) if order else None


def get_option_order(attempt, question_id: int) -> list[int] | None:
    """The stored shuffle for one question in one attempt (see
    _build_option_order), or None if there isn't one -- either because
    randomize_options was off for this exam, the question has no options
    (coding), or the stored JSON is somehow malformed. Callers fall back to
    the options' natural order in every None case."""
    if not attempt.option_order_json:
        return None
    try:
        order_map = json.loads(attempt.option_order_json)
    except (ValueError, TypeError):
        return None
    ids = order_map.get(str(question_id))
    return [int(i) for i in ids] if isinstance(ids, list) else None


def ensure_attempt_is_active(db: Session, student_id: int, attempt_id: int):
    attempt = _get_owned_in_progress_attempt(db, student_id, attempt_id)
    if remaining_seconds(attempt) <= 0:
        attempt = attempt_repository.mark_attempt_submitted(db, attempt, AttemptStatus.AUTO_SUBMITTED)
        _compute_and_store_result(db, attempt)
        raise _expired_auto_submit_error(attempt)
    return attempt


def get_answers_map(db: Session, student_id: int, attempt_id: int) -> dict[int, int | bool | None]:
    """Full question_id -> "is this answered" map for an in-progress attempt,
    used by the frontend to repaint the question navigator immediately after
    a page reload / reconnect, instead of only learning each question's
    answered state as the student happens to revisit it. MCQ questions map to
    their selected_option_id; coding questions map to True/None depending on
    whether any code has been saved (the navigator only needs truthiness)."""
    attempt = _get_owned_in_progress_attempt(db, student_id, attempt_id)
    result = {}
    for answer in attempt_repository.get_answers_for_attempt(db, attempt.id):
        if answer.selected_option_id is not None:
            result[answer.question_id] = answer.selected_option_id
        elif answer.selected_option_ids_json:
            try:
                ids = json.loads(answer.selected_option_ids_json)
            except (ValueError, TypeError):
                ids = None
            result[answer.question_id] = ids or None
        elif answer.code_submission:
            result[answer.question_id] = True
        else:
            result[answer.question_id] = None
    return result


def save_answer(db: Session, student_id: int, attempt_id: int, question_id: int, selected_option_id: int | None):
    attempt = ensure_attempt_is_active(db, student_id, attempt_id)
    question_ids = {int(x) for x in attempt.question_order.split(",")}
    if question_id not in question_ids:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Question does not belong to this attempt.")
    question = exam_repository.get_question(db, question_id)
    if not question or question.section.exam_id != attempt.exam_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Question does not belong to this exam.")
    if question.question_type != QuestionType.MCQ:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "This question is not a single-answer multiple-choice question.")
    if selected_option_id is not None and selected_option_id not in {option.id for option in question.options}:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Selected option does not belong to this question.")
    return attempt_repository.upsert_answer(db, attempt.id, question_id, selected_option_id)


def save_multi_select_answer(db: Session, student_id: int, attempt_id: int, question_id: int, selected_option_ids: list[int] | None):
    """Counterpart to save_answer for MULTI_SELECT questions: persists a set
    of chosen option ids rather than a single one. An empty/None list clears
    the answer (leaves the question unattempted), matching save_answer's
    treatment of selected_option_id=None."""
    attempt = ensure_attempt_is_active(db, student_id, attempt_id)
    question_ids = {int(x) for x in attempt.question_order.split(",")}
    if question_id not in question_ids:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Question does not belong to this attempt.")
    question = exam_repository.get_question(db, question_id)
    if not question or question.section.exam_id != attempt.exam_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Question does not belong to this exam.")
    if question.question_type != QuestionType.MULTI_SELECT:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "This question is not a multiple-selection question.")
    selected_option_ids = selected_option_ids or []
    valid_ids = {option.id for option in question.options}
    if any(option_id not in valid_ids for option_id in selected_option_ids):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Selected option does not belong to this question.")
    # Dedupe and store a stable order so repeated saves of the same set don't
    # thrash the stored JSON string.
    deduped = sorted(set(selected_option_ids))
    payload = json.dumps(deduped) if deduped else None
    return attempt_repository.upsert_multi_answer(db, attempt.id, question_id, payload)


def save_code_answer(db: Session, student_id: int, attempt_id: int, question_id: int, source_code: str):
    """Pure autosave -- persists the student's current code without running
    it, so every keystroke-debounced save stays cheap. Grading (running
    against every test case) happens once, at final submit time."""
    attempt, question = _get_owned_coding_question(db, student_id, attempt_id, question_id)
    return attempt_repository.upsert_code_answer(db, attempt.id, question_id, source_code)


def run_sample_test_cases(db: Session, student_id: int, attempt_id: int, question_id: int, source_code: str) -> dict:
    """Ungraded "Run" button: executes the student's current code against
    only the sample test cases (never the hidden ones), for quick feedback
    while they're still working. Does not persist anything."""
    _attempt, question = _get_owned_coding_question(db, student_id, attempt_id, question_id)
    return code_runner_service.run_against_test_cases(
        question.language, source_code, question.sample_test_cases, question.time_limit_seconds,
    )


def _get_owned_coding_question(db: Session, student_id: int, attempt_id: int, question_id: int):
    attempt = ensure_attempt_is_active(db, student_id, attempt_id)
    question_ids = {int(x) for x in attempt.question_order.split(",")}
    if question_id not in question_ids:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Question does not belong to this attempt.")
    question = exam_repository.get_question(db, question_id)
    if not question or question.section.exam_id != attempt.exam_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Question does not belong to this exam.")
    if question.question_type != QuestionType.CODING:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "This question is not a coding question.")
    return attempt, question


def submit_attempt(db: Session, student_id: int, attempt_id: int, auto: bool = False):
    attempt = _get_owned_in_progress_attempt(db, student_id, attempt_id)
    status_value = AttemptStatus.AUTO_SUBMITTED if auto else AttemptStatus.SUBMITTED
    attempt = attempt_repository.mark_attempt_submitted(db, attempt, status_value)
    return _compute_and_store_result(db, attempt)


def _compute_and_store_result(db: Session, attempt):
    question_ids = [int(x) for x in attempt.question_order.split(",")]
    answers = {answer.question_id: answer for answer in attempt_repository.get_answers_for_attempt(db, attempt.id)}
    total_marks = scored_marks = correct = incorrect = unattempted = 0

    for question_id in question_ids:
        question = exam_repository.get_question(db, question_id)
        if not question:
            continue
        total_marks += question.marks
        answer = answers.get(question_id)

        if question.question_type == QuestionType.CODING:
            earned, outcome = _grade_coding_answer(db, question, answer)
        elif question.question_type == QuestionType.MULTI_SELECT:
            earned, outcome = _grade_multi_select_answer(question, answer)
        else:
            earned, outcome = _grade_mcq_answer(question, answer)

        scored_marks += earned
        if outcome == "unattempted":
            unattempted += 1
        elif outcome == "correct":
            correct += 1
        else:
            incorrect += 1

    existing_result = attempt_repository.get_result(db, attempt.id)
    if existing_result:
        return existing_result

    # The check above and this insert aren't atomic: two requests racing to
    # submit the same attempt (a double-click, or a manual submit racing the
    # timer's auto-submit) can both see no existing result and both reach
    # here. exam_results.attempt_id is unique, so the loser hits an
    # IntegrityError rather than silently corrupting data -- but without this
    # catch that error was uncaught here and bubbled up to the global
    # SQLAlchemyError handler as an opaque 503, when the right answer is just
    # "someone else already created it, hand back that one."
    try:
        return attempt_repository.create_result(db, attempt.id, {
            "total_marks": total_marks,
            "scored_marks": scored_marks,
            "percentage": round((scored_marks / total_marks) * 100, 2) if total_marks else 0.0,
            "correct_count": correct,
            "incorrect_count": incorrect,
            "unattempted_count": unattempted,
        })
    except IntegrityError:
        db.rollback()
        existing_result = attempt_repository.get_result(db, attempt.id)
        if existing_result:
            return existing_result
        raise


def _grade_from_stored_coding_result(question, answer) -> tuple[int, str, dict | None]:
    """Per-question breakdown for the report, reusing the test-case results
    already stored at submit time (see _grade_coding_answer) instead of
    re-running the student's code -- a report can be opened any number of
    times and must always be fast and always reflect exactly what grading
    produced, not a fresh (and possibly different, if the sandbox is flaky)
    execution."""
    if not answer or not answer.code_submission or not answer.code_submission.strip():
        return 0, "unattempted", None
    if not answer.code_test_results_json:
        return 0, "incorrect", None
    try:
        run_result = json.loads(answer.code_test_results_json)
    except (ValueError, TypeError):
        return 0, "incorrect", None
    if not run_result.get("available"):
        return 0, "incorrect", run_result
    total_cases = len(run_result.get("results", [])) or 1
    passed_cases = sum(1 for case in run_result.get("results", []) if case.get("passed"))
    earned = round(question.marks * passed_cases / total_cases)
    outcome = "correct" if run_result.get("all_passed") else "incorrect"
    return earned, outcome, run_result


def build_full_report(db: Session, attempt) -> dict:
    """Assembles the comprehensive post-exam report: exam + candidate details,
    timing, pass/fail, and a full per-question breakdown (text, options,
    selected vs. correct answer, marks awarded, explanation). Built entirely
    from already-graded data -- never re-executes student code -- so opening
    a report is always fast and always matches what grading actually produced.
    """
    exam = attempt.exam
    student = attempt.student
    result = attempt_repository.get_result(db, attempt.id)
    question_ids = [int(x) for x in attempt.question_order.split(",")]
    answers = {a.question_id: a for a in attempt_repository.get_answers_for_attempt(db, attempt.id)}

    questions_report = []
    for question_id in question_ids:
        question = exam_repository.get_question(db, question_id)
        if not question:
            continue
        answer = answers.get(question_id)

        selected_option_ids = None
        correct_option_ids = None

        if question.question_type == QuestionType.CODING:
            earned, outcome, run_result = _grade_from_stored_coding_result(question, answer)
            options_out = []
            selected_option_id = None
            selected_answer = answer.code_submission if answer else None
            correct_answer = None  # no single "correct answer" string for open-ended code
        elif question.question_type == QuestionType.MULTI_SELECT:
            earned, outcome = _grade_multi_select_answer(question, answer)
            run_result = None
            options_out = [{"id": option.id, "text": option.text} for option in question.options]
            selected_option_id = None
            selected_ids = _parse_selected_option_ids(answer)
            selected_option_ids = selected_ids
            correct_option_ids = [option.id for option in question.options if option.is_correct]
            selected_texts = [o.text for o in question.options if o.id in selected_ids]
            correct_texts = [o.text for o in question.options if o.is_correct]
            selected_answer = ", ".join(selected_texts) if selected_texts else None
            correct_answer = ", ".join(correct_texts) if correct_texts else None
        else:
            earned, outcome = _grade_mcq_answer(question, answer)
            run_result = None
            options_out = [{"id": option.id, "text": option.text} for option in question.options]
            selected_option_id = answer.selected_option_id if answer else None
            selected_option = next((o for o in question.options if o.id == selected_option_id), None)
            correct_option = next((o for o in question.options if o.is_correct), None)
            selected_answer = selected_option.text if selected_option else None
            correct_answer = correct_option.text if correct_option else None

        questions_report.append({
            "question_id": question.id,
            "question_type": question.question_type.value,
            "text": question.text,
            "marks": question.marks,
            "marks_awarded": earned,
            "options": options_out,
            "selected_option_id": selected_option_id,
            "selected_option_ids": selected_option_ids,
            "correct_option_ids": correct_option_ids,
            "selected_answer": selected_answer,
            "correct_answer": correct_answer,
            "outcome": outcome,  # "correct" | "incorrect" | "unattempted"
            "explanation": question.explanation,
            "code_test_results": run_result,
        })

    time_taken_seconds = None
    if attempt.started_at and attempt.submitted_at:
        time_taken_seconds = int((_as_utc(attempt.submitted_at) - _as_utc(attempt.started_at)).total_seconds())

    percentage = result.percentage if result else 0.0
    passed = (percentage >= exam.pass_percentage) if result else None

    return {
        "attempt_id": attempt.id,
        "exam_id": exam.id,
        "exam_title": exam.title,
        "exam_description": exam.description,
        "candidate_name": student.user.full_name,
        "candidate_email": student.user.email,
        "roll_number": student.roll_number,
        "duration_minutes": exam.duration_minutes,
        "started_at": attempt.started_at,
        "submitted_at": attempt.submitted_at,
        "time_taken_seconds": time_taken_seconds,
        "status": attempt.status.value,
        "total_marks": result.total_marks if result else 0,
        "scored_marks": result.scored_marks if result else 0,
        "percentage": percentage,
        "pass_percentage": exam.pass_percentage,
        "passed": passed,
        "correct_count": result.correct_count if result else 0,
        "incorrect_count": result.incorrect_count if result else 0,
        "unattempted_count": result.unattempted_count if result else 0,
        "questions": questions_report,
    }


def build_staff_report(db: Session, attempt) -> dict:
    """Extends build_full_report with the identity, risk, and violation
    detail that only staff (an admin, or the exam's owning examiner) should
    see -- deliberately not merged into build_full_report itself, since that
    shape is also returned to the student about their own attempt, and none
    of this is theirs to see: not their own risk-score framing, and
    certainly not an examiner's or admin's private notes about them.

    Unlike the student-facing report, this one doesn't require a finished
    ExamResult to be useful -- build_full_report already degrades cleanly to
    zeroed marks/percentage when `result` is None, so a staff member can
    still pull identity, timing, and violations for an attempt that is still
    in progress or was never submitted.
    """
    base = build_full_report(db, attempt)
    student = attempt.student
    identity = identity_service.verification_state(db, student)
    events = proctor_repository.list_events_for_attempt(db, attempt.id)
    risk_score, risk_tier = admin_service.risk_score_and_tier(events, attempt.status.value)
    summary = admin_service.proctoring_summary(events, risk_tier, attempt.status.value)

    violation_type_counts: dict[str, int] = {}
    for event in events:
        violation_type_counts[event.event_type.value] = violation_type_counts.get(event.event_type.value, 0) + 1

    return {
        **base,
        "candidate_id": student.id,
        "roll_number": student.roll_number,
        "face_registered": identity["face_registered"],
        "id_verified": identity["id_verified"],
        "identity_locked": identity["identity_locked"],
        "examiner_name": attempt.exam.examiner.user.full_name,
        "examiner_id": attempt.exam.examiner_id,
        "risk_score": risk_score,
        "risk_tier": risk_tier,
        "proctoring_summary": summary,
        "total_violations": len(events),
        "violation_type_counts": violation_type_counts,
        "violation_timeline": [{
            "id": event.id, "created_at": event.created_at, "event_type": event.event_type.value,
            "severity": event.severity.value, "description": event.description,
            "has_screenshot": bool(event.screenshot_path), "admin_decision": event.admin_decision.value,
        } for event in events],
        "examiner_comment": attempt.examiner_comment,
        "admin_comment": attempt.admin_comment,
    }


def set_attempt_comment(db: Session, user, attempt_id: int, comment: str) -> dict:
    """Writes to exactly one of the two comment columns, chosen by the
    caller's role -- never both, and never a role's own comment on someone
    else's exam (an examiner may only annotate an attempt on an exam they
    own). See StudentExamAttempt.examiner_comment/admin_comment.
    """
    attempt = attempt_repository.get_attempt(db, attempt_id)
    if not attempt:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Attempt not found.")

    cleaned = comment.strip() or None
    if user.role.name == "admin":
        attempt.admin_comment = cleaned
    elif user.role.name == "examiner":
        examiner = user.examiner_profile
        if not examiner or attempt.exam.examiner_id != examiner.id:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "You do not own this exam.")
        attempt.examiner_comment = cleaned
    else:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only staff can leave a comment on an attempt.")

    db.commit()
    return {"examiner_comment": attempt.examiner_comment, "admin_comment": attempt.admin_comment}


def _grade_mcq_answer(question, answer) -> tuple[int, str]:
    selected_id = answer.selected_option_id if answer else None
    if selected_id is None:
        return 0, "unattempted"
    correct_option = next((option for option in question.options if option.is_correct), None)
    if correct_option and correct_option.id == selected_id:
        return question.marks, "correct"
    return 0, "incorrect"


def _parse_selected_option_ids(answer) -> list[int]:
    if not answer or not answer.selected_option_ids_json:
        return []
    try:
        ids = json.loads(answer.selected_option_ids_json)
    except (ValueError, TypeError):
        return []
    return [int(i) for i in ids] if isinstance(ids, list) else []


def _grade_multi_select_answer(question, answer) -> tuple[int, str]:
    """All-or-nothing exact-set grading: the student's chosen options must
    equal the correct set precisely (see QuestionType.MULTI_SELECT's
    docstring) -- no partial credit for getting some but not all right, and
    picking even one extra, wrong option fails the question."""
    selected_ids = _parse_selected_option_ids(answer)
    if not selected_ids:
        return 0, "unattempted"
    correct_ids = {option.id for option in question.options if option.is_correct}
    if set(selected_ids) == correct_ids:
        return question.marks, "correct"
    return 0, "incorrect"


def _grade_coding_answer(db: Session, question, answer) -> tuple[int, str]:
    """Runs the student's saved code against every test case (sample +
    hidden) exactly once, at submit time, and stores the per-test-case
    results back onto the answer row for later review (e.g. a future
    'view my test results' panel on the results page)."""
    source_code = answer.code_submission if answer else None
    if not source_code or not source_code.strip():
        return 0, "unattempted"

    run_result = code_runner_service.run_against_test_cases(
        question.language, source_code, question.test_cases, question.time_limit_seconds,
    )
    if answer is not None:
        attempt_repository.set_code_test_results(db, answer.id, json.dumps(run_result))

    if not run_result["available"]:
        return 0, "incorrect"  # attempted, but couldn't be verified (execution unavailable)

    total_cases = len(run_result["results"]) or 1
    passed_cases = sum(1 for case in run_result["results"] if case["passed"])
    earned = round(question.marks * passed_cases / total_cases)
    outcome = "correct" if run_result["all_passed"] else "incorrect"
    return earned, outcome


def reset_student_attempt(db: Session, examiner_id: int, exam_id: int, student_id: int, reason: str):
    """Gives a student a clean retake after a disruption that leaves their
    attempt unusable -- connectivity loss, a browser crash, a power failure,
    a stuck device, or an AI-proctoring interruption. Works regardless of the
    attempt's current status (in progress, submitted, auto-submitted, or
    terminated): the whole point is to undo whatever state it's stuck in.

    student_exam_attempts has a unique (student_id, exam_id) constraint, so a
    fresh retake cannot be created *alongside* the old row -- it must replace
    it. The old attempt (and, via cascade, its answers and result) is deleted
    outright, but only after an AttemptReset audit row records who did this,
    when, why, and what the attempt looked like beforehand -- see
    app.models.attempt.AttemptReset.
    """
    exam = exam_repository.get_exam(db, exam_id)
    if not exam:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Exam not found.")
    if exam.examiner_id != examiner_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You do not own this exam.")

    attempt = attempt_repository.get_existing_attempt(db, student_id, exam_id)
    if not attempt:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "This student has not attempted this exam.")

    attempt_repository.create_attempt_reset(db, {
        "exam_id": exam_id,
        "student_id": student_id,
        "examiner_id": examiner_id,
        "previous_attempt_id": attempt.id,
        "previous_status": attempt.status.value,
        "reason": reason,
    })
    attempt_repository.delete_attempt(db, attempt)


def list_attempt_resets(db: Session, exam_id: int):
    return attempt_repository.list_attempt_resets_for_exam(db, exam_id)


def _get_owned_in_progress_attempt(db: Session, student_id: int, attempt_id: int):
    """Gate for every read and write inside a live attempt.

    Owning the attempt is not sufficient on its own. Gating only
    `start_attempt` left roughly a dozen endpoints -- question text, saved
    answers, code execution, submit, result -- reachable by a student whose
    access had since been revoked: removed from the roster, or excluded when
    the exam was switched to an allow-list. They could not start a *new*
    attempt, but the one they already had kept working, which defeats the
    point. The access check therefore lives here, not just at the door.
    """
    attempt = attempt_repository.get_attempt(db, attempt_id)
    if not attempt or attempt.student_id != student_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Attempt not found.")
    organization_service.require_student_access(db, attempt.student, attempt.exam)
    if attempt.status != AttemptStatus.IN_PROGRESS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "This attempt is no longer in progress.")
    return attempt