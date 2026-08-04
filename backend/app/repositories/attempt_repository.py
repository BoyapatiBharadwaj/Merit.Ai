"""
Data access for StudentExamAttempt, StudentAnswer, ExamResult tables.
"""
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


def upsert_answer(db: Session, attempt_id: int, question_id: int, selected_option_id: int | None) -> StudentAnswer:
    answer = (
        db.query(StudentAnswer)
        .filter(StudentAnswer.attempt_id == attempt_id, StudentAnswer.question_id == question_id)
        .first()
    )
    if answer:
        answer.selected_option_id = selected_option_id
    else:
        answer = StudentAnswer(attempt_id=attempt_id, question_id=question_id, selected_option_id=selected_option_id)
        db.add(answer)
    db.commit()
    db.refresh(answer)
    return answer


def upsert_multi_answer(db: Session, attempt_id: int, question_id: int, selected_option_ids_json: str | None) -> StudentAnswer:
    """Like upsert_answer, but for MULTI_SELECT questions: persists a JSON
    list of option ids rather than a single FK. selected_option_ids_json is
    None to represent "no options selected" (cleared/unanswered), never an
    empty-string sentinel."""
    answer = (
        db.query(StudentAnswer)
        .filter(StudentAnswer.attempt_id == attempt_id, StudentAnswer.question_id == question_id)
        .first()
    )
    if answer:
        answer.selected_option_ids_json = selected_option_ids_json
    else:
        answer = StudentAnswer(attempt_id=attempt_id, question_id=question_id, selected_option_ids_json=selected_option_ids_json)
        db.add(answer)
    db.commit()
    db.refresh(answer)
    return answer


def get_answers_for_attempt(db: Session, attempt_id: int) -> list[StudentAnswer]:
    return db.query(StudentAnswer).filter(StudentAnswer.attempt_id == attempt_id).all()


def upsert_code_answer(db: Session, attempt_id: int, question_id: int, source_code: str) -> StudentAnswer:
    answer = (
        db.query(StudentAnswer)
        .filter(StudentAnswer.attempt_id == attempt_id, StudentAnswer.question_id == question_id)
        .first()
    )
    if answer:
        answer.code_submission = source_code
    else:
        answer = StudentAnswer(attempt_id=attempt_id, question_id=question_id, code_submission=source_code)
        db.add(answer)
    db.commit()
    db.refresh(answer)
    return answer


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
