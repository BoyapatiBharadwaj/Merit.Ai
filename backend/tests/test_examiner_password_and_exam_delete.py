"""Two independent policy changes, tested together since both are small
authorization checks layered on existing endpoints:

  1. Examiners cannot change their own password (POST /users/me/password) --
     their credentials are admin-issued and admin-reset only.
  2. An examiner can delete their own DRAFT exams (DELETE /exams/{id}), but
     never a published one, and never another examiner's exam.
"""
from tests.conftest import auth_headers
from tests.test_exam_workflow import _build_published_exam, _create_examiner_and_login


def test_examiner_can_change_own_password(client, seed_roles, admin_token):
    """Policy change: examiners manage their own credential again.

    They are staff running the platform, not candidates sitting an exam --
    students remain the one administrator-managed role. Previously asserted the
    opposite; rewritten rather than deleted so the endpoint's role rule stays
    under test either way.
    """
    examiner_headers = auth_headers(_create_examiner_and_login(client, admin_token))

    response = client.post("/api/v1/users/me/password", json={
        "current_password": "Sup3rSecret!", "new_password": "NewPassw0rd!",
    }, headers=examiner_headers)
    assert response.status_code == 200, response.text

    # The change must be real, not just a 200 -- the old password must stop
    # working and the new one must start.
    assert client.post("/api/v1/auth/login", json={
        "email": "examiner@example.com", "password": "Sup3rSecret!"}).status_code == 401
    assert client.post("/api/v1/auth/login", json={
        "email": "examiner@example.com", "password": "NewPassw0rd!"}).status_code == 200


def test_student_cannot_change_own_password(client, seed_roles, admin_token):
    """Policy change: passwords are administrator-managed for BOTH students and
    examiners now, not just examiners.

    This test previously asserted the opposite. It is rewritten rather than
    deleted because the behaviour it guards is still worth pinning -- only the
    expected answer changed, and a silently-dropped test would leave nothing
    watching this endpoint's role rule.
    """
    from tests.test_exam_workflow import _register_student_and_login

    student_headers = auth_headers(_register_student_and_login(client))
    response = client.post("/api/v1/users/me/password", json={
        "current_password": "Sup3rSecret!", "new_password": "NewPassw0rd!",
    }, headers=student_headers)
    assert response.status_code == 403
    # Must point at the self-service route, or the refusal is a dead end.
    assert "forgot password" in response.json()["detail"].lower()


def test_examiner_can_delete_a_draft_exam(client, seed_roles, admin_token):
    examiner_headers = auth_headers(_create_examiner_and_login(client, admin_token))
    exam = client.post("/api/v1/exams", json={"title": "Scratch Exam", "duration_minutes": 10}, headers=examiner_headers).json()

    response = client.delete(f"/api/v1/exams/{exam['id']}", headers=examiner_headers)
    assert response.status_code == 204

    # Gone -- not just hidden.
    fetch = client.get(f"/api/v1/exams/{exam['id']}", headers=examiner_headers)
    assert fetch.status_code == 404
    my_exams = client.get("/api/v1/exams/my", headers=examiner_headers).json()
    assert all(e["id"] != exam["id"] for e in my_exams)


def test_cannot_delete_a_published_exam(client, seed_roles, admin_token):
    examiner_headers = auth_headers(_create_examiner_and_login(client, admin_token))
    exam_id = _build_published_exam(client, examiner_headers)

    response = client.delete(f"/api/v1/exams/{exam_id}", headers=examiner_headers)
    assert response.status_code == 400

    # Still there afterwards.
    fetch = client.get(f"/api/v1/exams/{exam_id}", headers=examiner_headers)
    assert fetch.status_code == 200


def test_cannot_delete_another_examiners_draft_exam(client, seed_roles, admin_token):
    owner_headers = auth_headers(_create_examiner_and_login(client, admin_token, email="owner@example.com"))
    other_headers = auth_headers(_create_examiner_and_login(client, admin_token, email="other@example.com"))
    exam = client.post("/api/v1/exams", json={"title": "Not Yours", "duration_minutes": 10}, headers=owner_headers).json()

    response = client.delete(f"/api/v1/exams/{exam['id']}", headers=other_headers)
    assert response.status_code == 403

    fetch = client.get(f"/api/v1/exams/{exam['id']}", headers=owner_headers)
    assert fetch.status_code == 200
