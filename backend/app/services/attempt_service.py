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
    """A Redis lock against double-submitting the same attempt -- best-effort,
    unlike every other lock in this app (see app/core/locks.py), because exam
    submission's actual correctness does NOT depend on it: `finalize_attempt`
    already reads the attempt row with `SELECT ... FOR UPDATE`
    (attempt_repository.get_attempt_for_update) and is written to be
    idempotent for an already-submitted attempt, and `_compute_and_store_result`
    catches the IntegrityError a genuine race produces. Those two mechanisms
    were correct before Redis came back into this stack and remain the actual
    guarantee.

    So this lock is an optimisation (skip redundant grading work when two
    requests for the same attempt overlap), not a safety net -- and precisely
    because of that, Redis being unreachable must not turn into a candidate's
    submission failing mid-exam with a 503. It logs and proceeds without the
    lock instead, falling back to the Postgres-level guarantees above.
    """
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
    elif attempt.status in _FINISHED_STATUSES and not attempt_repository.get_result(db, attempt.id):
        # Submitted but never graded -- grading runs outside the finalize
        # transaction (see finalize_attempt) so a sandbox timeout or a restart
        # can leave exactly this state. Repairing it on read is what stops it
        # being permanent, and costs one indexed lookup for every attempt that
        # is already fine.
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
    """Validate and stage ONE final answer inside the finalize transaction.

    Reuses the same ownership and type checks the autosave endpoints apply,
    because "it arrived attached to a submit" is not a reason to trust an
    option id. A question that isn't part of this attempt, or an option that
    belongs to a different question, is rejected here exactly as it would be
    from PUT /answer -- otherwise finalize would be a hole straight through
    every check the autosave path performs.
    """
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
    """Freeze the candidate's final answers and submit, as one transaction.

    This replaces a submit that raced the autosave it depended on. The client
    used to fire the last code save and immediately POST /submit without
    awaiting it; on any connection where the submit won that race the server
    marked the attempt submitted and graded the *previous* version of the code,
    then rejected the save that carried the real answer because the attempt was
    no longer active. The candidate saw a successful submission and was marked
    on work they had already replaced. Automatic timeout submission hit the same
    race, with nobody watching.

    Sending the final answers WITH the submission removes the race rather than
    narrowing it: there is no longer an ordering between them to get wrong. The
    attempt row is locked for the duration, so a manual submit and the timer's
    auto-submit firing together cannot both freeze a different set of answers.

    Idempotent on purpose. A finalize whose response was lost is retried by the
    client -- that is how the expired-pending-submission state recovers -- so an
    attempt that is already submitted returns its existing result instead of an
    error. Without that, the retry that repairs a dropped connection would
    instead show the candidate a failure for a submission that succeeded.

    `auto` is decided here, from the server's own clock, and is deliberately not
    a parameter. It used to be a query string the candidate controlled, which
    let them submit early flagged as a timeout, or late flagged as deliberate --
    an audit field the audited party could set.
    """
    # Ownership and roster access, but deliberately NOT the in-progress check
    # that every other attempt endpoint applies. A finalize arriving for an
    # already-submitted attempt is the retry path, not an error -- rejecting it
    # would mean the retry that repairs a dropped connection reports failure for
    # a submission that succeeded.
    _get_owned_attempt(db, student_id, attempt_id)

    with _best_effort_submission_lock(attempt_id):
        # Re-read under the Postgres row lock. Between the check above and
        # here, the timer's auto-submit may have finished this attempt; the
        # locked read is the one whose answer can be acted on.
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

    # Grading runs AFTER the commit, deliberately outside the transaction and
    # the lock. Coding questions execute the candidate's code in a Docker
    # sandbox, which can take tens of seconds; holding a row lock and a pooled
    # connection open across that would let one exam hall submitting at once
    # exhaust the pool and block every other candidate's autosave. What has to
    # be atomic -- the answers and the submitted status -- already is. Grading
    # is idempotent and re-entrant instead: if it fails here the attempt is
    # submitted with no result, and ensure_result recomputes it the next time
    # anyone opens the result or report.
    return ensure_result(db, attempt)


def ensure_result(db: Session, attempt):
    """The result for a finished attempt, computing it if it is missing.

    Grading deliberately happens outside the finalize transaction (see
    finalize_attempt), which leaves a window where an attempt is submitted but
    ungraded -- a sandbox timeout, a worker restart mid-grade. Before this
    existed that state was permanent: finalize_if_expired only looks at
    IN_PROGRESS attempts, so nothing ever revisited it, and the candidate's
    result endpoint returned 404 forever with their answers sitting in the
    database, graded by nobody.
    """
    result = attempt_repository.get_result(db, attempt.id)
    if result:
        return result
    return _compute_and_store_result(db, attempt)


