"""Coding-question authoring, autosave, sample "Run", and Docker-sandboxed
grading at submit time. Real Docker isn't available in most test
environments, so grading tests monkeypatch
app.services.code_runner_service.run_against_test_cases to simulate a real
sandboxed run's shape -- the graceful-degradation path (Docker missing) is
covered separately by forcing code_runner_service.is_available() False and
letting the real run_against_test_cases() handle it, rather than assuming the
ambient runner has no Docker daemon. That assumption doesn't hold everywhere:
GitHub Actions' hosted ubuntu-latest runners ship a working Docker Engine
(it's what the postgres service container in the migrations job runs on), so
this test failed there with available=True until it stopped depending on
runner reality."""
from tests.conftest import auth_headers
from tests.test_exam_workflow import _create_examiner_and_login, _register_student_and_login
from app.services import code_runner_service


def _create_coding_exam(client, examiner_headers, test_cases=None, marks=10):
    exam = client.post("/api/v1/exams", json={
        "title": "Coding Round", "duration_minutes": 30,
        "randomize_questions": False, "proctoring_enabled": False,
    }, headers=examiner_headers).json()
    section = client.post(f"/api/v1/exams/{exam['id']}/sections", json={
        "title": "Section 1", "order_index": 0,
    }, headers=examiner_headers).json()

    payload = {
        "text": "Read two integers and print their sum.",
        "marks": marks, "order_index": 0, "question_type": "coding",
        "language": "python",
        "starter_code": "a, b = map(int, input().split())\nprint(a + b)\n",
        "time_limit_seconds": 5,
        "test_cases": test_cases if test_cases is not None else [
            {"input": "1 2", "expected_output": "3", "is_sample": True},
            {"input": "10 20", "expected_output": "30", "is_sample": False},
        ],
    }
    question = client.post(f"/api/v1/exams/sections/{section['id']}/questions", json=payload, headers=examiner_headers)
    assert question.status_code == 201, question.text

    publish = client.post(f"/api/v1/exams/{exam['id']}/publish", headers=examiner_headers)
    assert publish.status_code == 200, publish.text
    return exam["id"], question.json()["id"]


def test_create_coding_question_requires_sample_test_case(client, seed_roles, admin_token):
    examiner_headers = auth_headers(_create_examiner_and_login(client, admin_token))
    exam = client.post("/api/v1/exams", json={"title": "Bad Question Exam", "duration_minutes": 10}, headers=examiner_headers).json()
    section = client.post(f"/api/v1/exams/{exam['id']}/sections", json={"title": "S", "order_index": 0}, headers=examiner_headers).json()

    response = client.post(f"/api/v1/exams/sections/{section['id']}/questions", json={
        "text": "no samples", "marks": 5, "order_index": 0, "question_type": "coding",
        "language": "python", "test_cases": [{"input": "", "expected_output": "1", "is_sample": False}],
    }, headers=examiner_headers)
    assert response.status_code == 422  # pydantic validator rejects: no sample test case


def test_create_coding_question_rejects_bad_language(client, seed_roles, admin_token):
    examiner_headers = auth_headers(_create_examiner_and_login(client, admin_token))
    exam = client.post("/api/v1/exams", json={"title": "Bad Question Exam", "duration_minutes": 10}, headers=examiner_headers).json()
    section = client.post(f"/api/v1/exams/{exam['id']}/sections", json={"title": "S", "order_index": 0}, headers=examiner_headers).json()

    response = client.post(f"/api/v1/exams/sections/{section['id']}/questions", json={
        "text": "bad lang", "marks": 5, "order_index": 0, "question_type": "coding",
        "language": "ruby", "test_cases": [{"input": "", "expected_output": "1", "is_sample": True}],
    }, headers=examiner_headers)
    assert response.status_code == 422


def test_student_sees_only_sample_test_cases(client, seed_roles, admin_token):
    examiner_headers = auth_headers(_create_examiner_and_login(client, admin_token))
    exam_id, question_id = _create_coding_exam(client, examiner_headers)
    student_headers = auth_headers(_register_student_and_login(client))

    start = client.post(f"/api/v1/attempts/start/{exam_id}", headers=student_headers)
    attempt_id = start.json()["attempt_id"]
    view = client.get(f"/api/v1/attempts/{attempt_id}/question/{question_id}", headers=student_headers)
    assert view.status_code == 200, view.text
    body = view.json()
    assert body["question_type"] == "coding"
    assert body["language"] == "python"
    assert len(body["sample_test_cases"]) == 1  # the hidden case (10 20 -> 30) must not leak
    assert body["sample_test_cases"][0]["expected_output"] == "3"
    assert body["source_code"].startswith("a, b = map")  # starter code shown before any save
    assert body["has_saved_submission"] is False


def test_code_autosave_persists_and_shows_in_recovery_map(client, seed_roles, admin_token):
    examiner_headers = auth_headers(_create_examiner_and_login(client, admin_token))
    exam_id, question_id = _create_coding_exam(client, examiner_headers)
    student_headers = auth_headers(_register_student_and_login(client))

    attempt_id = client.post(f"/api/v1/attempts/start/{exam_id}", headers=student_headers).json()["attempt_id"]
    save = client.put(f"/api/v1/attempts/{attempt_id}/code-answer", json={
        "question_id": question_id, "source_code": "print('hello')",
    }, headers=student_headers)
    assert save.status_code == 200, save.text

    reloaded = client.get(f"/api/v1/attempts/{attempt_id}/question/{question_id}", headers=student_headers)
    assert reloaded.json()["source_code"] == "print('hello')"

    answers_map = client.get(f"/api/v1/attempts/{attempt_id}/answers", headers=student_headers)
    assert answers_map.json()[str(question_id)] is True


