"""Auto-save / exam-recovery: resuming an in-progress attempt after a
reload or network drop should return the same attempt (not a fresh one)
and expose every previously-saved answer in one call."""
from tests.conftest import auth_headers
from tests.test_exam_workflow import _create_examiner_and_login, _register_student_and_login


def _build_two_question_exam(client, examiner_headers):
    exam = client.post("/api/v1/exams", json={
        "title": "Recovery Quiz", "duration_minutes": 30,
        "randomize_questions": False, "proctoring_enabled": False,
    }, headers=examiner_headers).json()

    section = client.post(f"/api/v1/exams/{exam['id']}/sections", json={
        "title": "Section 1", "order_index": 0,
    }, headers=examiner_headers).json()

    client.post(f"/api/v1/exams/sections/{section['id']}/questions", json={
        "text": "1 + 1 = ?", "marks": 5, "order_index": 0,
        "options": [{"text": "2", "is_correct": True}, {"text": "3", "is_correct": False}],
    }, headers=examiner_headers)
    client.post(f"/api/v1/exams/sections/{section['id']}/questions", json={
        "text": "2 + 2 = ?", "marks": 5, "order_index": 1,
        "options": [{"text": "4", "is_correct": True}, {"text": "5", "is_correct": False}],
    }, headers=examiner_headers)

    publish = client.post(f"/api/v1/exams/{exam['id']}/publish", headers=examiner_headers)
    assert publish.status_code == 200, publish.text
    return exam["id"]


def test_restarting_an_in_progress_attempt_resumes_instead_of_erroring(client, seed_roles, admin_token):
    examiner_headers = auth_headers(_create_examiner_and_login(client, admin_token))
    exam_id = _build_two_question_exam(client, examiner_headers)
    student_headers = auth_headers(_register_student_and_login(client))

    first = client.post(f"/api/v1/attempts/start/{exam_id}", headers=student_headers)
    assert first.status_code == 200, first.text
    attempt_id = first.json()["attempt_id"]

    # Simulate a browser crash / network drop mid-exam, then reload: the
    # student re-opens the exam page, which calls "start" again.
    second = client.post(f"/api/v1/attempts/start/{exam_id}", headers=student_headers)
    assert second.status_code == 200, second.text
    assert second.json()["attempt_id"] == attempt_id
    assert second.json()["question_ids_in_order"] == first.json()["question_ids_in_order"]


def test_answers_endpoint_returns_full_map_for_recovery(client, seed_roles, admin_token):
    examiner_headers = auth_headers(_create_examiner_and_login(client, admin_token))
    exam_id = _build_two_question_exam(client, examiner_headers)
    student_headers = auth_headers(_register_student_and_login(client))

    start = client.post(f"/api/v1/attempts/start/{exam_id}", headers=student_headers)
    attempt_id = start.json()["attempt_id"]
    question_ids = start.json()["question_ids_in_order"]

    question = client.get(f"/api/v1/attempts/{attempt_id}/question/{question_ids[0]}", headers=student_headers).json()
    option_id = question["options"][0]["id"]
    save = client.put(f"/api/v1/attempts/{attempt_id}/answer", json={
        "question_id": question_ids[0], "selected_option_id": option_id,
    }, headers=student_headers)
    assert save.status_code == 200

    # Recovery: a single call should reveal which questions are already answered.
    answers = client.get(f"/api/v1/attempts/{attempt_id}/answers", headers=student_headers)
    assert answers.status_code == 200, answers.text
    body = answers.json()
    assert body[str(question_ids[0])] == option_id
    assert str(question_ids[1]) not in body


def test_answers_endpoint_rejects_another_students_attempt(client, seed_roles, admin_token):
    examiner_headers = auth_headers(_create_examiner_and_login(client, admin_token))
    exam_id = _build_two_question_exam(client, examiner_headers)
    owner_headers = auth_headers(_register_student_and_login(client, email="owner2@example.com"))
    attempt_id = client.post(f"/api/v1/attempts/start/{exam_id}", headers=owner_headers).json()["attempt_id"]

    intruder_headers = auth_headers(_register_student_and_login(client, email="intruder2@example.com"))
    response = client.get(f"/api/v1/attempts/{attempt_id}/answers", headers=intruder_headers)
    assert response.status_code == 404
