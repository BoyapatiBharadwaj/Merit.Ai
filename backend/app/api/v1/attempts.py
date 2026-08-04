"""Student exam-taking endpoints."""
import json

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_examiner, require_student
from app.database.session import get_db
from app.models.enums import QuestionType
from app.models.user import User
from app.repositories import attempt_repository, exam_repository, proctor_repository, user_repository
from app.schemas.attempt import (
    AttemptCommentRequest, AttemptReportOut, CodeAnswerRequest, CodeRunRequest, ExamResultOut, ResetAttemptRequest,
    SaveAnswerRequest, SaveMultiAnswerRequest, StartAttemptResponse,
)
from app.services import attempt_service, organization_service, pdf_service

router = APIRouter(prefix="/attempts", tags=["Exam Attempts"])


def _ordered_options(attempt, question) -> list[dict]:
    """Options in this attempt's fixed per-question display order (see
    attempt_service._build_option_order), falling back to their natural
    (authored) order when there's no stored shuffle (randomize_options was
    off for this exam, or the question has no options). A published exam's
    questions and options are immutable -- editing either requires DRAFT
    status (see exam_service._get_editable_exam) -- so the stored order can
    never go stale against a question an attempt is already running against."""
    natural = [{"id": option.id, "text": option.text} for option in question.options]
    order = attempt_service.get_option_order(attempt, question.id)
    if not order:
        return natural
    by_id = {opt["id"]: opt for opt in natural}
    return [by_id[i] for i in order if i in by_id]


def _can_access_attempt(db: Session, user: User, attempt) -> bool:
    """Guards the finished-attempt reads (result, report, PDF).

    The student branch re-checks organization access rather than trusting
    ownership alone, for the same reason _get_owned_in_progress_attempt does:
    a student removed from the roster should not keep pulling down an exam's
    result afterwards.
    """
    if user.role.name == "admin":
        return True
    if user.role.name == "student":
        student = user.student_profile
        if not student or attempt.student_id != student.id:
            return False
        return organization_service.can_student_access_exam(db, student, attempt.exam)
    examiner = user.examiner_profile
    return bool(examiner and attempt.exam.examiner_id == examiner.id)


@router.post("/start/{exam_id}", response_model=StartAttemptResponse)
def start_attempt(exam_id: int, db: Session = Depends(get_db), user: User = Depends(require_student)):
    student = user_repository.get_student_by_user_id(db, user.id)
    # The identity gate used to run here, before start_attempt. That leaked an
    # enumeration oracle: an unenrolled, unverified student got 403 "verify
    # your ID" for a real proctoring-enabled exam and 404 "not available" for
    # one that does not exist -- so any student could map out which exam ids
    # exist across every organization. Both checks now live inside
    # start_attempt, access first, so every rejection looks identical.
    attempt, question_ids = attempt_service.start_attempt(db, student.id, exam_id)
    return StartAttemptResponse(
        attempt_id=attempt.id,
        exam_title=attempt.exam.title,
        duration_minutes=attempt.exam.duration_minutes,
        started_at=attempt.started_at,
        remaining_seconds=attempt_service.remaining_seconds(attempt),
        proctoring_enabled=attempt.exam.proctoring_enabled,
        question_ids_in_order=question_ids,
    )


