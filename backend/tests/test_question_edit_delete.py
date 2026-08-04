"""
Per-question edit and delete in the exam builder
(PUT/DELETE /exams/questions/{question_id}).

Both actions reuse the same "editable exam" guard as question creation and
reordering (exam_service._get_editable_exam): only the owning examiner can
act, and only while the exam is still a draft. That draft-only restriction
is also what makes delete unconditionally safe here -- an exam can't have
any attempts or saved answers until it's published, so a question reachable
by these endpoints can never have real student data attached to it yet.
"""
from tests.conftest import auth_headers
from tests.test_exam_workflow import _create_examiner_and_login


def _create_exam_with_section(client, examiner_headers, title="Edit Test Exam"):
    exam = client.post("/api/v1/exams", json={
        "title": title, "duration_minutes": 30, "randomize_questions": False, "proctoring_enabled": False,
    }, headers=examiner_headers).json()
    section = client.post(f"/api/v1/exams/{exam['id']}/sections", json={
        "title": "Section 1", "order_index": 0,
    }, headers=examiner_headers).json()
    return exam, section


def _create_mcq_question(client, examiner_headers, section_id, text="Original question?", marks=5):
    response = client.post(f"/api/v1/exams/sections/{section_id}/questions", json={
        "text": text, "marks": marks, "order_index": 0, "question_type": "mcq",
        "options": [
            {"text": "A", "is_correct": True},
            {"text": "B", "is_correct": False},
        ],
    }, headers=examiner_headers)
    assert response.status_code == 201, response.text
    return response.json()


def _create_coding_question(client, examiner_headers, section_id, text="Sum two numbers"):
    response = client.post(f"/api/v1/exams/sections/{section_id}/questions", json={
        "text": text, "marks": 10, "order_index": 0, "question_type": "coding",
        "language": "python", "starter_code": "print(1)",
        "time_limit_seconds": 5,
        "test_cases": [{"input": "1 2", "expected_output": "3", "is_sample": True}],
    }, headers=examiner_headers)
    assert response.status_code == 201, response.text
    return response.json()


def test_update_mcq_question_changes_text_marks_and_options(client, seed_roles, admin_token):
    examiner_headers = auth_headers(_create_examiner_and_login(client, admin_token))
    exam, section = _create_exam_with_section(client, examiner_headers)
    question = _create_mcq_question(client, examiner_headers, section["id"])

    update = client.put(f"/api/v1/exams/questions/{question['id']}", json={
        "text": "Updated question?", "marks": 9, "order_index": 0, "question_type": "mcq",
        "options": [
            {"text": "X", "is_correct": False},
            {"text": "Y", "is_correct": True},
            {"text": "Z", "is_correct": False},
        ],
    }, headers=examiner_headers)
    assert update.status_code == 200, update.text
    body = update.json()
    assert body["text"] == "Updated question?"
    assert body["marks"] == 9
    assert sorted(o["text"] for o in body["options"]) == ["X", "Y", "Z"]
    assert sum(1 for o in body["options"] if o["is_correct"]) == 1

    detail = client.get(f"/api/v1/exams/{exam['id']}", headers=examiner_headers).json()
    reloaded = detail["sections"][0]["questions"][0]
    assert reloaded["text"] == "Updated question?"
    assert len(reloaded["options"]) == 3


def test_update_rejects_mcq_without_exactly_one_correct_option(client, seed_roles, admin_token):
    examiner_headers = auth_headers(_create_examiner_and_login(client, admin_token))
    _, section = _create_exam_with_section(client, examiner_headers)
    question = _create_mcq_question(client, examiner_headers, section["id"])

    response = client.put(f"/api/v1/exams/questions/{question['id']}", json={
        "text": "Bad update", "marks": 5, "order_index": 0, "question_type": "mcq",
        "options": [{"text": "A", "is_correct": False}, {"text": "B", "is_correct": False}],
    }, headers=examiner_headers)
    assert response.status_code == 400


