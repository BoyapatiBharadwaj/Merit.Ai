"""Business logic for starting, answering, and submitting exam attempts."""
import csv
import io
import json
import logging
import random
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.locks import distributed_lock
from app.core.redis_client import RedisUnavailableError
from app.repositories import admin_repository, attempt_repository, exam_repository, proctor_repository
from app.models.enums import AttemptStatus, ExamStatus, QuestionType
from app.models.student import Student
from app.services import admin_service, code_runner_service, identity_service, organization_service

logger = logging.getLogger("app")


@contextmanager
def _best_effort_submission_lock(attempt_id: int):
    """A Redis lock against double-submitting the same attempt."""
    try:
        with distributed_lock(f"attempt-submit:{attempt_id}", timeout=30, blocking_timeout=5) as acquired:
            yield acquired
    except RedisUnavailableError:
        logger.warning("Redis unavailable while locking attempt %s submission; proceeding without it -- "
                       "the row-level lock in get_attempt_for_update is what actually guarantees "
                       "correctness here.", attempt_id)
        yield True


# An attempt in any of these is over: no further answers, and a result is
# expected to exist for it.
_FINISHED_STATUSES = (AttemptStatus.SUBMITTED, AttemptStatus.AUTO_SUBMITTED, AttemptStatus.TERMINATED)


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def remaining_seconds(attempt) -> int:
    deadline = _as_utc(attempt.started_at) + timedelta(minutes=attempt.exam.duration_minutes)
    return max(0, int((deadline - datetime.now(timezone.utc)).total_seconds()))


def _expired_auto_submit_error(attempt) -> HTTPException:
    """A structured 400, not just a string message."""
    return HTTPException(status.HTTP_400_BAD_REQUEST, detail={
        "code": "attempt_expired_auto_submitted",
        "message": "Your exam time expired and your attempt was automatically submitted.",
        "attempt_id": attempt.id,
    })


def finalize_if_expired(db: Session, attempt):
    """Opportunistically auto-submits an in-progress attempt whose own deadline (started_at +
    duration) has already passed.
    """
    if attempt.status == AttemptStatus.IN_PROGRESS and remaining_seconds(attempt) <= 0:
        attempt = attempt_repository.mark_attempt_submitted(db, attempt, AttemptStatus.AUTO_SUBMITTED)
        _compute_and_store_result(db, attempt)
    elif attempt.status in _FINISHED_STATUSES and not attempt_repository.get_result(db, attempt.id):
        # Submitted but never graded -- grading runs outside the finalize transaction (see
        # finalize_attempt) so a sandbox timeout or a restart can leave exactly this state.
        _compute_and_store_result(db, attempt)
    return attempt


def start_attempt(db: Session, student_id: int, exam_id: int):
    exam = exam_repository.get_exam(db, exam_id)
    now = datetime.now(timezone.utc)
    if not exam or exam.status != ExamStatus.PUBLISHED:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Exam not available.")

    # THE access check, not a duplicate of the list filter.
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
    """Computes this attempt's fixed per-question option display
    order, if the exam has randomize_options enabled.
    """
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
    _build_option_order), or None if there isn't one.
    """
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
    used by the frontend to repaint the question navigator immediately
    after a page reload / reconnect, instead of only learning each
    question's answered state as the student happens to revisit it.
    """
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


def save_answer(db: Session, student_id: int, attempt_id: int, question_id: int, selected_option_id: int | None,
                *, answer_version: int | None = None, idempotency_key: str | None = None):
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
    return attempt_repository.upsert_answer(
        db, attempt.id, question_id, selected_option_id,
        answer_version=answer_version, idempotency_key=idempotency_key,
    )


def save_multi_select_answer(db: Session, student_id: int, attempt_id: int, question_id: int,
                             selected_option_ids: list[int] | None,
                             *, answer_version: int | None = None, idempotency_key: str | None = None):
    """Counterpart to save_answer for MULTI_SELECT questions: persists a set of chosen option
    ids rather than a single one.
    """
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
    return attempt_repository.upsert_multi_answer(
        db, attempt.id, question_id, payload,
        answer_version=answer_version, idempotency_key=idempotency_key,
    )


def save_code_answer(db: Session, student_id: int, attempt_id: int, question_id: int, source_code: str,
                     *, answer_version: int | None = None, idempotency_key: str | None = None):
    """Pure autosave -- persists the student's current code without running
    it, so every keystroke-debounced save stays cheap. Grading (running
    against every test case) happens once, at final submit time."""
    attempt, question = _get_owned_coding_question(db, student_id, attempt_id, question_id)
    return attempt_repository.upsert_code_answer(
        db, attempt.id, question_id, source_code,
        answer_version=answer_version, idempotency_key=idempotency_key,
    )


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


