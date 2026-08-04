"""PDF result report and the comprehensive JSON report endpoint."""
from tests.conftest import auth_headers
from tests.test_exam_workflow import _build_published_exam, _create_examiner_and_login, _register_student_and_login


def _complete_attempt(client, student_headers, exam_id):
    start = client.post(f"/api/v1/attempts/start/{exam_id}", headers=student_headers)
    assert start.status_code == 200, start.text
    attempt_id = start.json()["attempt_id"]
    submit = client.post(f"/api/v1/attempts/{attempt_id}/submit", headers=student_headers)
    assert submit.status_code == 200, submit.text
    return attempt_id


def test_student_can_download_result_pdf_for_own_attempt(client, seed_roles, admin_token):
    examiner_headers = auth_headers(_create_examiner_and_login(client, admin_token))
    exam_id = _build_published_exam(client, examiner_headers)
    student_headers = auth_headers(_register_student_and_login(client))
    attempt_id = _complete_attempt(client, student_headers, exam_id)

    response = client.get(f"/api/v1/attempts/{attempt_id}/result/pdf", headers=student_headers)
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/pdf"
    assert "attachment" in response.headers["content-disposition"]
    assert response.content[:4] == b"%PDF"


def test_student_can_fetch_comprehensive_report_for_own_attempt(client, seed_roles, admin_token):
    examiner_headers = auth_headers(_create_examiner_and_login(client, admin_token))
    exam_id = _build_published_exam(client, examiner_headers)
    student_headers = auth_headers(_register_student_and_login(client))
    attempt_id = _complete_attempt(client, student_headers, exam_id)

    response = client.get(f"/api/v1/attempts/{attempt_id}/report", headers=student_headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["attempt_id"] == attempt_id
    assert body["exam_title"]
    assert body["candidate_name"]
    assert "pass_percentage" in body and "passed" in body
    assert isinstance(body["questions"], list) and len(body["questions"]) > 0
    question = body["questions"][0]
    for field in ("question_id", "text", "marks", "marks_awarded", "outcome"):
        assert field in question


def test_certificate_route_no_longer_exists(client, seed_roles, admin_token):
    """The certificate feature was removed outright, not just hidden --
    confirms the route is gone rather than merely unauthorized."""
    examiner_headers = auth_headers(_create_examiner_and_login(client, admin_token))
    exam_id = _build_published_exam(client, examiner_headers)
    student_headers = auth_headers(_register_student_and_login(client))
    attempt_id = _complete_attempt(client, student_headers, exam_id)

    response = client.get(f"/api/v1/attempts/{attempt_id}/certificate/pdf", headers=student_headers)
    assert response.status_code == 404


def test_another_student_cannot_download_someone_elses_report(client, seed_roles, admin_token):
    examiner_headers = auth_headers(_create_examiner_and_login(client, admin_token))
    exam_id = _build_published_exam(client, examiner_headers)
    owner_headers = auth_headers(_register_student_and_login(client, email="owner@example.com"))
    attempt_id = _complete_attempt(client, owner_headers, exam_id)

    other_headers = auth_headers(_register_student_and_login(client, email="intruder@example.com"))
    response = client.get(f"/api/v1/attempts/{attempt_id}/result/pdf", headers=other_headers)
    assert response.status_code == 404


def test_examiner_who_owns_exam_can_download_student_result_pdf(client, seed_roles, admin_token):
    examiner_headers = auth_headers(_create_examiner_and_login(client, admin_token))
    exam_id = _build_published_exam(client, examiner_headers)
    student_headers = auth_headers(_register_student_and_login(client))
    attempt_id = _complete_attempt(client, student_headers, exam_id)

    response = client.get(f"/api/v1/attempts/{attempt_id}/result/pdf", headers=examiner_headers)
    assert response.status_code == 200, response.text
    assert response.content[:4] == b"%PDF"


def test_report_not_available_before_attempt_is_submitted(client, seed_roles, admin_token):
    examiner_headers = auth_headers(_create_examiner_and_login(client, admin_token))
    exam_id = _build_published_exam(client, examiner_headers)
    student_headers = auth_headers(_register_student_and_login(client))

    start = client.post(f"/api/v1/attempts/start/{exam_id}", headers=student_headers)
    attempt_id = start.json()["attempt_id"]

    response = client.get(f"/api/v1/attempts/{attempt_id}/report", headers=student_headers)
    assert response.status_code == 404