def test_update_coding_question_changes_language_and_test_cases(client, seed_roles, admin_token):
    examiner_headers = auth_headers(_create_examiner_and_login(client, admin_token))
    _, section = _create_exam_with_section(client, examiner_headers)
    question = _create_coding_question(client, examiner_headers, section["id"])

    update = client.put(f"/api/v1/exams/questions/{question['id']}", json={
        "text": "Sum two numbers (updated)", "marks": 15, "order_index": 0, "question_type": "coding",
        "language": "javascript", "starter_code": "console.log(1)",
        "time_limit_seconds": 8,
        "test_cases": [
            {"input": "1 2", "expected_output": "3", "is_sample": True},
            {"input": "5 5", "expected_output": "10", "is_sample": False},
        ],
    }, headers=examiner_headers)
    assert update.status_code == 200, update.text
    body = update.json()
    assert body["language"] == "javascript"
    assert body["time_limit_seconds"] == 8
    assert len(body["test_cases"]) == 2


def test_switching_mcq_to_coding_on_edit_clears_old_options(client, seed_roles, admin_token):
    examiner_headers = auth_headers(_create_examiner_and_login(client, admin_token))
    _, section = _create_exam_with_section(client, examiner_headers)
    question = _create_mcq_question(client, examiner_headers, section["id"])

    update = client.put(f"/api/v1/exams/questions/{question['id']}", json={
        "text": "Now a coding question", "marks": 10, "order_index": 0, "question_type": "coding",
        "language": "python", "starter_code": "",
        "time_limit_seconds": 5,
        "test_cases": [{"input": "", "expected_output": "ok", "is_sample": True}],
    }, headers=examiner_headers)
    assert update.status_code == 200, update.text
    body = update.json()
    assert body["question_type"] == "coding"
    assert body["options"] == []


def test_switching_coding_to_mcq_on_edit_clears_old_coding_fields(client, seed_roles, admin_token):
    examiner_headers = auth_headers(_create_examiner_and_login(client, admin_token))
    _, section = _create_exam_with_section(client, examiner_headers)
    question = _create_coding_question(client, examiner_headers, section["id"])

    update = client.put(f"/api/v1/exams/questions/{question['id']}", json={
        "text": "Now an MCQ", "marks": 5, "order_index": 0, "question_type": "mcq",
        "options": [{"text": "A", "is_correct": True}, {"text": "B", "is_correct": False}],
    }, headers=examiner_headers)
    assert update.status_code == 200, update.text
    body = update.json()
    assert body["question_type"] == "mcq"
    assert body["language"] is None
    assert body["test_cases"] == []
    assert len(body["options"]) == 2


def test_update_rejects_once_exam_is_published(client, seed_roles, admin_token):
    examiner_headers = auth_headers(_create_examiner_and_login(client, admin_token))
    exam, section = _create_exam_with_section(client, examiner_headers)
    question = _create_mcq_question(client, examiner_headers, section["id"])
    publish = client.post(f"/api/v1/exams/{exam['id']}/publish", headers=examiner_headers)
    assert publish.status_code == 200, publish.text

    response = client.put(f"/api/v1/exams/questions/{question['id']}", json={
        "text": "Should not be allowed", "marks": 5, "order_index": 0, "question_type": "mcq",
        "options": [{"text": "A", "is_correct": True}, {"text": "B", "is_correct": False}],
    }, headers=examiner_headers)
    assert response.status_code == 400


def test_update_rejects_for_a_non_owning_examiner(client, seed_roles, admin_token):
    owner_headers = auth_headers(_create_examiner_and_login(client, admin_token, email="owner-edit@example.com"))
    other_headers = auth_headers(_create_examiner_and_login(client, admin_token, email="other-edit@example.com"))
    _, section = _create_exam_with_section(client, owner_headers)
    question = _create_mcq_question(client, owner_headers, section["id"])

    response = client.put(f"/api/v1/exams/questions/{question['id']}", json={
        "text": "Hijacked", "marks": 5, "order_index": 0, "question_type": "mcq",
        "options": [{"text": "A", "is_correct": True}, {"text": "B", "is_correct": False}],
    }, headers=other_headers)
    assert response.status_code == 403


