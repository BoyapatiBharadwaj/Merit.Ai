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
    """This student's LIVE attempt at this exam, ignoring archived ones.

    An attempt archived by a reset must not be found here, or the retake the
    reset was granted for could never be started -- start_attempt would keep
    finding the old row and report "you have already attempted this exam".
    """
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


# Dialects whose SELECT ... FOR UPDATE actually blocks a second writer. SQLite
# is absent on purpose rather than by oversight: it has no row locks at all, and
# does not need them here -- it serialises the entire write transaction, so two
# concurrent saves can never interleave between the read and the commit in the
# first place. SQLAlchemy's SQLite compiler silently drops a FOR UPDATE clause,
# so asking for one would appear to work while doing nothing; naming the
# dialects that support it keeps that difference visible instead of implied.
_ROW_LOCKING_DIALECTS = frozenset({"postgresql", "mysql", "mariadb", "oracle", "mssql"})


def _supports_row_locks(db: Session) -> bool:
    try:
        return db.get_bind().dialect.name in _ROW_LOCKING_DIALECTS
    except Exception:  # pragma: no cover - a session with no bind cannot lock anyway
        return False


def _get_answer(db: Session, attempt_id: int, question_id: int, *, lock: bool = False):
    """The stored answer for one question, optionally locked for update.

    `lock=True` is what makes the version check mean anything. Without it the
    sequence is: read version, compare in Python, write, commit -- and two
    autosaves arriving together (a debounced save racing its own retry, or two
    tabs, or simply a fast typist on a slow link) can BOTH read version 4, both
    decide they are newer, and both write. The later-arriving one wins by
    accident of scheduling rather than by being newer, which is precisely the
    outcome answer_version exists to prevent.

    With the lock, the second reader blocks until the first commits and then
    re-reads the row it just wrote, so it sees version 5 and is correctly
    classified as stale. This relies on READ COMMITTED, which is PostgreSQL's
    default and the isolation level this application runs at.
    """
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
    """Read-check-write for one answer, under a row lock, as one transaction.

    Shared by all three answer types because the race is identical for each and
    only the column being assigned differs -- `apply_change` is the one line
    that varies. Three near-identical copies of this is how the lock would come
    to be added to two of them and forgotten on the third.
    """
    answer = _get_answer(db, attempt_id, question_id, lock=True)
    outcome = _should_apply(answer, answer_version, idempotency_key)
    if outcome != AnswerWriteOutcome.APPLIED:
        # Nothing to write, but the row is locked and must not stay that way
        # while the response is serialised -- during a whole-hall submit that
        # would queue every other save behind a write we already decided to
        # discard. Committing an empty transaction is the cheapest release.
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
        # FOR UPDATE locks rows that exist; it cannot lock one that doesn't. Two
        # saves for a question answered for the very first time can therefore
        # both find nothing and both INSERT, and uq_attempt_question rejects the
        # loser. That is the constraint doing its job -- the row it wanted now
        # exists, so retrying once takes the normal locked path and the version
        # check decides the winner properly. Bounded to a single retry: a second
        # failure is not this race and should surface rather than spin.
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
    """Like _write_answer but flushes instead of committing.

    For attempt_service.finalize_attempt, which writes the candidate's final
    answers and marks the attempt submitted in one transaction: committing here
    would release the attempt lock half way through and reintroduce the race the
    lock exists to close.

    Still takes the per-answer row lock. That is not redundant with the attempt
    lock -- an autosave already in flight when Submit is pressed does not hold
    the attempt lock, so this row lock is the only thing that makes it wait,
    re-read the version finalize just wrote, and correctly conclude it is stale
    rather than overwriting the submitted answer.
    """
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
    """Like upsert_answer, but for MULTI_SELECT questions: persists a JSON
    list of option ids rather than a single FK. selected_option_ids_json is
    None to represent "no options selected" (cleared/unanswered), never an
    empty-string sentinel."""
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
    """The attempt, locked, so a finalize cannot interleave with another.

    Two finalize requests for one attempt is not a hypothetical: the timer's
    auto-submit firing at the same moment the candidate clicks Submit produces
    exactly that, and so does a double-click on a slow connection. Without the
    lock both read status=in_progress, both apply their own final answers, and
    the answers that end up graded are whichever set happened to commit second.
    With it the second request waits, then sees the attempt already submitted
    and returns the existing result instead of re-freezing different answers.
    """
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


def list_attempts_for_exam(db: Session, exam_id: int, *, include_archived: bool = False) -> list[StudentExamAttempt]:
    """Archived attempts are excluded by default.

    Including them would make a candidate who was granted a retake appear twice
    in the examiner's list and count twice in the analytics -- once with the
    abandoned score. `include_archived=True` is for the reset-history view,
    which exists precisely to show them.
    """
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
    """Only the attempts currently being sat.

    The live-monitoring page used to download every attempt for the exam every
    ten seconds and filter in the browser -- so the cost of watching one live
    candidate grew with every candidate who had ever sat the exam. Filtering
    here means the query returns what the page is actually for.
    """
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
    """Destroy an attempt and everything under it.

    NOT the reset path any more -- see archive_attempt. Resets used to call this,
    which took the answers, result, comments, proctoring events and violation
    screenshots with it by cascade and left only an audit row referring to an id
    that no longer existed.

    Kept for the cases that genuinely mean "this data should not exist":
    deleting a candidate's account, and erasing an exam.
    """
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
    """One page of an exam's attempts, plus the total.

    The whole list used to be returned and sliced in the browser, so viewing 25
    rows cost the transfer and parse of every attempt the exam had ever had.

    `search` matches the candidate's name or email. Escaped, because `%` and `_`
    are LIKE wildcards: a candidate searching for "100%" would otherwise match
    everything, and a deliberately wildcard-heavy string turns one keystroke into
    a full scan.
    """
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
    """Neutralise LIKE wildcards in user input.

    `%` and `_` are wildcards, so an unescaped search box lets any input become
    a pattern -- "100%" matches every row, and "%_%_%_%" is a cheap way to make
    the database work hard. The backslash is escaped first, or escaping the
    others would corrupt it.
    """
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
