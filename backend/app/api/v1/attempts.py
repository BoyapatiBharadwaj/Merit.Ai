"""Student exam-taking endpoints."""
import json
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from app.api.deps import attempt_student, get_current_user, require_examiner, require_student
from app.core.config import settings
from app.core.security import create_attempt_token
from app.database.session import get_db
from app.models.enums import QuestionType
from app.models.user import User
from app.repositories import (
    admin_repository, attempt_repository, exam_repository, proctor_repository, user_repository,
)
from app.schemas.pagination import Page, PageParams, build_page
from app.schemas.attempt import (
    AttemptCommentRequest, AttemptReportOut, AttemptStatusOut, CodeAnswerRequest, CodeRunRequest, ExamResultOut,
    FinalizeAttemptRequest, ResetAttemptRequest,
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
    remaining = attempt_service.remaining_seconds(attempt)
    return StartAttemptResponse(
        attempt_id=attempt.id,
        exam_title=attempt.exam.title,
        duration_minutes=attempt.exam.duration_minutes,
        started_at=attempt.started_at,
        remaining_seconds=remaining,
        proctoring_enabled=attempt.exam.proctoring_enabled,
        question_ids_in_order=question_ids,
        # Derived from the attempt's OWN remaining time, not a fixed lifetime:
        # a 45-minute exam gets a token that dies 45 minutes (plus grace) from
        # now, not one that outlives it by hours.
        attempt_token=create_attempt_token(
            subject=str(user.id), role=user.role.name, attempt_id=attempt.id,
            expires_at=datetime.now(timezone.utc)
            + timedelta(seconds=remaining)
            + timedelta(minutes=settings.ATTEMPT_TOKEN_GRACE_MINUTES),
            # Carries the password epoch like any other token: an attempt token
            # is long-lived, so it is the LAST one that should survive the
            # account's password being reset mid-exam.
            user=user,
        ),
    )


@router.get("/{attempt_id}/status", response_model=AttemptStatusOut)
def get_attempt_status(attempt_id: int, db: Session = Depends(get_db),
                       user: User = Depends(attempt_student)):
    """Cheap, read-only heartbeat: is this attempt still live, and for how long.

    Also the client's recovery path. If the connection drops long enough for the
    deadline to pass, the server finalises the attempt (finalize_if_expired) and
    this reports it -- so the page learns its attempt is over from the server
    rather than guessing from its own timer.
    """
    student = user_repository.get_student_by_user_id(db, user.id)
    attempt = attempt_repository.get_attempt(db, attempt_id)
    if not attempt or not student or attempt.student_id != student.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Attempt not found.")
    attempt = attempt_service.finalize_if_expired(db, attempt)
    return AttemptStatusOut(
        attempt_id=attempt.id,
        status=attempt.status.value,
        remaining_seconds=attempt_service.remaining_seconds(attempt),
        submitted_at=attempt.submitted_at,
        server_time=datetime.now(timezone.utc),
    )


@router.get("/{attempt_id}/question/{question_id}")
def get_question_for_attempt(attempt_id: int, question_id: int, db: Session = Depends(get_db), user: User = Depends(attempt_student)):
    student = user_repository.get_student_by_user_id(db, user.id)
    attempt = attempt_service.ensure_attempt_is_active(db, student.id, attempt_id)
    if question_id not in {int(value) for value in attempt.question_order.split(",")}:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Question not found in this attempt.")
    question = exam_repository.get_question(db, question_id)
    if not question or question.section.exam_id != attempt.exam_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Question not found in this attempt.")
    existing_answer = next((answer for answer in attempt_repository.get_answers_for_attempt(db, attempt_id) if answer.question_id == question_id), None)

    # The version the server currently holds for this question, so the client
    # can continue the sequence instead of restarting it.
    #
    # This is what makes a page reload survivable. The client owns the counter
    # and bumps it per change; on a fresh page load its map is empty, so it
    # started again from 1 while the server still held 5 -- and every save the
    # candidate made after reloading was correctly refused as stale by a server
    # doing exactly what it was told, then reported to them as saved. Seeding
    # from here means the next change is version 6, and lands.
    stored_version = (existing_answer.answer_version or 0) if existing_answer else 0

    if question.question_type == QuestionType.CODING:
        return {
            "question_id": question.id,
            "answer_version": stored_version,
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
            "answer_version": stored_version,
            "question_type": "multi_select",
            "text": question.text,
            "marks": question.marks,
            "options": _ordered_options(attempt, question),
            "selected_option_ids": selected_ids,
        }
    return {
        "question_id": question.id,
        "answer_version": stored_version,
        "question_type": "mcq",
        "text": question.text,
        "marks": question.marks,
        "options": _ordered_options(attempt, question),
        "selected_option_id": existing_answer.selected_option_id if existing_answer else None,
    }


@router.get("/{attempt_id}/answers")
def get_answers(attempt_id: int, db: Session = Depends(get_db), user: User = Depends(attempt_student)):
    """Full saved-answers map for the in-progress attempt owned by the caller,
    used to repaint question-navigator state after a reload/reconnect (see
    'exam recovery' in exam.js's beginExam)."""
    student = user_repository.get_student_by_user_id(db, user.id)
    return attempt_service.get_answers_map(db, student.id, attempt_id)


@router.put("/{attempt_id}/answer")
def save_answer(attempt_id: int, payload: SaveAnswerRequest, db: Session = Depends(get_db), user: User = Depends(attempt_student)):
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
def save_multi_answer(attempt_id: int, payload: SaveMultiAnswerRequest, db: Session = Depends(get_db), user: User = Depends(attempt_student)):
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
def save_code_answer(attempt_id: int, payload: CodeAnswerRequest, db: Session = Depends(get_db), user: User = Depends(attempt_student)):
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
def run_code_sample(attempt_id: int, payload: CodeRunRequest, db: Session = Depends(get_db), user: User = Depends(attempt_student)):
    """Ungraded 'Run' button: executes against sample test cases only, for
    immediate feedback. Never touches the hidden test cases used for grading."""
    student = user_repository.get_student_by_user_id(db, user.id)
    return attempt_service.run_sample_test_cases(db, student.id, attempt_id, payload.question_id, payload.source_code)


def _result_out(attempt_id: int, result, *, released: bool = True) -> ExamResultOut:
    """Shapes an ExamResult into the wire response, withholding every score
    field when `released` is False -- see Exam.results_released. Callers pass
    released=False only for a STUDENT caller of an exam whose examiner has
    chosen not to show results yet; staff always pass released=True."""
    if not released:
        return ExamResultOut(attempt_id=attempt_id, results_released=False)
    return ExamResultOut(attempt_id=attempt_id, results_released=True, **{key: getattr(result, key) for key in [
        "total_marks", "scored_marks", "percentage", "correct_count", "incorrect_count", "unattempted_count",
    ]})


@router.post("/{attempt_id}/finalize", response_model=ExamResultOut)
def finalize_attempt(attempt_id: int, payload: FinalizeAttemptRequest,
                     db: Session = Depends(get_db), user: User = Depends(attempt_student)):
    """Submit, carrying the candidate's final answers in the same request.

    The submission the exam page actually makes. Sending the answers with the
    submission rather than just before it is what stops the last change being
    graded from a previous version -- see attempt_service.finalize_attempt.

    Safe to retry: an attempt that is already submitted returns its existing
    result rather than an error, which is what lets the client keep retrying a
    submission over a bad connection without ever showing the candidate a
    failure for something that already succeeded.
    """
    student = user_repository.get_student_by_user_id(db, user.id)
    result = attempt_service.finalize_attempt(
        db, student.id, attempt_id, final_answers=payload.final_answers,
    )
    attempt = attempt_repository.get_attempt(db, attempt_id)
    return _result_out(attempt_id, result, released=attempt.exam.results_released)


@router.post("/{attempt_id}/submit", response_model=ExamResultOut)
def submit_attempt(attempt_id: int, db: Session = Depends(get_db), user: User = Depends(attempt_student)):
    """Submit with no answer payload -- everything already autosaved.

    Kept alongside /finalize for clients that have nothing outstanding to send,
    and because a candidate mid-exam on a cached bundle must keep being able to
    submit through a deployment.

    The `auto` query parameter this used to take is gone. It decided whether the
    attempt was recorded as a deliberate submission or a timeout, and it came
    from the candidate: `?auto=true` before the deadline made a normal
    submission look like they had run out of time, and `?auto=false` after it
    made a timeout look deliberate. It is now read from the server's clock.
    """
    student = user_repository.get_student_by_user_id(db, user.id)
    result = attempt_service.finalize_attempt(db, student.id, attempt_id)
    attempt = attempt_repository.get_attempt(db, attempt_id)
    return _result_out(attempt_id, result, released=attempt.exam.results_released)


@router.get("/{attempt_id}/result", response_model=ExamResultOut)
def get_result(attempt_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    attempt = attempt_repository.get_attempt(db, attempt_id)
    if not attempt or not _can_access_attempt(db, user, attempt):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Result not found.")
    attempt = attempt_service.finalize_if_expired(db, attempt)
    result = attempt_repository.get_result(db, attempt_id)
    if not result:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Result not available yet.")
    # Staff (admin, or the exam's owning examiner) always see the real score;
    # only a STUDENT's own view is gated by the exam's results-visibility
    # settings -- see Exam.results_released.
    released = True if user.role.name != "student" else attempt.exam.results_released
    return _result_out(attempt_id, result, released=released)


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
    # A candidate reading their OWN report does not get the answer key until the
    # examiner releases it -- see build_full_report. Staff always do.
    return attempt_service.build_full_report(
        db, attempt, for_candidate=(user.role.name == "student"))


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
    rows = []
    for attempt in attempts:
        result = attempt_repository.get_result(db, attempt.id)
        # Same gate as GET /result and /report: an exam whose examiner hasn't
        # released results yet must not leak the score into a candidate's OWN
        # history list either -- see Exam.results_released.
        released = attempt.exam.results_released
        rows.append({
            "attempt_id": attempt.id, "exam_id": attempt.exam_id, "exam_title": attempt.exam.title,
            "status": attempt.status.value, "started_at": attempt.started_at, "submitted_at": attempt.submitted_at,
            "results_released": released,
            "scored_marks": result.scored_marks if (result and released) else None,
            "total_marks": result.total_marks if (result and released) else None,
            "percentage": result.percentage if (result and released) else None,
        })
    return rows


@router.get("/exam/{exam_id}", response_model=Page[dict])
def attempts_for_exam(exam_id: int, params: PageParams = Depends(), search: str = "",
                      db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """One page of this exam's attempts.

    This returned every attempt the exam had ever had, and the browser sliced
    them for display -- so viewing 25 rows cost the transfer and parse of all of
    them, repeatedly, for every examiner watching a live sitting. It also ran a
    separate result query per attempt; results now come back in one batch.
    """
    exam = exam_repository.get_exam(db, exam_id)
    if not exam or (user.role.name != "admin" and (not user.examiner_profile or exam.examiner_id != user.examiner_profile.id)):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Exam not found.")

    rows, total = attempt_repository.paginated_attempts_for_exam(
        db, exam_id, offset=params.offset, limit=params.page_size, search=search,
    )
    attempts = [attempt_service.finalize_if_expired(db, row) for row in rows]
    results = attempt_repository.results_for_attempts(db, [a.id for a in attempts])
    violation_counts = admin_repository.violation_counts_for_attempts(db, [a.id for a in attempts])
    items = [{
        "attempt_id": attempt.id,
        "student_id": attempt.student_id,
        "student_name": attempt.student.user.full_name if attempt.student and attempt.student.user else None,
        "status": attempt.status.value,
        "started_at": attempt.started_at,
        "submitted_at": attempt.submitted_at,
        "scored_marks": results[attempt.id].scored_marks if attempt.id in results else None,
        "total_marks": results[attempt.id].total_marks if attempt.id in results else None,
        # So the examiner can see at a glance which attempts need a look --
        # the actual review (evidence, risk score, decisions) lives one click
        # away at GET /attempts/{id}/staff-report, not on this list.
        "violation_count": violation_counts.get(attempt.id, 0),
    } for attempt in attempts]
    return build_page(items, total, params)


@router.get("/exam/{exam_id}/export")
def export_exam_attempts_csv(exam_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """The exam's live results CSV: one row per candidate who has sat it so
    far (name, violation count, start/end time, result), regenerated fresh
    from the database on every download -- see attempt_service.build_exam_csv
    for why that beats a physically maintained file. Same ownership rule as
    every other exam-scoped attempt endpoint here: the owning examiner, or
    any admin.
    """
    exam = exam_repository.get_exam(db, exam_id)
    if not exam or (user.role.name != "admin" and (not user.examiner_profile or exam.examiner_id != user.examiner_profile.id)):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Exam not found.")
    csv_bytes = attempt_service.build_exam_csv(db, exam)
    safe_title = "".join(c if c.isalnum() or c in "-_" else "_" for c in exam.title)[:60] or "exam"
    return Response(
        content=csv_bytes,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{safe_title}_results.csv"'},
    )


@router.get("/exam/{exam_id}/active", response_model=list[dict])
def active_attempts_for_exam(exam_id: int, db: Session = Depends(get_db),
                             user: User = Depends(get_current_user)):
    """Only the candidates currently writing -- what live monitoring polls.

    That page used to fetch every attempt for the exam every ten seconds and
    filter in the browser, so watching two live candidates cost the same as
    downloading the entire sitting history, repeatedly, for as long as the page
    stayed open. It also ran a separate result query per attempt.
    """
    exam = exam_repository.get_exam(db, exam_id)
    if not exam or (user.role.name != "admin"
                    and (not user.examiner_profile or exam.examiner_id != user.examiner_profile.id)):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Exam not found.")

    attempts = attempt_repository.active_attempts_for_exam(db, exam_id)
    violations = admin_repository.violation_counts_for_attempts(db, [a.id for a in attempts])
    return [{
        "attempt_id": attempt.id,
        "student_id": attempt.student_id,
        "student_name": attempt.student.user.full_name if attempt.student and attempt.student.user else None,
        "started_at": attempt.started_at,
        "remaining_seconds": attempt_service.remaining_seconds(attempt),
        "violation_count": violations.get(attempt.id, 0),
    } for attempt in attempts]


@router.get("/exam/{exam_id}/archived", response_model=list[dict])
def archived_attempts_for_exam(exam_id: int, db: Session = Depends(get_db),
                               user: User = Depends(get_current_user)):
    """Attempts superseded by a reset.

    These used to be deleted outright, so this endpoint could not have existed:
    the reset destroyed the answers, result, comments and every proctoring event
    along with the attempt. They are now archived instead, which is what makes a
    disputed exam answerable after a retake has been granted.
    """
    exam = exam_repository.get_exam(db, exam_id)
    if not exam or (user.role.name != "admin"
                    and (not user.examiner_profile or exam.examiner_id != user.examiner_profile.id)):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Exam not found.")

    attempts = attempt_repository.list_archived_attempts_for_exam(db, exam_id)
    results = attempt_repository.results_for_attempts(db, [a.id for a in attempts])
    return [{
        "attempt_id": attempt.id,
        "student_id": attempt.student_id,
        "student_name": attempt.student.user.full_name if attempt.student and attempt.student.user else None,
        "status": attempt.status.value,
        "started_at": attempt.started_at,
        "submitted_at": attempt.submitted_at,
        "archived_at": attempt.archived_at,
        "scored_marks": results[attempt.id].scored_marks if attempt.id in results else None,
        "total_marks": results[attempt.id].total_marks if attempt.id in results else None,
    } for attempt in attempts]


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
    # Staff can always pull the report; a student cannot download the very
    # numbers /result and /report are withholding from them -- see
    # Exam.results_released.
    if user.role.name == "student" and not attempt.exam.results_released:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Results for this exam have not been released yet.")
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