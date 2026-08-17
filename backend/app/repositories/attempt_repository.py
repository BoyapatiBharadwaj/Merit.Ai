"""
Data access for StudentExamAttempt, StudentAnswer, ExamResult tables.
"""
import logging
from datetime import datetime, timezone

from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload
from app.models.attempt import AttemptReset, StudentExamAttempt, StudentAnswer, ExamResult
from app.models.enums import AttemptStatus

logger = logging.getLogger("app")


def get_attempt(db: Session, attempt_id: int) -> StudentExamAttempt | None:
    return db.query(StudentExamAttempt).filter(StudentExamAttempt.id == attempt_id).first()


def get_existing_attempt(db: Session, student_id: int, exam_id: int) -> StudentExamAttempt | None:
    """This student's LIVE attempt at this exam, ignoring archived ones."""
    return (
        db.query(StudentExamAttempt)
        .filter(StudentExamAttempt.student_id == student_id,
                StudentExamAttempt.exam_id == exam_id,
                StudentExamAttempt.archived_at.is_(None))
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



# --- autosave concurrency control ---
# Shared by all three answer types (MCQ, multi-select, code),
# because the race and its fix are identical for each.

class AnswerWriteOutcome:
    """Why a write was or wasn't applied. Returned alongside the row so the API
    can tell the client the truth instead of an unconditional `saved: true`."""

    APPLIED = "applied"
    STALE = "stale"
    DUPLICATE = "duplicate"


def _should_apply(answer, answer_version: int | None, idempotency_key: str | None) -> str:
    """Decide whether an incoming write wins."""
    if answer is None:
        return AnswerWriteOutcome.APPLIED

    # Only the most recently ACCEPTED key is stored, so this recognises a replay of the latest
    # request but not of an older one.
    if idempotency_key and answer.idempotency_key == idempotency_key:
        return AnswerWriteOutcome.DUPLICATE

    # No version supplied at all: an older client that predates this field.
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


# Dialects whose SELECT ... FOR UPDATE actually blocks a second writer.
_ROW_LOCKING_DIALECTS = frozenset({"postgresql", "mysql", "mariadb", "oracle", "mssql"})


def _supports_row_locks(db: Session) -> bool:
    try:
        return db.get_bind().dialect.name in _ROW_LOCKING_DIALECTS
    except Exception:  # pragma: no cover - a session with no bind cannot lock anyway
        return False


def _get_answer(db: Session, attempt_id: int, question_id: int, *, lock: bool = False):
    """The stored answer for one question, optionally locked for update."""
    query = (
        db.query(StudentAnswer)
        .filter(StudentAnswer.attempt_id == attempt_id, StudentAnswer.question_id == question_id)
    )
    if lock and _supports_row_locks(db):
        query = query.with_for_update()
    return query.first()


def _write_answer(db: Session, attempt_id: int, question_id: int, apply_change,
                  *, answer_version: int | None, idempotency_key: str | None,
                  _retrying: bool = False):
    """Read-check-write for one answer, under a row lock, as one transaction."""
    answer = _get_answer(db, attempt_id, question_id, lock=True)
    outcome = _should_apply(answer, answer_version, idempotency_key)
    if outcome != AnswerWriteOutcome.APPLIED:
        # Nothing to write, but the row is locked and must not
        # stay that way while the response is serialised.
        db.commit()
        return answer, outcome

    if answer is None:
        answer = StudentAnswer(attempt_id=attempt_id, question_id=question_id)
        db.add(answer)
    apply_change(answer)
    _stamp(answer, answer_version, idempotency_key)

    try:
        db.commit()
    except IntegrityError:
        # FOR UPDATE locks rows that exist; it cannot lock one that doesn't.
        db.rollback()
        if _retrying:
            raise
        logger.debug("Answer insert raced for attempt=%s question=%s; retrying under lock.",
                     attempt_id, question_id)
        return _write_answer(db, attempt_id, question_id, apply_change,
                             answer_version=answer_version, idempotency_key=idempotency_key,
                             _retrying=True)

    db.refresh(answer)
    return answer, outcome


def stage_answer(db: Session, attempt_id: int, question_id: int, apply_change,
                 *, answer_version: int | None, idempotency_key: str | None):
    """Like _write_answer but flushes instead of committing."""
    answer = _get_answer(db, attempt_id, question_id, lock=True)
    outcome = _should_apply(answer, answer_version, idempotency_key)
    if outcome != AnswerWriteOutcome.APPLIED:
        return answer, outcome

    if answer is None:
        answer = StudentAnswer(attempt_id=attempt_id, question_id=question_id)
        db.add(answer)
    apply_change(answer)
    _stamp(answer, answer_version, idempotency_key)
    db.flush()
    return answer, outcome


def upsert_answer(db: Session, attempt_id: int, question_id: int, selected_option_id: int | None,
                  *, answer_version: int | None = None, idempotency_key: str | None = None):
    """Returns (answer, outcome). See _should_apply for the ordering rules."""
    def _apply(answer):
        answer.selected_option_id = selected_option_id

    return _write_answer(db, attempt_id, question_id, _apply,
                         answer_version=answer_version, idempotency_key=idempotency_key)


def upsert_multi_answer(db: Session, attempt_id: int, question_id: int, selected_option_ids_json: str | None,
                        *, answer_version: int | None = None, idempotency_key: str | None = None):
    """Like upsert_answer, but for MULTI_SELECT questions."""
    def _apply(answer):
        answer.selected_option_ids_json = selected_option_ids_json

    return _write_answer(db, attempt_id, question_id, _apply,
                         answer_version=answer_version, idempotency_key=idempotency_key)


def get_answers_for_attempt(db: Session, attempt_id: int) -> list[StudentAnswer]:
    return db.query(StudentAnswer).filter(StudentAnswer.attempt_id == attempt_id).all()


def upsert_code_answer(db: Session, attempt_id: int, question_id: int, source_code: str,
                       *, answer_version: int | None = None, idempotency_key: str | None = None):
    def _apply(answer):
        answer.code_submission = source_code

    return _write_answer(db, attempt_id, question_id, _apply,
                         answer_version=answer_version, idempotency_key=idempotency_key)


def set_code_test_results(db: Session, answer_id: int, results_json: str) -> None:
    answer = db.query(StudentAnswer).filter(StudentAnswer.id == answer_id).first()
    if answer:
        answer.code_test_results_json = results_json
        db.commit()


def get_attempt_for_update(db: Session, attempt_id: int) -> StudentExamAttempt | None:
    """The attempt, locked, so a finalize cannot interleave with another."""
    query = db.query(StudentExamAttempt).filter(StudentExamAttempt.id == attempt_id)
    if _supports_row_locks(db):
        query = query.with_for_update()
    return query.first()


def mark_attempt_submitted(db: Session, attempt, status: AttemptStatus, *, commit: bool = True):
    """`commit=False` lets a caller fold this into a larger transaction --
    specifically attempt_service.finalize_attempt, which must freeze the final
    answers and mark the attempt submitted together or not at all."""
    attempt.status = status
    attempt.submitted_at = datetime.now(timezone.utc)
    if commit:
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
    """Every result for these attempts, keyed by attempt id, in ONE query."""
    if not attempt_ids:
        return {}
    rows = db.query(ExamResult).filter(ExamResult.attempt_id.in_(attempt_ids)).all()
    return {row.attempt_id: row for row in rows}


def list_attempts_for_exam(db: Session, exam_id: int, *, include_archived: bool = False) -> list[StudentExamAttempt]:
    """Archived attempts are excluded by default."""
    query = db.query(StudentExamAttempt).filter(StudentExamAttempt.exam_id == exam_id)
    if not include_archived:
        query = query.filter(StudentExamAttempt.archived_at.is_(None))
    return query.all()


def list_attempts_for_student(db: Session, student_id: int, *, include_archived: bool = False) -> list[StudentExamAttempt]:
    query = db.query(StudentExamAttempt).filter(StudentExamAttempt.student_id == student_id)
    if not include_archived:
        query = query.filter(StudentExamAttempt.archived_at.is_(None))
    return query.all()


def list_archived_attempts_for_exam(db: Session, exam_id: int) -> list[StudentExamAttempt]:
    """The superseded attempts, newest first -- what the reset history shows."""
    return (
        db.query(StudentExamAttempt)
        .options(joinedload(StudentExamAttempt.student))
        .filter(StudentExamAttempt.exam_id == exam_id,
                StudentExamAttempt.archived_at.isnot(None))
        .order_by(StudentExamAttempt.archived_at.desc())
        .all()
    )


def active_attempts_for_exam(db: Session, exam_id: int) -> list[StudentExamAttempt]:
    """Only the attempts currently being sat."""
    return (
        db.query(StudentExamAttempt)
        .options(joinedload(StudentExamAttempt.student))
        .filter(StudentExamAttempt.exam_id == exam_id,
                StudentExamAttempt.archived_at.is_(None),
                StudentExamAttempt.status == AttemptStatus.IN_PROGRESS)
        .order_by(StudentExamAttempt.started_at.asc())
        .all()
    )


def archive_attempt(db: Session, attempt: StudentExamAttempt, *, reset_id: int | None = None,
                    commit: bool = True) -> StudentExamAttempt:
    """Supersede an attempt without destroying it. See attempt_service.reset_student_attempt."""
    attempt.archived_at = datetime.now(timezone.utc)
    attempt.archived_by_reset_id = reset_id
    if commit:
        db.commit()
        db.refresh(attempt)
    return attempt


def delete_attempt(db: Session, attempt: StudentExamAttempt) -> None:
    """Destroy an attempt and everything under it."""
    db.delete(attempt)
    db.commit()


def create_attempt_reset(db: Session, data: dict, *, commit: bool = True) -> AttemptReset:
    """`commit=False` so the audit row and the archiving it describes land in
    one transaction -- a reset recorded without the attempt actually being
    superseded, or the reverse, would each be worse than neither."""
    reset = AttemptReset(**data)
    db.add(reset)
    if commit:
        db.commit()
        db.refresh(reset)
    else:
        db.flush()
    return reset


def list_attempt_resets_for_exam(db: Session, exam_id: int) -> list[AttemptReset]:
    return (
        db.query(AttemptReset)
        .options(joinedload(AttemptReset.student), joinedload(AttemptReset.examiner))
        .filter(AttemptReset.exam_id == exam_id)
        .order_by(AttemptReset.created_at.desc())
        .all()
    )


def paginated_attempts_for_exam(db: Session, exam_id: int, *, offset: int, limit: int,
                                search: str = "") -> tuple[list[StudentExamAttempt], int]:
    """One page of an exam's attempts, plus the total."""
    from app.models.student import Student
    from app.models.user import User

    query = (
        db.query(StudentExamAttempt)
        .join(Student, Student.id == StudentExamAttempt.student_id)
        .join(User, User.id == Student.user_id)
        .options(joinedload(StudentExamAttempt.student).joinedload(Student.user))
        .filter(StudentExamAttempt.exam_id == exam_id,
                StudentExamAttempt.archived_at.is_(None))
    )
    if search.strip():
        pattern = f"%{escape_like(search.strip())}%"
        query = query.filter(or_(User.full_name.ilike(pattern, escape="\\"),
                                 User.email.ilike(pattern, escape="\\")))

    total = query.order_by(None).count()
    rows = (query.order_by(StudentExamAttempt.started_at.desc())
            .offset(offset).limit(limit).all())
    return rows, total


def escape_like(value: str) -> str:
    """Neutralise LIKE wildcards in user input."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
