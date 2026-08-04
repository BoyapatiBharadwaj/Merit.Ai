"""The multi_select question type: "select all that apply" grading (exact
set match, all-or-nothing -- see attempt_service._grade_multi_select_answer),
authoring validation, and end-to-end attempt/report flow."""
from tests.conftest import auth_headers
from tests.test_exam_workflow import _create_examiner_and_login, _register_student_and_login


def _build_multi_select_exam(client, examiner_headers, *, marks=10):
    exam = client.post("/api/v1/exams", json={
        "title": "Multi-Select Quiz", "duration_minutes": 30,
        "randomize_questions": False, "proctoring_enabled": False,
    }, headers=examiner_headers).json()
    section = client.post(f"/api/v1/exams/{exam['id']}/sections", json={
        "title": "Section 1", "order_index": 0,
    }, headers=examiner_headers).json()
    question = client.post(f"/api/v1/exams/sections/{section['id']}/questions", json={
        "text": "Which are prime numbers?", "marks": marks, "order_index": 0,
        "question_type": "multi_select",
        "options": [
            {"text": "2", "is_correct": True},
            {"text": "3", "is_correct": True},
            {"text": "4", "is_correct": False},
            {"text": "9", "is_correct": False},
        ],
    }, headers=examiner_headers)
    assert question.status_code == 201, question.text
    publish = client.post(f"/api/v1/exams/{exam['id']}/publish", headers=examiner_headers)
    assert publish.status_code == 200, publish.text
    return exam["id"], question.json()


def test_creating_a_multi_select_question_requires_at_least_one_correct_option(client, seed_roles, admin_token):
    examiner_headers = auth_headers(_create_examiner_and_login(client, admin_token))
    exam = client.post("/api/v1/exams", json={"title": "Empty Options Exam", "duration_minutes": 10}, headers=examiner_headers).json()
    section = client.post(f"/api/v1/exams/{exam['id']}/sections", json={"title": "S", "order_index": 0}, headers=examiner_headers).json()

    response = client.post(f"/api/v1/exams/sections/{section['id']}/questions", json={
        "text": "Pick some", "marks": 5, "order_index": 0, "question_type": "multi_select",
        "options": [{"text": "a", "is_correct": False}, {"text": "b", "is_correct": False}],
    }, headers=examiner_headers)
    assert response.status_code == 400


def test_multi_select_allows_more_than_one_correct_option(client, seed_roles, admin_token):
    examiner_headers = auth_headers(_create_examiner_and_login(client, admin_token))
    exam_id, question = _build_multi_select_exam(client, examiner_headers)
    assert question["question_type"] == "multi_select"
    assert sum(1 for o in question["options"] if o["is_correct"]) == 2


def test_selecting_exactly_the_correct_set_earns_full_marks(client, seed_roles, admin_token):
    examiner_headers = auth_headers(_create_examiner_and_login(client, admin_token))
    exam_id, question = _build_multi_select_exam(client, examiner_headers, marks=10)
    student_headers = auth_headers(_register_student_and_login(client))

    start = client.post(f"/api/v1/attempts/start/{exam_id}", headers=student_headers).json()
    attempt_id = start["attempt_id"]
    question_id = question["id"]
    correct_ids = [o["id"] for o in question["options"] if o["is_correct"]]

    save = client.put(f"/api/v1/attempts/{attempt_id}/multi-answer", json={
        "question_id": question_id, "selected_option_ids": correct_ids,
    }, headers=student_headers)
    assert save.status_code == 200, save.text

    submit = client.post(f"/api/v1/attempts/{attempt_id}/submit", headers=student_headers)
    result = submit.json()
    assert result["scored_marks"] == 10
    assert result["correct_count"] == 1


def test_selecting_a_partial_or_extra_set_earns_zero_not_partial_credit(client, seed_roles, admin_token):
    examiner_headers = auth_headers(_create_examiner_and_login(client, admin_token))
    exam_id, question = _build_multi_select_exam(client, examiner_headers, marks=10)
    student_headers = auth_headers(_register_student_and_login(client))

    start = client.post(f"/api/v1/attempts/start/{exam_id}", headers=student_headers).json()
    attempt_id = start["attempt_id"]
    question_id = question["id"]
    correct_ids = [o["id"] for o in question["options"] if o["is_correct"]]
    # Correct set plus one wrong extra option -- must score zero, not partial.
    wrong_id = next(o["id"] for o in question["options"] if not o["is_correct"])

    client.put(f"/api/v1/attempts/{attempt_id}/multi-answer", json={
        "question_id": question_id, "selected_option_ids": correct_ids + [wrong_id],
    }, headers=student_headers)

    submit = client.post(f"/api/v1/attempts/{attempt_id}/submit", headers=student_headers)
    result = submit.json()
    assert result["scored_marks"] == 0
    assert result["incorrect_count"] == 1


def test_unattempted_multi_select_question_is_unattempted_not_incorrect(client, seed_roles, admin_token):
    examiner_headers = auth_headers(_create_examiner_and_login(client, admin_token))
    exam_id, question = _build_multi_select_exam(client, examiner_headers)
    student_headers = auth_headers(_register_student_and_login(client))

    start = client.post(f"/api/v1/attempts/start/{exam_id}", headers=student_headers).json()
    submit = client.post(f"/api/v1/attempts/{start['attempt_id']}/submit", headers=student_headers)
    result = submit.json()
    assert result["scored_marks"] == 0
    assert result["unattempted_count"] == 1
    assert result["incorrect_count"] == 0


def test_multi_select_report_includes_selected_and_correct_option_ids(client, seed_roles, admin_token):
    examiner_headers = auth_headers(_create_examiner_and_login(client, admin_token))
    exam_id, question = _build_multi_select_exam(client, examiner_headers)
    student_headers = auth_headers(_register_student_and_login(client))

    start = client.post(f"/api/v1/attempts/start/{exam_id}", headers=student_headers).json()
    attempt_id = start["attempt_id"]
    question_id = question["id"]
    correct_ids = sorted(o["id"] for o in question["options"] if o["is_correct"])

    client.put(f"/api/v1/attempts/{attempt_id}/multi-answer", json={
        "question_id": question_id, "selected_option_ids": correct_ids,
    }, headers=student_headers)
    client.post(f"/api/v1/attempts/{attempt_id}/submit", headers=student_headers)

    report = client.get(f"/api/v1/attempts/{attempt_id}/report", headers=student_headers)
    assert report.status_code == 200, report.text
    q_report = next(q for q in report.json()["questions"] if q["question_id"] == question_id)
    assert q_report["question_type"] == "multi_select"
    assert sorted(q_report["selected_option_ids"]) == correct_ids
    assert sorted(q_report["correct_option_ids"]) == correct_ids
    assert q_report["outcome"] == "correct"


def test_saving_a_multi_answer_on_a_non_multi_select_question_is_rejected(client, seed_roles, admin_token):
    from tests.test_exam_workflow import _build_published_exam

    examiner_headers = auth_headers(_create_examiner_and_login(client, admin_token))
    exam_id = _build_published_exam(client, examiner_headers)
    student_headers = auth_headers(_register_student_and_login(client))

    start = client.post(f"/api/v1/attempts/start/{exam_id}", headers=student_headers).json()
    question_id = start["question_ids_in_order"][0]

    response = client.put(f"/api/v1/attempts/{start['attempt_id']}/multi-answer", json={
        "question_id": question_id, "selected_option_ids": [],
    }, headers=student_headers)
    assert response.status_code == 400