@router.get("/{attempt_id}/question/{question_id}")
def get_question_for_attempt(attempt_id: int, question_id: int, db: Session = Depends(get_db), user: User = Depends(require_student)):
    student = user_repository.get_student_by_user_id(db, user.id)
    attempt = attempt_service.ensure_attempt_is_active(db, student.id, attempt_id)
    if question_id not in {int(value) for value in attempt.question_order.split(",")}:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Question not found in this attempt.")
    question = exam_repository.get_question(db, question_id)
    if not question or question.section.exam_id != attempt.exam_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Question not found in this attempt.")
    existing_answer = next((answer for answer in attempt_repository.get_answers_for_attempt(db, attempt_id) if answer.question_id == question_id), None)

    if question.question_type == QuestionType.CODING:
        return {
            "question_id": question.id,
            "question_type": "coding",
            "text": question.text,
            "marks": question.marks,
            "language": question.language,
            "starter_code": question.starter_code,
            "time_limit_seconds": question.time_limit_seconds,
            # Hidden test cases are never sent to the client -- only samples,
            # for the student to sanity-check their code against.
            "sample_test_cases": [{"input": case.get("input", ""), "expected_output": case.get("expected_output", "")} for case in question.sample_test_cases],
            "source_code": (existing_answer.code_submission if existing_answer else None) or question.starter_code or "",
            "has_saved_submission": bool(existing_answer and existing_answer.code_submission),
        }
    if question.question_type == QuestionType.MULTI_SELECT:
        selected_ids = []
        if existing_answer and existing_answer.selected_option_ids_json:
            try:
                selected_ids = json.loads(existing_answer.selected_option_ids_json)
            except (ValueError, TypeError):
                selected_ids = []
        return {
            "question_id": question.id,
            "question_type": "multi_select",
            "text": question.text,
            "marks": question.marks,
            "options": _ordered_options(attempt, question),
            "selected_option_ids": selected_ids,
        }
    return {
        "question_id": question.id,
        "question_type": "mcq",
        "text": question.text,
        "marks": question.marks,
        "options": _ordered_options(attempt, question),
        "selected_option_id": existing_answer.selected_option_id if existing_answer else None,
    }


@router.get("/{attempt_id}/answers")
def get_answers(attempt_id: int, db: Session = Depends(get_db), user: User = Depends(require_student)):
    """Full saved-answers map for the in-progress attempt owned by the caller,
    used to repaint question-navigator state after a reload/reconnect (see
    'exam recovery' in exam.js's beginExam)."""
    student = user_repository.get_student_by_user_id(db, user.id)
    return attempt_service.get_answers_map(db, student.id, attempt_id)


@router.put("/{attempt_id}/answer")
def save_answer(attempt_id: int, payload: SaveAnswerRequest, db: Session = Depends(get_db), user: User = Depends(require_student)):
    """Autosave one MCQ answer.

    Returns 200 even when the write was NOT applied, and says so in `applied`.
    A stale or duplicate save is a normal consequence of the client's retry
    backoff on a bad connection, not a client error -- surfacing it as a 4xx
    would light up the candidate's screen with failures during exactly the
    network conditions where they most need reassurance. The stored state comes
    back either way so the client can reconcile.
    """
    student = user_repository.get_student_by_user_id(db, user.id)
    answer, outcome = attempt_service.save_answer(
        db, student.id, attempt_id, payload.question_id, payload.selected_option_id,
        answer_version=payload.answer_version, idempotency_key=payload.idempotency_key,
    )
    return {
        "saved": True,
        "applied": outcome == attempt_repository.AnswerWriteOutcome.APPLIED,
        "outcome": outcome,
        "question_id": answer.question_id,
        "selected_option_id": answer.selected_option_id,
        "answer_version": answer.answer_version,
    }


@router.put("/{attempt_id}/multi-answer")
def save_multi_answer(attempt_id: int, payload: SaveMultiAnswerRequest, db: Session = Depends(get_db), user: User = Depends(require_student)):
    student = user_repository.get_student_by_user_id(db, user.id)
    answer, outcome = attempt_service.save_multi_select_answer(
        db, student.id, attempt_id, payload.question_id, payload.selected_option_ids,
        answer_version=payload.answer_version, idempotency_key=payload.idempotency_key,
    )
    selected_ids = json.loads(answer.selected_option_ids_json) if answer.selected_option_ids_json else []
    return {
        "saved": True,
        "applied": outcome == attempt_repository.AnswerWriteOutcome.APPLIED,
        "outcome": outcome,
        "question_id": answer.question_id,
        "selected_option_ids": selected_ids,
        "answer_version": answer.answer_version,
    }


@router.put("/{attempt_id}/code-answer")
def save_code_answer(attempt_id: int, payload: CodeAnswerRequest, db: Session = Depends(get_db), user: User = Depends(require_student)):
    """Autosave only -- persists the student's current code without running
    it. Grading happens once at final submit (see attempt_service)."""
    student = user_repository.get_student_by_user_id(db, user.id)
    answer, outcome = attempt_service.save_code_answer(
        db, student.id, attempt_id, payload.question_id, payload.source_code,
        answer_version=payload.answer_version, idempotency_key=payload.idempotency_key,
    )
    return {
        "saved": True,
        "applied": outcome == attempt_repository.AnswerWriteOutcome.APPLIED,
        "outcome": outcome,
        "question_id": answer.question_id,
        "answer_version": answer.answer_version,
    }