def _apply_final_answer(db: Session, attempt, item) -> None:
    """Validate and stage ONE final answer inside the finalize transaction."""
    question_ids = {int(x) for x in attempt.question_order.split(",")}
    if item.question_id not in question_ids:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Question does not belong to this attempt.")
    question = exam_repository.get_question(db, item.question_id)
    if not question or question.section.exam_id != attempt.exam_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Question does not belong to this exam.")

    valid_ids = {option.id for option in question.options}

    if question.question_type == QuestionType.CODING:
        if item.source_code is None:
            return
        source_code = item.source_code

        def _apply(answer):
            answer.code_submission = source_code
    elif question.question_type == QuestionType.MULTI_SELECT:
        if item.selected_option_ids is None:
            return
        if any(option_id not in valid_ids for option_id in item.selected_option_ids):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Selected option does not belong to this question.")
        deduped = sorted(set(item.selected_option_ids))
        payload = json.dumps(deduped) if deduped else None

        def _apply(answer):
            answer.selected_option_ids_json = payload
    else:
        if item.selected_option_id is not None and item.selected_option_id not in valid_ids:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Selected option does not belong to this question.")
        selected_option_id = item.selected_option_id

        def _apply(answer):
            answer.selected_option_id = selected_option_id

    attempt_repository.stage_answer(
        db, attempt.id, item.question_id, _apply,
        answer_version=item.answer_version, idempotency_key=item.idempotency_key,
    )


def finalize_attempt(db: Session, student_id: int, attempt_id: int, *, final_answers=None):
    """Freeze the candidate's final answers and submit, as one transaction."""
    # Ownership and roster access, but deliberately NOT the
    # in-progress check that every other attempt endpoint applies.
    _get_owned_attempt(db, student_id, attempt_id)

    with _best_effort_submission_lock(attempt_id):
        # Re-read under the Postgres row lock. Between the check above and here, the timer's
        # auto-submit may have finished this attempt.
        attempt = attempt_repository.get_attempt_for_update(db, attempt_id)
        if attempt is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Attempt not found.")
        if attempt.status != AttemptStatus.IN_PROGRESS:
            db.commit()  # release the lock; someone else already finalized
            return ensure_result(db, attempt)

        try:
            for item in (final_answers or []):
                _apply_final_answer(db, attempt, item)

            expired = remaining_seconds(attempt) <= 0
            attempt_repository.mark_attempt_submitted(
                db, attempt,
                AttemptStatus.AUTO_SUBMITTED if expired else AttemptStatus.SUBMITTED,
                commit=False,
            )
            db.commit()
        except Exception:
            db.rollback()
            raise
        db.refresh(attempt)

    # Grading runs AFTER the commit, deliberately outside the transaction and the lock.
    return ensure_result(db, attempt)


def ensure_result(db: Session, attempt):
    """The result for a finished attempt, computing it if it is missing."""
    result = attempt_repository.get_result(db, attempt.id)
    if result:
        return result
    return _compute_and_store_result(db, attempt)


def submit_attempt(db: Session, student_id: int, attempt_id: int, auto: bool = False):
    """Retained for the server-side callers that submit an attempt with no client payload -- the
    expiry sweep and the lockdown terminator.
    """
    with _best_effort_submission_lock(attempt_id):
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

    # The check above and this insert aren't atomic.
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
    """Per-question breakdown for the report, reusing the test-case results already stored at
    submit time (see _grade_coding_answer) instead of re-running the student's code.
    """
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


def build_full_report(db: Session, attempt, *, for_candidate: bool = False) -> dict:
    """Assembles the comprehensive post-exam report: exam + candidate
    details, timing, pass/fail, and a full per-question breakdown (text,
    options, selected vs. correct answer, marks awarded, explanation).
    """
    exam = attempt.exam
    student = attempt.student
    result = attempt_repository.get_result(db, attempt.id)
    question_ids = [int(x) for x in attempt.question_order.split(",")]
    answers = {a.question_id: a for a in attempt_repository.get_answers_for_attempt(db, attempt.id)}

    # Whether THIS reader may see the answer key. The candidate report returned correct options,
    # correct-answer text and explanations the instant an attempt was submitted.
    withhold_results = for_candidate and not exam.results_released
    withhold_key = withhold_results or (
        for_candidate and not (exam.answer_key_released and exam.show_answers_on_release)
    )

    questions_report = []
    # No per-question breakdown at all while results are withheld.
    for question_id in ([] if withhold_results else question_ids):
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
            "correct_option_ids": None if withhold_key else correct_option_ids,
            "selected_answer": selected_answer,
            "correct_answer": None if withhold_key else correct_answer,
            # The outcome stays: a candidate is entitled to know whether they got it right.
            "outcome": outcome,  # "correct" | "incorrect" | "unattempted"
            "explanation": None if withhold_key else question.explanation,
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
        "results_released": not withhold_results,
        "total_marks": None if withhold_results else (result.total_marks if result else 0),
        "scored_marks": None if withhold_results else (result.scored_marks if result else 0),
        "percentage": None if withhold_results else percentage,
        "pass_percentage": exam.pass_percentage,
        "passed": None if withhold_results else passed,
        "answers_released": not withhold_key,
        "release_results_at": exam.release_results_at,
        "correct_count": None if withhold_results else (result.correct_count if result else 0),
        "incorrect_count": None if withhold_results else (result.incorrect_count if result else 0),
        "unattempted_count": None if withhold_results else (result.unattempted_count if result else 0),
        "questions": questions_report,
    }


