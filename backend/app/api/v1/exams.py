"""Exam, section, and question management endpoints."""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_examiner, require_student
from app.database.session import get_db
from app.models.user import User
from app.repositories import attempt_repository, exam_repository, user_repository
from app.schemas.exam import ExamCreate, ExamDetailOut, ExamDetailsUpdate, ExamOut, ExamScheduleUpdate
from app.schemas.question import (
    QuestionCreate, QuestionOut, QuestionReorderRequest, SectionCreate, SectionOut, SectionUpdate,
)
from app.services import attempt_service, exam_service, organization_service

router = APIRouter(prefix="/exams", tags=["Exams"])


@router.post("", response_model=ExamOut, status_code=201)
def create_exam(payload: ExamCreate, db: Session = Depends(get_db), user: User = Depends(require_examiner)):
    examiner = user_repository.get_examiner_by_user_id(db, user.id)
    return exam_service.create_exam(db, examiner.id, payload.model_dump())


@router.get("/my", response_model=list[ExamOut])
def my_exams(db: Session = Depends(get_db), user: User = Depends(require_examiner)):
    examiner = user_repository.get_examiner_by_user_id(db, user.id)
    return exam_repository.list_exams_for_examiner(db, examiner.id)


@router.get("/all", response_model=list[ExamOut])
def all_exams(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    if user.role.name != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You do not have access to this resource.")
    return exam_repository.list_all_exams(db)


@router.get("/available", response_model=list[dict])
def available_exams(db: Session = Depends(get_db), user: User = Depends(require_student)):
    """Every published exam this student's organization/allow-list grants
    them, each annotated with their own attempt (if any) and a computed
    `candidate_status` (upcoming/ongoing/completed/missed) -- see
    exam_service.compute_candidate_status. Deliberately not time-windowed:
    the real enforcement is in attempt_service.start_attempt, so this list is
    free to also show exams that haven't opened yet (Upcoming) or have
    already closed (Missed) instead of making them vanish.
    """
    student = user_repository.get_student_by_user_id(db, user.id)
    exams = organization_service.list_accessible_published_exams(db, student)
    # finalize_if_expired here (not just in the attempts endpoints) because
    # this is now the only list the dashboard reads -- without this, an
    # attempt whose deadline passed while the student was away would keep
    # showing "Ongoing" with a stuck countdown until they happened to open
    # the exam page again.
    attempts_by_exam = {
        a.exam_id: attempt_service.finalize_if_expired(db, a)
        for a in attempt_repository.list_attempts_for_student(db, student.id)
    }
    now = datetime.now(timezone.utc)
    results_by_attempt = {}
    for attempt in attempts_by_exam.values():
        result = attempt_repository.get_result(db, attempt.id)
        if result:
            results_by_attempt[attempt.id] = result
    return [
        exam_service.serialize_exam_for_candidate(
            exam, attempts_by_exam.get(exam.id),
            results_by_attempt.get(attempts_by_exam[exam.id].id) if exam.id in attempts_by_exam else None,
            now,
        )
        for exam in exams
    ]


@router.get("/{exam_id}", response_model=ExamDetailOut)
def get_exam(exam_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    exam = exam_repository.get_exam(db, exam_id)
    if not exam:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Exam not found.")
    if user.role.name == "admin":
        return exam
    examiner = user_repository.get_examiner_by_user_id(db, user.id)
    if not examiner or exam.examiner_id != examiner.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You do not have access to this exam.")
    return exam


@router.put("/{exam_id}", response_model=ExamOut)
def update_exam(exam_id: int, payload: ExamDetailsUpdate, db: Session = Depends(get_db), user: User = Depends(require_examiner)):
    """Edit of every exam-level setting except the schedule -- draft only
    (see exam_service.update_exam_details). Once published, these are all
    frozen; only the schedule can still move, via PATCH /{exam_id}/schedule."""
    examiner = user_repository.get_examiner_by_user_id(db, user.id)
    return exam_service.update_exam_details(db, examiner.id, exam_id, payload.model_dump())


@router.patch("/{exam_id}/schedule", response_model=ExamOut)
def update_exam_schedule(exam_id: int, payload: ExamScheduleUpdate, db: Session = Depends(get_db), user: User = Depends(require_examiner)):
    """Schedule-only edit, allowed before AND after publishing (unlike every
    other exam edit) -- see exam_service.update_exam_schedule for the rules
    on what's still changeable once the exam has started."""
    examiner = user_repository.get_examiner_by_user_id(db, user.id)
    return exam_service.update_exam_schedule(db, examiner.id, exam_id, payload.start_time, payload.end_time)


@router.delete("/{exam_id}", status_code=204)
def delete_exam(exam_id: int, db: Session = Depends(get_db), user: User = Depends(require_examiner)):
    """Owner + draft-only, same gate as every other edit action (see
    exam_service._get_editable_exam) -- a published exam has (or may soon
    have) real student attempts riding on it and is never deletable, only
    closable."""
    examiner = user_repository.get_examiner_by_user_id(db, user.id)
    exam_service.delete_exam(db, examiner.id, exam_id)


@router.post("/{exam_id}/sections", response_model=SectionOut, status_code=201)
def add_section(exam_id: int, payload: SectionCreate, db: Session = Depends(get_db), user: User = Depends(require_examiner)):
    examiner = user_repository.get_examiner_by_user_id(db, user.id)
    return exam_service.add_section(db, examiner.id, exam_id, payload.model_dump())


@router.patch("/sections/{section_id}", response_model=SectionOut)
def update_section(section_id: int, payload: SectionUpdate, db: Session = Depends(get_db), user: User = Depends(require_examiner)):
    """Rename a section while the exam is still a draft -- see
    exam_service.update_section for why this freezes at publish."""
    examiner = user_repository.get_examiner_by_user_id(db, user.id)
    return exam_service.update_section(db, examiner.id, section_id, payload.title)


@router.post("/sections/{section_id}/questions", response_model=QuestionOut, status_code=201)
def add_question(section_id: int, payload: QuestionCreate, db: Session = Depends(get_db), user: User = Depends(require_examiner)):
    examiner = user_repository.get_examiner_by_user_id(db, user.id)
    return exam_service.add_question(
        db, examiner.id, section_id, payload.text, payload.marks, payload.order_index,
        question_type=payload.question_type,
        options=[option.model_dump() for option in payload.options],
        language=payload.language, starter_code=payload.starter_code,
        test_cases=[case.model_dump() for case in payload.test_cases],
        time_limit_seconds=payload.time_limit_seconds,
        explanation=payload.explanation,
    )


@router.put("/questions/{question_id}", response_model=QuestionOut)
def update_question(question_id: int, payload: QuestionCreate, db: Session = Depends(get_db), user: User = Depends(require_examiner)):
    examiner = user_repository.get_examiner_by_user_id(db, user.id)
    return exam_service.update_question(
        db, examiner.id, question_id, payload.text, payload.marks, payload.order_index,
        question_type=payload.question_type,
        options=[option.model_dump() for option in payload.options],
        language=payload.language, starter_code=payload.starter_code,
        test_cases=[case.model_dump() for case in payload.test_cases],
        time_limit_seconds=payload.time_limit_seconds,
        explanation=payload.explanation,
    )


@router.delete("/questions/{question_id}", status_code=204)
def delete_question(question_id: int, db: Session = Depends(get_db), user: User = Depends(require_examiner)):
    examiner = user_repository.get_examiner_by_user_id(db, user.id)
    exam_service.delete_question(db, examiner.id, question_id)


@router.put("/sections/{section_id}/questions/reorder")
def reorder_questions(section_id: int, payload: QuestionReorderRequest, db: Session = Depends(get_db), user: User = Depends(require_examiner)):
    examiner = user_repository.get_examiner_by_user_id(db, user.id)
    exam_service.reorder_questions(db, examiner.id, section_id, payload.question_ids)
    return {"reordered": True}


@router.post("/{exam_id}/publish", response_model=ExamOut)
def publish_exam(exam_id: int, db: Session = Depends(get_db), user: User = Depends(require_examiner)):
    examiner = user_repository.get_examiner_by_user_id(db, user.id)
    return exam_service.publish_exam(db, examiner.id, exam_id)