def test_update_404_for_nonexistent_question(client, seed_roles, admin_token):
    examiner_headers = auth_headers(_create_examiner_and_login(client, admin_token))
    response = client.put("/api/v1/exams/questions/999999", json={
        "text": "Ghost", "marks": 5, "order_index": 0, "question_type": "mcq",
        "options": [{"text": "A", "is_correct": True}, {"text": "B", "is_correct": False}],
    }, headers=examiner_headers)
    assert response.status_code == 404


def test_delete_question_removes_it_from_the_section(client, seed_roles, admin_token):
    examiner_headers = auth_headers(_create_examiner_and_login(client, admin_token))
    exam, section = _create_exam_with_section(client, examiner_headers)
    keep = _create_mcq_question(client, examiner_headers, section["id"], text="Keep me")
    doomed = _create_mcq_question(client, examiner_headers, section["id"], text="Delete me")

    response = client.delete(f"/api/v1/exams/questions/{doomed['id']}", headers=examiner_headers)
    assert response.status_code == 204

    detail = client.get(f"/api/v1/exams/{exam['id']}", headers=examiner_headers).json()
    remaining = detail["sections"][0]["questions"]
    assert len(remaining) == 1
    assert remaining[0]["id"] == keep["id"]


def test_delete_rejects_once_exam_is_published(client, seed_roles, admin_token):
    examiner_headers = auth_headers(_create_examiner_and_login(client, admin_token))
    exam, section = _create_exam_with_section(client, examiner_headers)
    question = _create_mcq_question(client, examiner_headers, section["id"])
    publish = client.post(f"/api/v1/exams/{exam['id']}/publish", headers=examiner_headers)
    assert publish.status_code == 200, publish.text

    response = client.delete(f"/api/v1/exams/questions/{question['id']}", headers=examiner_headers)
    assert response.status_code == 400


def test_delete_rejects_for_a_non_owning_examiner(client, seed_roles, admin_token):
    owner_headers = auth_headers(_create_examiner_and_login(client, admin_token, email="owner-delete@example.com"))
    other_headers = auth_headers(_create_examiner_and_login(client, admin_token, email="other-delete@example.com"))
    _, section = _create_exam_with_section(client, owner_headers)
    question = _create_mcq_question(client, owner_headers, section["id"])

    response = client.delete(f"/api/v1/exams/questions/{question['id']}", headers=other_headers)
    assert response.status_code == 403


def test_delete_404_for_nonexistent_question(client, seed_roles, admin_token):
    examiner_headers = auth_headers(_create_examiner_and_login(client, admin_token))
    response = client.delete("/api/v1/exams/questions/999999", headers=examiner_headers)
    assert response.status_code == 404


def test_reorder_still_works_after_deleting_a_question(client, seed_roles, admin_token):
    examiner_headers = auth_headers(_create_examiner_and_login(client, admin_token))
    exam, section = _create_exam_with_section(client, examiner_headers)
    q1 = _create_mcq_question(client, examiner_headers, section["id"], text="Q1")
    q2 = _create_mcq_question(client, examiner_headers, section["id"], text="Q2")
    q3 = _create_mcq_question(client, examiner_headers, section["id"], text="Q3")

    delete = client.delete(f"/api/v1/exams/questions/{q2['id']}", headers=examiner_headers)
    assert delete.status_code == 204

    reorder = client.put(f"/api/v1/exams/sections/{section['id']}/questions/reorder", json={
        "question_ids": [q3["id"], q1["id"]],
    }, headers=examiner_headers)
    assert reorder.status_code == 200, reorder.text

    detail = client.get(f"/api/v1/exams/{exam['id']}", headers=examiner_headers).json()
    ordered_ids = [q["id"] for q in detail["sections"][0]["questions"]]
    assert ordered_ids == [q3["id"], q1["id"]]
