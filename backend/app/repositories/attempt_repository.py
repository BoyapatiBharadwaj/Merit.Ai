"""
Data access for StudentExamAttempt, StudentAnswer, ExamResult tables.
"""
from datetime import datetime, timezone

from sqlalchemy.orm import Session, joinedload
from app.models.attempt import AttemptReset, StudentExamAttempt, StudentAnswer, ExamResult
from app.models.enums import AttemptStatus


def get_attempt(db: Session, attempt_id: int) -> StudentExamAttempt | None:
    return db.query(StudentExamAttempt).filter(StudentExamAttempt.id == attempt_id).first()


def get_existing_attempt(db: Session, student_id: int, exam_id: int) -> StudentExamAttempt | None:
    return (
        db.query(StudentExamAttempt)
        .filter(StudentExamAttempt.student_id == student_id, StudentExamAttempt.exam_id == exam_id)
        .first()
    )


def create_attempt(db: Session, student_id: int, exam_id: int, question_order: str,
                    option_order_json: str | None = None) -> StudentExamAttempt:
    attempt = StudentExamAttempt(
        student_id=student_id, exam_id=exam_id, question_order=question_order,
        option_order_json=option_order_json,
    )
    db.add(attempt)
    db.commit()
    db.refresh(attempt)
    return attempt



# --- autosave concurrency control ---------------------------------------------
#
# Shared by all three answer types (MCQ, multi-select, code), because the race
# and its fix are identical for each -- only the column being written differs.
# See StudentAnswer.answer_version for why the version is client-owned.

class AnswerWriteOutcome:
    """Why a write was or wasn't applied. Returned alongside the row so the API
    can tell the client the truth instead of an unconditional `saved: true`."""

    APPLIED = "applied"
    STALE = "stale"
    DUPLICATE = "duplicate"


def _should_apply(answer, answer_version: int | None, idempotency_key: str | None) -> str:
    """Decide whether an incoming write wins.

    Order matters. Duplicate is checked FIRST: a retry of a request that already
    landed carries the same version as the stored row, so a version-only check
    would classify it as a normal same-version write and apply it again. Both
    outcomes are harmless for a simple option id, but not for a code submission
    the student has since edited.
    """
    if answer is None:
        return AnswerWriteOutcome.APPLIED

    # Only the most recently ACCEPTED key is stored, so this recognises a replay
    # of the latest request but not of an older one. That is sufficient rather
    # than sloppy: the case it catches (a request that landed and whose reply was
    # lost, retried immediately) is the one the client's backoff actually
    # produces, and a replay of anything older necessarily carries an older
    # version and is refused by the check below. Keeping a full set of seen keys
    # per answer would buy a more precise error label and nothing else.
    if idempotency_key and answer.idempotency_key == idempotency_key:
        return AnswerWriteOutcome.DUPLICATE

    # No version supplied at all: an older client that predates this field.
    # Falls back to the previous last-write-wins behaviour rather than rejecting
    # the save -- a candidate mid-exam on a cached bundle must not start losing
    # answers because the server was upgraded underneath them.
    if answer_version is None:
        return AnswerWriteOutcome.APPLIED

    if answer_version < (answer.answer_version or 0):
        return AnswerWriteOutcome.STALE

    return AnswerWriteOutcome.APPLIED


def _stamp(answer, answer_version: int | None, idempotency_key: str | None) -> None:
    if answer_version is not None:
        answer.answer_version = answer_version
    answer.idempotency_key = idempotency_key
    answer.saved_at = datetime.now(timezone.utc)


def _get_answer(db: Session, attempt_id: int, question_id: int):
    return (
        db.query(StudentAnswer)
        .filter(StudentAnswer.attempt_id == attempt_id, StudentAnswer.question_id == question_id)
        .first()
    )


def upsert_answer(db: Session, attempt_id: int, question_id: int, selected_option_id: int | None,
                  *, answer_version: int | None = None, idempotency_key: str | None = None):
    """Returns (answer, outcome). See _should_apply for the ordering rules."""
    answer = _get_answer(db, attempt_id, question_id)
    outcome = _should_apply(answer, answer_version, idempotency_key)
    if outcome != AnswerWriteOutcome.APPLIED:
        return answer, outcome

    if answer:
        answer.selected_option_id = selected_option_id
    else:
        answer = StudentAnswer(attempt_id=attempt_id, question_id=question_id, selected_option_id=selected_option_id)
        db.add(answer)
    _stamp(answer, answer_version, idempotency_key)
    db.commit()
    db.refresh(answer)
    return answer, outcome


