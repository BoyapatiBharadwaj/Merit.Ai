"""Exam schedule editing rules (draft vs. published vs. started), schedule
date/time validation, the student-attempt reset feature, and the now-dynamic
number of MCQ options."""
from datetime import datetime, timedelta, timezone

from tests.conftest import auth_headers
from tests.test_exam_workflow import _create_examiner_and_login, _register_student_and_login


def _make_draft_exam(client, examiner_headers, **overrides):
    payload = {"title": "Schedulable Exam", "duration_minutes": 30, "proctoring_enabled": False, "randomize_questions": False}
    payload.update(overrides)
    exam = client.post("/api/v1/exams", json=payload, headers=examiner_headers).json()
    section = client.post(f"/api/v1/exams/{exam['id']}/sections", json={
        "title": "Section 1", "order_index": 0,
    }, headers=examiner_headers).json()
    client.post(f"/api/v1/exams/sections/{section['id']}/questions", json={
        "text": "2 + 2 = ?", "marks": 5, "order_index": 0,
        "options": [{"text": "3", "is_correct": False}, {"text": "4", "is_correct": True}],
    }, headers=examiner_headers)
    return exam["id"]


def _publish(client, examiner_headers, exam_id):
    response = client.post(f"/api/v1/exams/{exam_id}/publish", headers=examiner_headers)
    assert response.status_code == 200, response.text


# --- - ---
# Validation -------------------------------------------------------------------------

def test_create_exam_rejects_a_start_time_in_the_past(client, seed_roles, admin_token):
    examiner_headers = auth_headers(_create_examiner_and_login(client, admin_token))
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    response = client.post("/api/v1/exams", json={
        "title": "Backdated Exam", "duration_minutes": 30, "start_time": past,
    }, headers=examiner_headers)
    assert response.status_code == 422


def test_create_exam_rejects_end_time_before_start_time(client, seed_roles, admin_token):
    examiner_headers = auth_headers(_create_examiner_and_login(client, admin_token))
    start = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    end = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    response = client.post("/api/v1/exams", json={
        "title": "Backwards Exam", "duration_minutes": 30, "start_time": start, "end_time": end,
    }, headers=examiner_headers)
    assert response.status_code == 422


def test_create_exam_allows_a_start_time_a_few_seconds_in_the_past(client, seed_roles, admin_token):
    """The 30s grace window absorbs the gap between when a user picks "now"
    in their browser and when the request lands on the server."""
    examiner_headers = auth_headers(_create_examiner_and_login(client, admin_token))
    almost_now = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()
    response = client.post("/api/v1/exams", json={
        "title": "Just Now Exam", "duration_minutes": 30, "start_time": almost_now,
    }, headers=examiner_headers)
    assert response.status_code == 201, response.text


# ---------------------------------------------------------------------------
# Schedule editing rules
# ---------------------------------------------------------------------------

def test_draft_exam_schedule_is_fully_editable(client, seed_roles, admin_token):
    examiner_headers = auth_headers(_create_examiner_and_login(client, admin_token))
    exam_id = _make_draft_exam(client, examiner_headers)
    start = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    end = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()

    response = client.patch(f"/api/v1/exams/{exam_id}/schedule", json={"start_time": start, "end_time": end}, headers=examiner_headers)
    assert response.status_code == 200, response.text
    assert response.json()["start_time"] is not None