def test_run_sample_reports_unavailable_without_docker(client, seed_roles, admin_token, monkeypatch):
    """Forces code_runner_service.is_available() False -- rather than relying
    on the ambient runner actually lacking Docker -- so this deterministically
    exercises the real, un-mocked run_against_test_cases() and its
    graceful-degradation branch on every machine this runs on, including CI
    providers that (unlike most) do ship a working Docker daemon."""
    monkeypatch.setattr(code_runner_service, "is_available", lambda: False)
    examiner_headers = auth_headers(_create_examiner_and_login(client, admin_token))
    exam_id, question_id = _create_coding_exam(client, examiner_headers)
    student_headers = auth_headers(_register_student_and_login(client))
    attempt_id = client.post(f"/api/v1/attempts/start/{exam_id}", headers=student_headers).json()["attempt_id"]

    run = client.post(f"/api/v1/attempts/{attempt_id}/code-answer/run", json={
        "question_id": question_id, "source_code": "print('anything')",
    }, headers=student_headers)
    assert run.status_code == 200, run.text
    body = run.json()
    assert body["available"] is False
    assert "docker" in body["message"].lower() or "unavailable" in body["message"].lower()


def test_submit_grades_coding_question_with_mocked_runner(client, seed_roles, admin_token, monkeypatch):
    examiner_headers = auth_headers(_create_examiner_and_login(client, admin_token))
    exam_id, question_id = _create_coding_exam(client, examiner_headers, marks=10)
    student_headers = auth_headers(_register_student_and_login(client))
    attempt_id = client.post(f"/api/v1/attempts/start/{exam_id}", headers=student_headers).json()["attempt_id"]

    client.put(f"/api/v1/attempts/{attempt_id}/code-answer", json={
        "question_id": question_id, "source_code": "a, b = map(int, input().split())\nprint(a + b)\n",
    }, headers=student_headers)

    # Simulate a sandboxed run where 1 of 2 test cases passes (proportional credit).
    def fake_run(language, source_code, test_cases, time_limit_seconds=None):
        results = [
            {"passed": True, "is_sample": True, "input": "1 2", "expected_output": "3", "actual_output": "3", "error": None, "time_ms": 12},
            {"passed": False, "is_sample": False, "input": "10 20", "expected_output": "30", "actual_output": "29", "error": None, "time_ms": 14},
        ]
        return {"available": True, "results": results, "all_passed": False, "message": None}

    monkeypatch.setattr("app.services.attempt_service.code_runner_service.run_against_test_cases", fake_run)

    submit = client.post(f"/api/v1/attempts/{attempt_id}/submit", headers=student_headers)
    assert submit.status_code == 200, submit.text
    result = submit.json()
    assert result["scored_marks"] == 5  # 1/2 test cases * 10 marks, rounded
    assert result["total_marks"] == 10
    assert result["incorrect_count"] == 1  # not all test cases passed
    assert result["correct_count"] == 0


def test_submit_gives_full_marks_when_all_coding_tests_pass(client, seed_roles, admin_token, monkeypatch):
    examiner_headers = auth_headers(_create_examiner_and_login(client, admin_token))
    exam_id, question_id = _create_coding_exam(client, examiner_headers, marks=10)
    student_headers = auth_headers(_register_student_and_login(client))
    attempt_id = client.post(f"/api/v1/attempts/start/{exam_id}", headers=student_headers).json()["attempt_id"]

    client.put(f"/api/v1/attempts/{attempt_id}/code-answer", json={
        "question_id": question_id, "source_code": "a, b = map(int, input().split())\nprint(a + b)\n",
    }, headers=student_headers)

    def fake_run(language, source_code, test_cases, time_limit_seconds=None):
        results = [{"passed": True, "is_sample": case.get("is_sample"), "input": case.get("input"), "expected_output": case.get("expected_output"), "actual_output": case.get("expected_output"), "error": None, "time_ms": 5} for case in test_cases]
        return {"available": True, "results": results, "all_passed": True, "message": None}

    monkeypatch.setattr("app.services.attempt_service.code_runner_service.run_against_test_cases", fake_run)

    submit = client.post(f"/api/v1/attempts/{attempt_id}/submit", headers=student_headers)
    assert submit.status_code == 200, submit.text
    result = submit.json()
    assert result["scored_marks"] == 10
    assert result["correct_count"] == 1
    assert result["unattempted_count"] == 0


def test_submit_marks_unattempted_when_no_code_saved(client, seed_roles, admin_token):
    examiner_headers = auth_headers(_create_examiner_and_login(client, admin_token))
    exam_id, question_id = _create_coding_exam(client, examiner_headers, marks=10)
    student_headers = auth_headers(_register_student_and_login(client))
    client.post(f"/api/v1/attempts/start/{exam_id}", headers=student_headers)

    submit = client.post(f"/api/v1/attempts/{list(client.get('/api/v1/attempts/my', headers=student_headers).json())[0]['attempt_id']}/submit", headers=student_headers)
    assert submit.status_code == 200, submit.text
    result = submit.json()
    assert result["scored_marks"] == 0
    assert result["unattempted_count"] == 1


def test_mcq_answer_endpoint_rejects_coding_question(client, seed_roles, admin_token):
    examiner_headers = auth_headers(_create_examiner_and_login(client, admin_token))
    exam_id, question_id = _create_coding_exam(client, examiner_headers)
    student_headers = auth_headers(_register_student_and_login(client))
    attempt_id = client.post(f"/api/v1/attempts/start/{exam_id}", headers=student_headers).json()["attempt_id"]

    response = client.put(f"/api/v1/attempts/{attempt_id}/answer", json={
        "question_id": question_id, "selected_option_id": 1,
    }, headers=student_headers)
    assert response.status_code == 400