def build_staff_report(db: Session, attempt) -> dict:
    """Extends build_full_report with the identity, risk, and violation detail that only staff
    (an admin, or the exam's owning examiner) should see.
    """
    base = build_full_report(db, attempt)
    student = attempt.student
    identity = identity_service.verification_state(db, student)
    events = proctor_repository.list_events_for_attempt(db, attempt.id)
    # Both scores.
    risk = admin_service.adjudicated_risk(events, attempt.status.value)
    risk_score = risk["adjudicated_score"]
    risk_tier = risk["adjudicated_tier"]
    summary = admin_service.proctoring_summary(events, risk_tier, attempt.status.value)

    violation_type_counts: dict[str, int] = {}
    for event in events:
        violation_type_counts[event.event_type.value] = violation_type_counts.get(event.event_type.value, 0) + 1

    return {
        **base,
        "risk": risk,
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
    """Writes to exactly one of the two comment columns, chosen by the caller's role."""
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


def build_exam_csv(db: Session, exam) -> bytes:
    """One CSV row per candidate who has taken this exam so
    far: name, violation count, start/end time, and result.
    """
    attempts = sorted(
        exam.attempts,
        key=lambda a: a.started_at or datetime.min.replace(tzinfo=timezone.utc),
    )
    attempt_ids = [a.id for a in attempts]
    results_by_attempt = attempt_repository.results_for_attempts(db, attempt_ids)
    violation_counts = admin_repository.violation_counts_for_attempts(db, attempt_ids)

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([
        "Student Name", "Roll Number", "Violations", "Start Time", "End Time",
        "Status", "Score (%)", "Result",
    ])
    for attempt in attempts:
        student = attempt.student
        result = results_by_attempt.get(attempt.id)
        result_label = ""
        if result is not None:
            result_label = "Passed" if result.percentage >= exam.pass_percentage else "Failed"
        writer.writerow([
            student.user.full_name if student and student.user else "",
            student.roll_number if student else "",
            violation_counts.get(attempt.id, 0),
            attempt.started_at.isoformat() if attempt.started_at else "",
            attempt.submitted_at.isoformat() if attempt.submitted_at else "",
            attempt.status.value,
            f"{result.percentage:.1f}" if result is not None else "",
            result_label,
        ])
    return buffer.getvalue().encode("utf-8")


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
    """Gives a student a clean retake after a disruption that leaves their attempt unusable."""
    exam = exam_repository.get_exam(db, exam_id)
    if not exam:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Exam not found.")
    if exam.examiner_id != examiner_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You do not own this exam.")

    attempt = attempt_repository.get_existing_attempt(db, student_id, exam_id)
    if not attempt:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "This student has not attempted this exam.")

    try:
        reset = attempt_repository.create_attempt_reset(db, {
            "exam_id": exam_id,
            "student_id": student_id,
            "examiner_id": examiner_id,
            "previous_attempt_id": attempt.id,
            "previous_status": attempt.status.value,
            "reason": reason,
        }, commit=False)
        db.flush()
        attempt_repository.archive_attempt(db, attempt, reset_id=reset.id, commit=False)
        db.commit()
    except Exception:
        db.rollback()
        raise
    return reset


def list_attempt_resets(db: Session, exam_id: int):
    return attempt_repository.list_attempt_resets_for_exam(db, exam_id)


def _get_owned_in_progress_attempt(db: Session, student_id: int, attempt_id: int):
    """Gate for every read and write inside a live attempt."""
    attempt = _get_owned_attempt(db, student_id, attempt_id)
    if attempt.status != AttemptStatus.IN_PROGRESS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "This attempt is no longer in progress.")
    return attempt


def _get_owned_attempt(db: Session, student_id: int, attempt_id: int):
    """Ownership and roster access, without requiring the attempt to be live."""
    attempt = attempt_repository.get_attempt(db, attempt_id)
    if not attempt or attempt.student_id != student_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Attempt not found.")
    organization_service.require_student_access(db, attempt.student, attempt.exam)
    return attempt