def test_draft_exam_full_details_are_editable(client, seed_roles, admin_token):
    examiner_headers = auth_headers(_create_examiner_and_login(client, admin_token))
    exam_id = _make_draft_exam(client, examiner_headers)

    response = client.put(f"/api/v1/exams/{exam_id}", json={
        "title": "Renamed Exam", "duration_minutes": 45, "pass_percentage": 55,
    }, headers=examiner_headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["title"] == "Renamed Exam"
    assert body["duration_minutes"] == 45
    assert body["pass_percentage"] == 55


def test_cannot_full_edit_a_published_exam(client, seed_roles, admin_token):
    examiner_headers = auth_headers(_create_examiner_and_login(client, admin_token))
    exam_id = _make_draft_exam(client, examiner_headers)
    _publish(client, examiner_headers, exam_id)

    response = client.put(f"/api/v1/exams/{exam_id}", json={
        "title": "Should Not Apply", "duration_minutes": 45,
    }, headers=examiner_headers)
    assert response.status_code == 400


def test_published_exam_not_yet_started_can_still_edit_both_start_and_end(client, seed_roles, admin_token):
    examiner_headers = auth_headers(_create_examiner_and_login(client, admin_token))
    future_start = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
    exam_id = _make_draft_exam(client, examiner_headers, start_time=future_start)
    _publish(client, examiner_headers, exam_id)

    new_start = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
    new_end = (datetime.now(timezone.utc) + timedelta(days=4)).isoformat()
    response = client.patch(f"/api/v1/exams/{exam_id}/schedule", json={"start_time": new_start, "end_time": new_end}, headers=examiner_headers)
    assert response.status_code == 200, response.text
    assert response.json()["end_time"] is not None


def test_published_and_started_exam_locks_the_start_time(client, seed_roles, admin_token):
    """No start_time at all means the exam opened the instant it was
    published (see exam_service.has_exam_started) -- so it counts as
    started immediately, and only the end time may still move."""
    examiner_headers = auth_headers(_create_examiner_and_login(client, admin_token))
    exam_id = _make_draft_exam(client, examiner_headers)  # no start_time -> open now
    _publish(client, examiner_headers, exam_id)

    attempted_new_start = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    blocked = client.patch(f"/api/v1/exams/{exam_id}/schedule", json={"start_time": attempted_new_start, "end_time": None}, headers=examiner_headers)
    assert blocked.status_code == 400

    new_end = (datetime.now(timezone.utc) + timedelta(hours=3)).isoformat()
    allowed = client.patch(f"/api/v1/exams/{exam_id}/schedule", json={"start_time": None, "end_time": new_end}, headers=examiner_headers)
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["end_time"] is not None


def test_resending_the_same_start_time_is_not_treated_as_a_change(client, seed_roles, admin_token):
    """The frontend always resends the full schedule (a "replace both fields" form, not a partial patch)."""
    examiner_headers = auth_headers(_create_examiner_and_login(client, admin_token))
    exam_id = _make_draft_exam(client, examiner_headers)  # no start_time -> open now
    _publish(client, examiner_headers, exam_id)

    new_end = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    response = client.patch(f"/api/v1/exams/{exam_id}/schedule", json={"start_time": None, "end_time": new_end}, headers=examiner_headers)
    assert response.status_code == 200, response.text


def test_schedule_update_rejects_start_in_the_past_when_still_editable(client, seed_roles, admin_token):
    examiner_headers = auth_headers(_create_examiner_and_login(client, admin_token))
    exam_id = _make_draft_exam(client, examiner_headers)
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()

    # Unlike ExamCreate, this check depends on DB state (whether the exam has already started),
    # so it's enforced by the service layer (400), not the schema (422).
    response = client.patch(f"/api/v1/exams/{exam_id}/schedule", json={"start_time": past, "end_time": None}, headers=examiner_headers)
    assert response.status_code == 400


def test_schedule_update_rejects_end_before_start(client, seed_roles, admin_token):
    examiner_headers = auth_headers(_create_examiner_and_login(client, admin_token))
    exam_id = _make_draft_exam(client, examiner_headers)
    start = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
    end = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()

    response = client.patch(f"/api/v1/exams/{exam_id}/schedule", json={"start_time": start, "end_time": end}, headers=examiner_headers)
    assert response.status_code == 422


def test_other_examiner_cannot_edit_schedule(client, seed_roles, admin_token):
    owner_headers = auth_headers(_create_examiner_and_login(client, admin_token, email="owner2@example.com"))
    other_headers = auth_headers(_create_examiner_and_login(client, admin_token, email="other2@example.com"))
    exam_id = _make_draft_exam(client, owner_headers)

    response = client.patch(f"/api/v1/exams/{exam_id}/schedule", json={"start_time": None, "end_time": None}, headers=other_headers)
    assert response.status_code == 403


# --- - ---
# Reset student exam -------------------------------------------------------------------------

def test_examiner_can_reset_a_students_attempt(client, seed_roles, admin_token):
    """Submits the attempt first (not just starts it) so the "before" state is unambiguous."""
    examiner_headers = auth_headers(_create_examiner_and_login(client, admin_token))
    exam_id = _make_draft_exam(client, examiner_headers)
    _publish(client, examiner_headers, exam_id)
    student_headers = auth_headers(_register_student_and_login(client))

    start = client.post(f"/api/v1/attempts/start/{exam_id}", headers=student_headers)
    assert start.status_code == 200, start.text
    attempt_id = start.json()["attempt_id"]
    submit = client.post(f"/api/v1/attempts/{attempt_id}/submit", headers=student_headers)
    assert submit.status_code == 200, submit.text

    # Before reset: a submitted attempt can't be restarted.
    blocked = client.post(f"/api/v1/attempts/start/{exam_id}", headers=student_headers)
    assert blocked.status_code == 400

    student = client.get("/api/v1/users/me", headers=student_headers).json()
    roster = client.get("/api/v1/users/students", headers=examiner_headers).json()
    student_id = next(s["id"] for s in roster if s["email"] == student["email"])

    reset = client.post(f"/api/v1/attempts/exam/{exam_id}/student/{student_id}/reset", json={
        "reason": "Browser crashed mid-exam",
    }, headers=examiner_headers)
    assert reset.status_code == 200, reset.text

    # After reset: starting fresh succeeds again -- the old (submitted)
    # attempt is gone, not merely handed back.
    resume = client.post(f"/api/v1/attempts/start/{exam_id}", headers=student_headers)
    assert resume.status_code == 200, resume.text

    resets = client.get(f"/api/v1/attempts/exam/{exam_id}/resets", headers=examiner_headers)
    assert resets.status_code == 200, resets.text
    entries = resets.json()
    assert len(entries) == 1
    assert entries[0]["reason"] == "Browser crashed mid-exam"
    assert entries[0]["previous_attempt_id"] == attempt_id
    assert entries[0]["previous_status"] == "submitted"
    assert entries[0]["student_id"] == student_id


def test_reset_requires_a_reason(client, seed_roles, admin_token):
    examiner_headers = auth_headers(_create_examiner_and_login(client, admin_token))
    exam_id = _make_draft_exam(client, examiner_headers)
    _publish(client, examiner_headers, exam_id)
    student_headers = auth_headers(_register_student_and_login(client))
    client.post(f"/api/v1/attempts/start/{exam_id}", headers=student_headers)

    student = client.get("/api/v1/users/me", headers=student_headers).json()
    roster = client.get("/api/v1/users/students", headers=examiner_headers).json()
    student_id = next(s["id"] for s in roster if s["email"] == student["email"])

    response = client.post(f"/api/v1/attempts/exam/{exam_id}/student/{student_id}/reset", json={"reason": ""}, headers=examiner_headers)
    assert response.status_code == 422


def test_cannot_reset_a_student_who_never_attempted(client, seed_roles, admin_token):
    examiner_headers = auth_headers(_create_examiner_and_login(client, admin_token))
    exam_id = _make_draft_exam(client, examiner_headers)
    _publish(client, examiner_headers, exam_id)
    student_headers = auth_headers(_register_student_and_login(client))

    student = client.get("/api/v1/users/me", headers=student_headers).json()
    roster = client.get("/api/v1/users/students", headers=examiner_headers).json()
    student_id = next(s["id"] for s in roster if s["email"] == student["email"])

    response = client.post(f"/api/v1/attempts/exam/{exam_id}/student/{student_id}/reset", json={
        "reason": "Never attempted",
    }, headers=examiner_headers)
    assert response.status_code == 404


def test_other_examiner_cannot_reset_a_students_attempt(client, seed_roles, admin_token):
    owner_headers = auth_headers(_create_examiner_and_login(client, admin_token, email="owner3@example.com"))
    other_headers = auth_headers(_create_examiner_and_login(client, admin_token, email="other3@example.com"))
    exam_id = _make_draft_exam(client, owner_headers)
    _publish(client, owner_headers, exam_id)
    student_headers = auth_headers(_register_student_and_login(client))
    client.post(f"/api/v1/attempts/start/{exam_id}", headers=student_headers)

    student = client.get("/api/v1/users/me", headers=student_headers).json()
    roster = client.get("/api/v1/users/students", headers=owner_headers).json()
    student_id = next(s["id"] for s in roster if s["email"] == student["email"])

    response = client.post(f"/api/v1/attempts/exam/{exam_id}/student/{student_id}/reset", json={
        "reason": "Trying to interfere",
    }, headers=other_headers)
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# Dynamic number of options
# ---------------------------------------------------------------------------

def test_question_with_only_two_options_is_supported(client, seed_roles, admin_token):
    examiner_headers = auth_headers(_create_examiner_and_login(client, admin_token))
    exam_id = _make_draft_exam(client, examiner_headers)
    exam = client.get(f"/api/v1/exams/{exam_id}", headers=examiner_headers).json()
    section_id = exam["sections"][0]["id"]

    response = client.post(f"/api/v1/exams/sections/{section_id}/questions", json={
        "text": "True or False: the sky is blue.", "marks": 2, "order_index": 1,
        "options": [{"text": "True", "is_correct": True}, {"text": "False", "is_correct": False}],
    }, headers=examiner_headers)
    assert response.status_code == 201, response.text
    assert len(response.json()["options"]) == 2


def test_question_with_more_than_four_options_is_supported(client, seed_roles, admin_token):
    examiner_headers = auth_headers(_create_examiner_and_login(client, admin_token))
    exam_id = _make_draft_exam(client, examiner_headers)
    exam = client.get(f"/api/v1/exams/{exam_id}", headers=examiner_headers).json()
    section_id = exam["sections"][0]["id"]

    response = client.post(f"/api/v1/exams/sections/{section_id}/questions", json={
        "text": "Pick the prime number.", "marks": 2, "order_index": 1,
        "options": [
            {"text": "4", "is_correct": False}, {"text": "6", "is_correct": False},
            {"text": "7", "is_correct": True}, {"text": "8", "is_correct": False},
            {"text": "9", "is_correct": False}, {"text": "10", "is_correct": False},
        ],
    }, headers=examiner_headers)
    assert response.status_code == 201, response.text
    assert len(response.json()["options"]) == 6


def test_a_single_option_is_still_rejected(client, seed_roles, admin_token):
    examiner_headers = auth_headers(_create_examiner_and_login(client, admin_token))
    exam_id = _make_draft_exam(client, examiner_headers)
    exam = client.get(f"/api/v1/exams/{exam_id}", headers=examiner_headers).json()
    section_id = exam["sections"][0]["id"]

    response = client.post(f"/api/v1/exams/sections/{section_id}/questions", json={
        "text": "Not really a choice.", "marks": 1, "order_index": 1,
        "options": [{"text": "Only one", "is_correct": True}],
    }, headers=examiner_headers)
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Exam details editing (instructions, randomize_options, full-field coverage)
# ---------------------------------------------------------------------------

def test_draft_exam_details_edit_covers_instructions_and_randomize_options(client, seed_roles, admin_token):
    examiner_headers = auth_headers(_create_examiner_and_login(client, admin_token))
    exam_id = _make_draft_exam(client, examiner_headers)

    response = client.put(f"/api/v1/exams/{exam_id}", json={
        "title": "Fully Configured Exam",
        "description": "A description.",
        "instructions": "No calculators. Answer every question.",
        "duration_minutes": 60,
        "pass_percentage": 70,
        "randomize_questions": True,
        "randomize_options": False,
        "proctoring_enabled": False,
    }, headers=examiner_headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["instructions"] == "No calculators. Answer every question."
    assert body["randomize_options"] is False
    assert body["randomize_questions"] is True
    assert body["proctoring_enabled"] is False

    # Persisted, not just echoed back -- a fresh GET must agree.
    reloaded = client.get(f"/api/v1/exams/{exam_id}", headers=examiner_headers).json()
    assert reloaded["instructions"] == "No calculators. Answer every question."
    assert reloaded["randomize_options"] is False


def test_details_edit_ignores_schedule_fields_entirely(client, seed_roles, admin_token):
    """ExamDetailsUpdate has no start_time/end_time fields at all, so even a
    client that sneaks them into the PUT body can't use this endpoint to
    bypass the schedule's own (state-dependent) validation rules -- Pydantic
    silently drops fields the schema doesn't declare."""
    examiner_headers = auth_headers(_create_examiner_and_login(client, admin_token))
    future_start = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    exam_id = _make_draft_exam(client, examiner_headers, start_time=future_start)
    before = client.get(f"/api/v1/exams/{exam_id}", headers=examiner_headers).json()

    response = client.put(f"/api/v1/exams/{exam_id}", json={
        "title": "Still Draft", "duration_minutes": 30,
        "start_time": "2000-01-01T00:00:00Z", "end_time": "2000-01-02T00:00:00Z",
    }, headers=examiner_headers)
    assert response.status_code == 200, response.text

    after = client.get(f"/api/v1/exams/{exam_id}", headers=examiner_headers).json()
    assert after["start_time"] == before["start_time"]
    assert after["end_time"] == before["end_time"]


def test_create_exam_defaults_randomize_options_to_true(client, seed_roles, admin_token):
    examiner_headers = auth_headers(_create_examiner_and_login(client, admin_token))
    response = client.post("/api/v1/exams", json={
        "title": "Default Options Exam", "duration_minutes": 30,
    }, headers=examiner_headers)
    assert response.status_code == 201, response.text
    assert response.json()["randomize_options"] is True


# ---------------------------------------------------------------------------
# Randomize answer choices (per-attempt option shuffle)
# ---------------------------------------------------------------------------

def _make_published_exam_with_many_options(client, examiner_headers, *, randomize_options):
    exam = client.post("/api/v1/exams", json={
        "title": "Many Options Exam", "duration_minutes": 30,
        "proctoring_enabled": False, "randomize_questions": False,
        "randomize_options": randomize_options,
    }, headers=examiner_headers).json()
    section = client.post(f"/api/v1/exams/{exam['id']}/sections", json={
        "title": "Section 1", "order_index": 0,
    }, headers=examiner_headers).json()
    # Eight options: with randomization on, the odds of an untouched shuffle
    # coincidentally landing back on the authored order are 1 in 8! (40320).
    options = [{"text": str(n), "is_correct": n == 5} for n in range(1, 9)]
    client.post(f"/api/v1/exams/sections/{section['id']}/questions", json={
        "text": "Pick 5.", "marks": 5, "order_index": 0, "options": options,
    }, headers=examiner_headers)
    exam_detail = client.get(f"/api/v1/exams/{exam['id']}", headers=examiner_headers).json()
    natural_ids = [o["id"] for o in exam_detail["sections"][0]["questions"][0]["options"]]
    publish = client.post(f"/api/v1/exams/{exam['id']}/publish", headers=examiner_headers)
    assert publish.status_code == 200, publish.text
    return exam["id"], natural_ids


def test_randomize_options_shuffles_choices_but_grades_and_stays_stable(client, seed_roles, admin_token):
    examiner_headers = auth_headers(_create_examiner_and_login(client, admin_token))
    exam_id, natural_ids = _make_published_exam_with_many_options(client, examiner_headers, randomize_options=True)
    student_headers = auth_headers(_register_student_and_login(client))

    start = client.post(f"/api/v1/attempts/start/{exam_id}", headers=student_headers)
    assert start.status_code == 200, start.text
    attempt_id = start.json()["attempt_id"]
    question_id = start.json()["question_ids_in_order"][0]

    first_fetch = client.get(f"/api/v1/attempts/{attempt_id}/question/{question_id}", headers=student_headers).json()
    shuffled_ids = [o["id"] for o in first_fetch["options"]]

    # Same set of options, just reordered -- nothing added, lost, or duped.
    assert sorted(shuffled_ids) == sorted(natural_ids)
    assert shuffled_ids != natural_ids

    # Re-fetching must return the exact same order (computed once at attempt
    # start and persisted), not a fresh shuffle per request.
    second_fetch = client.get(f"/api/v1/attempts/{attempt_id}/question/{question_id}", headers=student_headers).json()
    assert [o["id"] for o in second_fetch["options"]] == shuffled_ids

    # Grading is by option id, looked up by the (shuffled) displayed text --
    # correctness must not depend on which position "5" ended up in.
    correct_option = next(o["id"] for o in first_fetch["options"] if o["text"] == "5")
    client.put(f"/api/v1/attempts/{attempt_id}/answer", json={
        "question_id": question_id, "selected_option_id": correct_option,
    }, headers=student_headers)
    submit = client.post(f"/api/v1/attempts/{attempt_id}/submit", headers=student_headers)
    assert submit.status_code == 200, submit.text
    assert submit.json()["scored_marks"] == 5


def test_randomize_options_off_keeps_natural_order(client, seed_roles, admin_token):
    examiner_headers = auth_headers(_create_examiner_and_login(client, admin_token))
    exam_id, natural_ids = _make_published_exam_with_many_options(client, examiner_headers, randomize_options=False)
    student_headers = auth_headers(_register_student_and_login(client))

    start = client.post(f"/api/v1/attempts/start/{exam_id}", headers=student_headers)
    assert start.status_code == 200, start.text
    attempt_id = start.json()["attempt_id"]
    question_id = start.json()["question_ids_in_order"][0]

    question = client.get(f"/api/v1/attempts/{attempt_id}/question/{question_id}", headers=student_headers).json()
    assert [o["id"] for o in question["options"]] == natural_ids


def test_examiners_own_view_of_options_is_never_shuffled(client, seed_roles, admin_token):
    """The per-attempt shuffle is a candidate-facing display concern only."""
    examiner_headers = auth_headers(_create_examiner_and_login(client, admin_token))
    exam_id, natural_ids = _make_published_exam_with_many_options(client, examiner_headers, randomize_options=True)
    student_headers = auth_headers(_register_student_and_login(client))
    client.post(f"/api/v1/attempts/start/{exam_id}", headers=student_headers)

    exam_detail = client.get(f"/api/v1/exams/{exam_id}", headers=examiner_headers).json()
    assert [o["id"] for o in exam_detail["sections"][0]["questions"][0]["options"]] == natural_ids