def submit_attempt(db: Session, student_id: int, attempt_id: int, auto: bool = False):
    """Retained for the server-side callers that submit an attempt with no
    client payload -- the expiry sweep and the lockdown terminator. Candidate
    submissions go through finalize_attempt, which carries their final answers.
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


def build_full_report(db: Session, attempt, *, for_candidate: bool = False) -> dict:
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

    # Whether THIS reader may see the answer key.
    #
    # The candidate report returned correct options, correct-answer text and
    # explanations the instant an attempt was submitted. So the first person to
    # finish held the complete key while everyone else was still writing, and
    # could simply send it to them -- the platform handing out the answers to
    # its own live exam. Staff are unaffected: an examiner reviewing a paper
    # needs the key, and always has.
    # Whether THIS reader may see the score/pass-fail outcome at all -- the
    # examiner's show_results / results_release_mode settings (see
    # Exam.results_released). Checked first because it is the coarser gate:
    # if the score itself is withheld, the answer key must be too (there is
    # no sense in which "you can't see whether you passed, but here's which
    # options were correct" is a sensible state).
    withhold_results = for_candidate and not exam.results_released
    withhold_key = withhold_results or (
        for_candidate and not (exam.answer_key_released and exam.show_answers_on_release)
    )

    questions_report = []
    # No per-question breakdown at all while results are withheld -- outcome
    # and marks_awarded are exactly the "results" this setting exists to
    # delay, and leaving them in the response would defeat it the moment
    # anyone opened their browser's network tab, even with the summary
    # numbers below correctly hidden.
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
            # The outcome stays: a candidate is entitled to know whether they
            # got it right. What is withheld is WHICH answer was correct, which
            # is the part that is useful to someone still writing.
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
    # Both scores. Dismissing a violation used to change admin_decision and
    # nothing else, so a candidate whose flags a reviewer had explicitly cleared
    # stayed labelled high risk -- and the label is what the next person to open
    # the record reads. The summary is built from the ADJUDICATED tier, since
    # that is the conclusion; the automated figure stays visible beside it so a
    # reviewer's judgement can itself be reviewed.
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


def build_exam_csv(db: Session, exam) -> bytes:
    """One CSV row per candidate who has taken this exam so far: name,
    violation count, start/end time, and result.

    Generated fresh from the current database state on every request rather
    than a physically written-and-appended file. A candidate's row needs to
    "appear" the moment they submit and their violation count needs to stay
    correct if a reviewer later re-adjudicates a flag -- both are automatic
    here, for free, since this is just a query every time, and there is no
    separate file on disk that could drift from what the database actually
    holds or that two examiners downloading at once could corrupt each
    other's writes to.
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
    """Gives a student a clean retake after a disruption that leaves their
    attempt unusable -- connectivity loss, a browser crash, a power failure,
    a stuck device, or an AI-proctoring interruption. Works regardless of the
    attempt's current status (in progress, submitted, auto-submitted, or
    terminated): the whole point is to undo whatever state it's stuck in.

    The old attempt is ARCHIVED, not deleted.

    It used to be deleted, because student_exam_attempts had a plain unique
    (student_id, exam_id) constraint and the retake needed the slot. The cost of
    that was everything hanging off the row: answers, the result, the examiner's
    and admin's comments, every proctoring event and every violation screenshot,
    all removed by cascade. What survived was an AttemptReset row holding a
    reason string and an integer pointing at an attempt that no longer existed.

    That is the wrong trade for this feature specifically. A reset is granted
    after something went wrong -- a crash, a disconnection, a proctoring
    interruption, an accusation -- which is exactly the situation where somebody
    may later need to see what actually happened. The evidence was being
    destroyed by the action most likely to precede a request to examine it.

    Uniqueness is now scoped to live attempts (see the partial index on
    StudentExamAttempt), so the archived row can stay where it is. It stops
    counting as this student's attempt -- gone from their results, the
    examiner's list and the analytics -- while remaining readable to staff.
    """
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
    """Gate for every read and write inside a live attempt.

    Owning the attempt is not sufficient on its own. Gating only
    `start_attempt` left roughly a dozen endpoints -- question text, saved
    answers, code execution, submit, result -- reachable by a student whose
    access had since been revoked: removed from the roster, or excluded when
    the exam was switched to an allow-list. They could not start a *new*
    attempt, but the one they already had kept working, which defeats the
    point. The access check therefore lives here, not just at the door.
    """
    attempt = _get_owned_attempt(db, student_id, attempt_id)
    if attempt.status != AttemptStatus.IN_PROGRESS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "This attempt is no longer in progress.")
    return attempt


def _get_owned_attempt(db: Session, student_id: int, attempt_id: int):
    """Ownership and roster access, without requiring the attempt to be live.

    Split out of _get_owned_in_progress_attempt for finalize_attempt, which must
    accept a retry against an already-submitted attempt while still refusing one
    that belongs to somebody else.
    """
    attempt = attempt_repository.get_attempt(db, attempt_id)
    if not attempt or attempt.student_id != student_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Attempt not found.")
    organization_service.require_student_access(db, attempt.student, attempt.exam)
    return attempt