def upsert_multi_answer(db: Session, attempt_id: int, question_id: int, selected_option_ids_json: str | None,
                        *, answer_version: int | None = None, idempotency_key: str | None = None):
    """Like upsert_answer, but for MULTI_SELECT questions: persists a JSON
    list of option ids rather than a single FK. selected_option_ids_json is
    None to represent "no options selected" (cleared/unanswered), never an
    empty-string sentinel."""
    answer = _get_answer(db, attempt_id, question_id)
    outcome = _should_apply(answer, answer_version, idempotency_key)
    if outcome != AnswerWriteOutcome.APPLIED:
        return answer, outcome

    if answer:
        answer.selected_option_ids_json = selected_option_ids_json
    else:
        answer = StudentAnswer(attempt_id=attempt_id, question_id=question_id, selected_option_ids_json=selected_option_ids_json)
        db.add(answer)
    _stamp(answer, answer_version, idempotency_key)
    db.commit()
    db.refresh(answer)
    return answer, outcome


def get_answers_for_attempt(db: Session, attempt_id: int) -> list[StudentAnswer]:
    return db.query(StudentAnswer).filter(StudentAnswer.attempt_id == attempt_id).all()


def upsert_code_answer(db: Session, attempt_id: int, question_id: int, source_code: str,
                       *, answer_version: int | None = None, idempotency_key: str | None = None):
    answer = _get_answer(db, attempt_id, question_id)
    outcome = _should_apply(answer, answer_version, idempotency_key)
    if outcome != AnswerWriteOutcome.APPLIED:
        return answer, outcome

    if answer:
        answer.code_submission = source_code
    else:
        answer = StudentAnswer(attempt_id=attempt_id, question_id=question_id, code_submission=source_code)
        db.add(answer)
    _stamp(answer, answer_version, idempotency_key)
    db.commit()
    db.refresh(answer)
    return answer, outcome


def set_code_test_results(db: Session, answer_id: int, results_json: str) -> None:
    answer = db.query(StudentAnswer).filter(StudentAnswer.id == answer_id).first()
    if answer:
        answer.code_test_results_json = results_json
        db.commit()


def mark_attempt_submitted(db: Session, attempt, status: AttemptStatus):
    from datetime import datetime, timezone
    attempt.status = status
    attempt.submitted_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(attempt)
    return attempt


def create_result(db: Session, attempt_id: int, stats: dict) -> ExamResult:
    result = ExamResult(attempt_id=attempt_id, **stats)
    db.add(result)
    db.commit()
    db.refresh(result)
    return result


def get_result(db: Session, attempt_id: int) -> ExamResult | None:
    return db.query(ExamResult).filter(ExamResult.attempt_id == attempt_id).first()


def results_for_attempts(db: Session, attempt_ids: list[int]) -> dict[int, ExamResult]:
    """Every result for these attempts, keyed by attempt id, in ONE query.

    The admin dashboards build rows per exam or per student and previously
    called `get_result` once per attempt inside those loops -- a textbook N+1.
    An exam hall of 500 candidates meant 500 round-trips to render one page, and
    it degraded linearly with the thing an admin dashboard exists to show more
    of. `admin_repository.violation_counts_for_attempts` already establishes the
    batching idiom for exactly this shape of problem, a few lines away from the
    worst offender; this is the same fix applied to results.

    Returns a dict rather than a list so callers can keep their existing
    per-attempt lookups and simply stop hitting the database inside the loop.
    """
    if not attempt_ids:
        return {}
    rows = db.query(ExamResult).filter(ExamResult.attempt_id.in_(attempt_ids)).all()
    return {row.attempt_id: row for row in rows}


def list_attempts_for_exam(db: Session, exam_id: int) -> list[StudentExamAttempt]:
    return db.query(StudentExamAttempt).filter(StudentExamAttempt.exam_id == exam_id).all()


def list_attempts_for_student(db: Session, student_id: int) -> list[StudentExamAttempt]:
    return db.query(StudentExamAttempt).filter(StudentExamAttempt.student_id == student_id).all()


def delete_attempt(db: Session, attempt: StudentExamAttempt) -> None:
    """Cascades to the attempt's answers and result (see the cascade="all,
    delete-orphan" relationships on StudentExamAttempt) -- used only by
    attempt_service.reset_student_attempt, after an AttemptReset audit row
    has already been written recording what this attempt looked like."""
    db.delete(attempt)
    db.commit()


def create_attempt_reset(db: Session, data: dict) -> AttemptReset:
    reset = AttemptReset(**data)
    db.add(reset)
    db.commit()
    db.refresh(reset)
    return reset


def list_attempt_resets_for_exam(db: Session, exam_id: int) -> list[AttemptReset]:
    return (
        db.query(AttemptReset)
        .options(joinedload(AttemptReset.student), joinedload(AttemptReset.examiner))
        .filter(AttemptReset.exam_id == exam_id)
        .order_by(AttemptReset.created_at.desc())
        .all()
    )