@router.post("/{attempt_id}/code-answer/run")
def run_code_sample(attempt_id: int, payload: CodeRunRequest, db: Session = Depends(get_db), user: User = Depends(require_student)):
    """Ungraded 'Run' button: executes against sample test cases only, for
    immediate feedback. Never touches the hidden test cases used for grading."""
    student = user_repository.get_student_by_user_id(db, user.id)
    return attempt_service.run_sample_test_cases(db, student.id, attempt_id, payload.question_id, payload.source_code)


@router.post("/{attempt_id}/submit", response_model=ExamResultOut)
def submit_attempt(attempt_id: int, auto: bool = False, db: Session = Depends(get_db), user: User = Depends(require_student)):
    student = user_repository.get_student_by_user_id(db, user.id)
    result = attempt_service.submit_attempt(db, student.id, attempt_id, auto=auto)
    return ExamResultOut(attempt_id=attempt_id, **{key: getattr(result, key) for key in ["total_marks", "scored_marks", "percentage", "correct_count", "incorrect_count", "unattempted_count"]})


@router.get("/{attempt_id}/result", response_model=ExamResultOut)
def get_result(attempt_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    attempt = attempt_repository.get_attempt(db, attempt_id)
    if not attempt or not _can_access_attempt(db, user, attempt):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Result not found.")
    attempt = attempt_service.finalize_if_expired(db, attempt)
    result = attempt_repository.get_result(db, attempt_id)
    if not result:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Result not available yet.")
    return ExamResultOut(attempt_id=attempt_id, **{key: getattr(result, key) for key in ["total_marks", "scored_marks", "percentage", "correct_count", "incorrect_count", "unattempted_count"]})


@router.get("/{attempt_id}/report", response_model=AttemptReportOut)
def get_report(attempt_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """The comprehensive report: exam + candidate details, timing, pass/fail,
    and a full per-question breakdown -- see attempt_service.build_full_report.
    Same access rule as /result (owning student, the exam's examiner, or an
    admin), and opportunistically finalizes an expired-but-still-in_progress
    attempt first so the report is never stale just because nobody had
    looked at this attempt since its deadline passed."""
    attempt = attempt_repository.get_attempt(db, attempt_id)
    if not attempt or not _can_access_attempt(db, user, attempt):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Report not found.")
    attempt = attempt_service.finalize_if_expired(db, attempt)
    if not attempt_repository.get_result(db, attempt_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Report not available yet.")
    return attempt_service.build_full_report(db, attempt)


def _is_staff_for_attempt(user: User, attempt) -> bool:
    if user.role.name == "admin":
        return True
    if user.role.name == "examiner":
        return bool(user.examiner_profile and user.examiner_profile.id == attempt.exam.examiner_id)
    return False


@router.get("/{attempt_id}/staff-report")
def get_staff_report(attempt_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """The admin drill-down's candidate exam report: everything /report has,
    plus identity verification, risk score, a templated proctoring summary,
    the full violation timeline, and both comment fields. Staff-only (an
    admin, or the exam's owning examiner) -- never the student themselves,
    since an examiner's or admin's private notes about them live in here.

    Unlike /report, this doesn't 404 for an attempt with no result yet: a
    staff member reviewing a still-in-progress or abandoned attempt is a
    normal, useful case (see attempt_service.build_staff_report), not an
    error state.
    """
    attempt = attempt_repository.get_attempt(db, attempt_id)
    if not attempt or not _is_staff_for_attempt(user, attempt):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Report not found.")
    attempt = attempt_service.finalize_if_expired(db, attempt)
    return attempt_service.build_staff_report(db, attempt)


@router.patch("/{attempt_id}/comment")
def set_attempt_comment(attempt_id: int, payload: AttemptCommentRequest,
                        db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Sets exactly one of examiner_comment/admin_comment, chosen by the
    caller's own role -- see attempt_service.set_attempt_comment for the
    ownership check that keeps an examiner from annotating an exam that
    isn't theirs."""
    return attempt_service.set_attempt_comment(db, user, attempt_id, payload.comment)


@router.get("/my", response_model=list[dict])
def my_attempts(db: Session = Depends(get_db), user: User = Depends(require_student)):
    student = user_repository.get_student_by_user_id(db, user.id)
    attempts = [attempt_service.finalize_if_expired(db, a) for a in attempt_repository.list_attempts_for_student(db, student.id)]
    return [{"attempt_id": attempt.id, "exam_id": attempt.exam_id, "exam_title": attempt.exam.title, "status": attempt.status.value, "started_at": attempt.started_at, "submitted_at": attempt.submitted_at, "scored_marks": result.scored_marks if (result := attempt_repository.get_result(db, attempt.id)) else None, "total_marks": result.total_marks if result else None, "percentage": result.percentage if result else None} for attempt in attempts]


@router.get("/exam/{exam_id}", response_model=list[dict])
def attempts_for_exam(exam_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    exam = exam_repository.get_exam(db, exam_id)
    if not exam or (user.role.name != "admin" and (not user.examiner_profile or exam.examiner_id != user.examiner_profile.id)):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Exam not found.")
    attempts = [attempt_service.finalize_if_expired(db, a) for a in attempt_repository.list_attempts_for_exam(db, exam_id)]
    return [{"attempt_id": attempt.id, "student_id": attempt.student_id, "student_name": attempt.student.user.full_name, "status": attempt.status.value, "started_at": attempt.started_at, "submitted_at": attempt.submitted_at, "scored_marks": result.scored_marks if (result := attempt_repository.get_result(db, attempt.id)) else None, "total_marks": result.total_marks if result else None} for attempt in attempts]


@router.post("/exam/{exam_id}/student/{student_id}/reset")
def reset_student_attempt(exam_id: int, student_id: int, payload: ResetAttemptRequest, db: Session = Depends(get_db), user: User = Depends(require_examiner)):
    """Lets the exam's owning examiner give a student a clean retake after a
    disruption -- see attempt_service.reset_student_attempt for the full
    rationale and what gets recorded in the audit trail."""
    examiner = user_repository.get_examiner_by_user_id(db, user.id)
    attempt_service.reset_student_attempt(db, examiner.id, exam_id, student_id, payload.reason)
    return {"reset": True}


@router.get("/exam/{exam_id}/resets", response_model=list[dict])
def list_attempt_resets(exam_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Audit trail of every reset performed on this exam's attempts -- same
    access rule as /exam/{exam_id} (the owning examiner, or an admin)."""
    exam = exam_repository.get_exam(db, exam_id)
    if not exam or (user.role.name != "admin" and (not user.examiner_profile or exam.examiner_id != user.examiner_profile.id)):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Exam not found.")
    resets = attempt_service.list_attempt_resets(db, exam_id)
    return [{
        "id": r.id,
        "student_id": r.student_id,
        "student_name": r.student.user.full_name if r.student and r.student.user else None,
        "examiner_id": r.examiner_id,
        "examiner_name": r.examiner.user.full_name if r.examiner and r.examiner.user else None,
        "previous_attempt_id": r.previous_attempt_id,
        "previous_status": r.previous_status,
        "reason": r.reason,
        "created_at": r.created_at,
    } for r in resets]


def _load_attempt_and_result(db: Session, user: User, attempt_id: int):
    attempt = attempt_repository.get_attempt(db, attempt_id)
    if not attempt or not _can_access_attempt(db, user, attempt):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Result not found.")
    attempt = attempt_service.finalize_if_expired(db, attempt)
    result = attempt_repository.get_result(db, attempt_id)
    if not result:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Result not available yet.")
    return attempt, result


@router.get("/{attempt_id}/result/pdf")
def get_result_pdf(attempt_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    attempt, result = _load_attempt_and_result(db, user, attempt_id)
    violation_count = len(proctor_repository.list_events_for_attempt(db, attempt_id))
    pdf_bytes = pdf_service.build_result_report(
        student_name=attempt.student.user.full_name,
        roll_number=attempt.student.roll_number,
        exam_title=attempt.exam.title,
        attempt_id=attempt_id,
        started_at=attempt.started_at,
        submitted_at=attempt.submitted_at,
        status=attempt.status.value,
        total_marks=result.total_marks,
        scored_marks=result.scored_marks,
        percentage=result.percentage,
        correct_count=result.correct_count,
        incorrect_count=result.incorrect_count,
        unattempted_count=result.unattempted_count,
        violation_count=violation_count,
    )
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="result_attempt_{attempt_id}.pdf"'},
    )