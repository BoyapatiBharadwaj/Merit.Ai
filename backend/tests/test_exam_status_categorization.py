"""Automatic exam status categorization (Upcoming / Ongoing / Completed /
Missed) on GET /exams/available -- see exam_service.compute_candidate_status.

These deliberately manipulate started_at/submitted_at directly in the
database for the "attempt already expired" cases: simulating an actual wait
would make the suite slow, and the point under test is what the *read path*
does with a stale row, not the passage of real time.
"""
from datetime import datetime, timedelta, timezone

from tests.conftest import auth_headers
from tests.test_exam_workflow import _create_examiner_and_login, _register_student_and_login


def _build_exam(client, examiner_headers, *, start_time=None, end_time=None, duration_minutes=30):
    payload = {
        "title": "Scheduled Exam", "description": "basic", "duration_minutes": duration_minutes,
        "randomize_questions": False, "proctoring_enabled": False,
    }
    if start_time is not None:
        payload["start_time"] = start_time.isoformat()
    if end_time is not None:
        payload["end_time"] = end_time.isoformat()
    exam = client.post("/api/v1/exams", json=payload, headers=examiner_headers).json()

    section = client.post(f"/api/v1/exams/{exam['id']}/sections", json={
        "title": "Section 1", "order_index": 0,
    }, headers=examiner_headers).json()

    client.post(f"/api/v1/exams/sections/{section['id']}/questions", json={
        "text": "2 + 2 = ?", "marks": 5, "order_index": 0,
        "options": [{"text": "3", "is_correct": False}, {"text": "4", "is_correct": True}],
    }, headers=examiner_headers)

    publish = client.post(f"/api/v1/exams/{exam['id']}/publish", headers=examiner_headers)
    assert publish.status_code == 200, publish.text
    return exam["id"]


def _status_for(client, student_headers, exam_id):
    listed = client.get("/api/v1/exams/available", headers=student_headers).json()
    match = next((e for e in listed if e["id"] == exam_id), None)
    assert match is not None, f"exam {exam_id} not present in /exams/available"
    return match


def test_exam_scheduled_in_the_future_is_upcoming(client, seed_roles, admin_token):
    examiner_headers = auth_headers(_create_examiner_and_login(client, admin_token))
    future = datetime.now(timezone.utc) + timedelta(days=2)
    exam_id = _build_exam(client, examiner_headers, start_time=future)
    student_headers = auth_headers(_register_student_and_login(client))

    entry = _status_for(client, student_headers, exam_id)
    assert entry["candidate_status"] == "upcoming"

    # The list surfacing it as upcoming must agree with the real gate.
    start = client.post(f"/api/v1/attempts/start/{exam_id}", headers=student_headers)
    assert start.status_code == 400


def test_exam_with_no_window_and_no_attempt_is_ongoing(client, seed_roles, admin_token):
    examiner_headers = auth_headers(_create_examiner_and_login(client, admin_token))
    exam_id = _build_exam(client, examiner_headers)
    student_headers = auth_headers(_register_student_and_login(client))

    entry = _status_for(client, student_headers, exam_id)
    assert entry["candidate_status"] == "ongoing"
    assert entry["attempt_id"] is None


def test_exam_whose_window_closed_with_no_attempt_is_missed(client, seed_roles, admin_token):
    """Built with a valid (future) window -- a start/end time in the past is
    now rejected at creation time (see ExamCreate.validate_window) -- then
    back-dated directly in the database, the same "simulate time having
    passed" pattern used by test_expired_in_progress_attempt_is_auto_finalized_on_read
    below, rather than through the API which would (correctly) refuse it."""
    examiner_headers = auth_headers(_create_examiner_and_login(client, admin_token))
    soon_start = datetime.now(timezone.utc) + timedelta(minutes=5)
    soon_end = datetime.now(timezone.utc) + timedelta(minutes=10)
    exam_id = _build_exam(client, examiner_headers, start_time=soon_start, end_time=soon_end)

    from app.database.session import SessionLocal
    from app.models.exam import Exam

    session = SessionLocal()
    try:
        exam = session.query(Exam).filter(Exam.id == exam_id).first()
        exam.start_time = datetime.now(timezone.utc) - timedelta(days=2)
        exam.end_time = datetime.now(timezone.utc) - timedelta(days=1)
        session.commit()
    finally:
        session.close()

    student_headers = auth_headers(_register_student_and_login(client))

    entry = _status_for(client, student_headers, exam_id)
    assert entry["candidate_status"] == "missed"

    start = client.post(f"/api/v1/attempts/start/{exam_id}", headers=student_headers)
    assert start.status_code == 400


def test_in_progress_attempt_is_ongoing_even_past_the_exam_window(client, seed_roles, admin_token):
    """A student who starts near the end of an open window and is still
    working when it closes should see "Ongoing", not "Missed" -- their own
    attempt deadline (started_at + duration), not the exam's publish window,
    governs submission. See compute_candidate_status's ordering."""
    examiner_headers = auth_headers(_create_examiner_and_login(client, admin_token))
    exam_id = _build_exam(client, examiner_headers, duration_minutes=60)
    student_headers = auth_headers(_register_student_and_login(client))

    start = client.post(f"/api/v1/attempts/start/{exam_id}", headers=student_headers)
    assert start.status_code == 200, start.text

    entry = _status_for(client, student_headers, exam_id)
    assert entry["candidate_status"] == "ongoing"
    assert entry["attempt_status"] == "in_progress"


def test_submitted_attempt_is_completed(client, seed_roles, admin_token):
    examiner_headers = auth_headers(_create_examiner_and_login(client, admin_token))
    exam_id = _build_exam(client, examiner_headers)
    student_headers = auth_headers(_register_student_and_login(client))

    start = client.post(f"/api/v1/attempts/start/{exam_id}", headers=student_headers)
    attempt_id = start.json()["attempt_id"]
    submit = client.post(f"/api/v1/attempts/{attempt_id}/submit", headers=student_headers)
    assert submit.status_code == 200, submit.text

    entry = _status_for(client, student_headers, exam_id)
    assert entry["candidate_status"] == "completed"
    assert entry["percentage"] is not None


def test_expired_in_progress_attempt_is_auto_finalized_on_read(client, seed_roles, admin_token):
    """The safety net behind the client-side timer: an attempt whose own
    deadline has passed is auto-submitted the moment anyone reads it, with no
    background scheduler and no action required from the student."""
    examiner_headers = auth_headers(_create_examiner_and_login(client, admin_token))
    exam_id = _build_exam(client, examiner_headers, duration_minutes=10)
    student_headers = auth_headers(_register_student_and_login(client))

    start = client.post(f"/api/v1/attempts/start/{exam_id}", headers=student_headers)
    attempt_id = start.json()["attempt_id"]

    # Simulate time passing: back-date started_at well past the 10-minute
    # duration, as if the student closed their laptop mid-exam.
    from app.database.session import SessionLocal
    from app.models.attempt import StudentExamAttempt

    session = SessionLocal()
    try:
        attempt = session.query(StudentExamAttempt).filter(StudentExamAttempt.id == attempt_id).first()
        attempt.started_at = datetime.now(timezone.utc) - timedelta(minutes=30)
        session.commit()
    finally:
        session.close()

    # Resuming it (before anything else has touched it) is what actually
    # discovers the expiry and finalizes it -- this is the "student closes
    # the laptop and comes back later" path, and it must redirect to the
    # report instead of dead-ending on a generic error.
    resume = client.post(f"/api/v1/attempts/start/{exam_id}", headers=student_headers)
    assert resume.status_code == 400
    detail = resume.json()["detail"]
    assert detail["code"] == "attempt_expired_auto_submitted"
    assert detail["attempt_id"] == attempt_id

    # And separately: a read path that gets there first (an examiner opening
    # their attempts list, or the student's own dashboard) also finalizes it
    # opportunistically, with no special-casing needed by the caller.
    entry = _status_for(client, student_headers, exam_id)
    assert entry["candidate_status"] == "completed"
    assert entry["attempt_status"] == "auto_submitted"
    assert entry["percentage"] is not None
