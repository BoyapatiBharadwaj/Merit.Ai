"""End-to-end exam workflow: create -> publish -> attempt -> answer -> submit -> result."""
import pytest

from tests.conftest import auth_headers


def _create_examiner_and_login(client, admin_token, email="examiner@example.com",
                               organization_name="Acme Institute"):
    """Create an examiner and return a token it can act with."""
    import re
    from urllib.parse import unquote

    from app.services import email_service

    password = "Sup3rSecret!123"

    sent = []

    def _record(*, to, subject, text_body, html_body=None):
        sent.append({"to": to, "text": text_body})
        return True

    original_send, original_queue = email_service.send, email_service.queue
    email_service.send = _record
    email_service.queue = lambda background, **kw: _record(**kw) and None
    try:
        created = client.post(
            "/api/v1/auth/examiners",
            json={"first_name": "Test", "last_name": "Examiner", "email": email,
                  "organization_name": organization_name},
            headers=auth_headers(admin_token),
        )
    finally:
        email_service.send, email_service.queue = original_send, original_queue

    if created.status_code == 400:
        # Several call sites in this suite reuse the same default email within one test to get
        # "an examiner" more than once.
        login = client.post("/api/v1/auth/login", json={"email": email, "password": password})
        assert login.status_code == 200, login.text
        return login.json()["access_token"]

    assert created.status_code == 201, created.text
    message = next(m for m in reversed(sent) if m["to"] == email)
    match = re.search(r"[?&]token=([A-Za-z0-9_\-%]+)", message["text"])
    assert match, message["text"]
    token = unquote(match.group(1))

    activated = client.post("/api/v1/auth/activate",
                            json={"email": email, "token": token, "password": password})
    assert activated.status_code == 200, activated.text
    return activated.json()["access_token"]


def enrol_email(email: str, organization_id: int | None = None) -> None:
    """Put an email on an organization's roster, as an examiner would."""
    from app.database.session import SessionLocal
    from app.models.organization import Organization, OrganizationMember

    session = SessionLocal()
    try:
        if organization_id is None:
            org_ids = [o.id for o in session.query(Organization).all()]
        else:
            org_ids = [organization_id]
        for org_id in org_ids:
            already = (session.query(OrganizationMember)
                       .filter(OrganizationMember.organization_id == org_id,
                               OrganizationMember.email == email.lower())
                       .first())
            if not already:
                session.add(OrganizationMember(organization_id=org_id, email=email.lower()))
        session.commit()
    finally:
        session.close()


def _register_student_and_login(client, email="student@example.com", enrol=True,
                                organization_id=None):
    # Enrolment happens *before* registration, mirroring the real flow.
    if enrol:
        enrol_email(email, organization_id)
    # Consent is sent because a real client sends it.
    response = client.post("/api/v1/auth/register/student", json={
        "first_name": "Test", "last_name": "Student", "email": email, "password": "Sup3rSecret!",
        "accepted_terms": True, "accepted_proctoring": True, "terms_version": "2026-08-05",
    })
    assert response.status_code == 201
    return response.json()["access_token"]


def _build_published_exam(client, examiner_headers, proctoring_enabled=False):
    exam = client.post("/api/v1/exams", json={
        "title": "Intro Quiz", "description": "basic", "duration_minutes": 30,
        "randomize_questions": False, "proctoring_enabled": proctoring_enabled,
    }, headers=examiner_headers).json()

    section = client.post(f"/api/v1/exams/{exam['id']}/sections", json={
        "title": "Section 1", "order_index": 0,
    }, headers=examiner_headers).json()

    client.post(f"/api/v1/exams/sections/{section['id']}/questions", json={
        "text": "2 + 2 = ?", "marks": 5, "order_index": 0,
        "options": [
            {"text": "3", "is_correct": False},
            {"text": "4", "is_correct": True},
        ],
    }, headers=examiner_headers)

    publish = client.post(f"/api/v1/exams/{exam['id']}/publish", headers=examiner_headers)
    assert publish.status_code == 200, publish.text
    return exam["id"]


def test_full_exam_workflow_scores_correctly(client, seed_roles, admin_token):
    examiner_token = _create_examiner_and_login(client, admin_token)
    examiner_headers = auth_headers(examiner_token)
    exam_id = _build_published_exam(client, examiner_headers)

    student_token = _register_student_and_login(client)
    student_headers = auth_headers(student_token)

    start = client.post(f"/api/v1/attempts/start/{exam_id}", headers=student_headers)
    assert start.status_code == 200, start.text
    attempt = start.json()
    assert attempt["proctoring_enabled"] is False
    question_id = attempt["question_ids_in_order"][0]

    question = client.get(f"/api/v1/attempts/{attempt['attempt_id']}/question/{question_id}", headers=student_headers).json()
    correct_option = next(o["id"] for o in question["options"] if o["text"] == "4")

    save = client.put(f"/api/v1/attempts/{attempt['attempt_id']}/answer", json={
        "question_id": question_id, "selected_option_id": correct_option,
    }, headers=student_headers)
    assert save.status_code == 200

    submit = client.post(f"/api/v1/attempts/{attempt['attempt_id']}/submit", headers=student_headers)
    assert submit.status_code == 200, submit.text
    result = submit.json()
    assert result["scored_marks"] == 5
    assert result["total_marks"] == 5
    assert result["percentage"] == 100.0

    my_attempts = client.get("/api/v1/attempts/my", headers=student_headers)
    assert my_attempts.status_code == 200
    assert my_attempts.json()[0]["exam_id"] == exam_id


def test_cannot_publish_exam_with_no_questions(client, seed_roles, admin_token):
    examiner_headers = auth_headers(_create_examiner_and_login(client, admin_token))
    exam = client.post("/api/v1/exams", json={
        "title": "Empty Exam", "duration_minutes": 10,
    }, headers=examiner_headers).json()

    response = client.post(f"/api/v1/exams/{exam['id']}/publish", headers=examiner_headers)
    assert response.status_code == 400


def test_reorder_questions_persists_new_order(client, seed_roles, admin_token):
    examiner_headers = auth_headers(_create_examiner_and_login(client, admin_token))
    exam = client.post("/api/v1/exams", json={"title": "Reorder Exam", "duration_minutes": 10}, headers=examiner_headers).json()
    section = client.post(f"/api/v1/exams/{exam['id']}/sections", json={"title": "S", "order_index": 0}, headers=examiner_headers).json()

    q1 = client.post(f"/api/v1/exams/sections/{section['id']}/questions", json={
        "text": "Q1", "marks": 1, "order_index": 0,
        "options": [{"text": "A", "is_correct": True}, {"text": "B", "is_correct": False}],
    }, headers=examiner_headers).json()
    q2 = client.post(f"/api/v1/exams/sections/{section['id']}/questions", json={
        "text": "Q2", "marks": 1, "order_index": 1,
        "options": [{"text": "A", "is_correct": True}, {"text": "B", "is_correct": False}],
    }, headers=examiner_headers).json()

    reorder = client.put(f"/api/v1/exams/sections/{section['id']}/questions/reorder", json={
        "question_ids": [q2["id"], q1["id"]],
    }, headers=examiner_headers)
    assert reorder.status_code == 200, reorder.text

    detail = client.get(f"/api/v1/exams/{exam['id']}", headers=examiner_headers).json()
    ordered_ids = [q["id"] for q in detail["sections"][0]["questions"]]
    assert ordered_ids == [q2["id"], q1["id"]]


def test_reorder_rejects_mismatched_question_set(client, seed_roles, admin_token):
    examiner_headers = auth_headers(_create_examiner_and_login(client, admin_token))
    exam = client.post("/api/v1/exams", json={"title": "Reorder Exam 2", "duration_minutes": 10}, headers=examiner_headers).json()
    section = client.post(f"/api/v1/exams/{exam['id']}/sections", json={"title": "S", "order_index": 0}, headers=examiner_headers).json()
    client.post(f"/api/v1/exams/sections/{section['id']}/questions", json={
        "text": "Q1", "marks": 1, "order_index": 0,
        "options": [{"text": "A", "is_correct": True}, {"text": "B", "is_correct": False}],
    }, headers=examiner_headers)

    response = client.put(f"/api/v1/exams/sections/{section['id']}/questions/reorder", json={
        "question_ids": [999],
    }, headers=examiner_headers)
    assert response.status_code == 400


def test_examiner_cannot_edit_another_examiners_exam(client, seed_roles, admin_token):
    owner_headers = auth_headers(_create_examiner_and_login(client, admin_token, email="owner@example.com"))
    other_headers = auth_headers(_create_examiner_and_login(client, admin_token, email="other@example.com"))

    exam = client.post("/api/v1/exams", json={
        "title": "Private Exam", "duration_minutes": 10,
    }, headers=owner_headers).json()

    response = client.post(f"/api/v1/exams/{exam['id']}/sections", json={
        "title": "Section 1", "order_index": 0,
    }, headers=other_headers)
    assert response.status_code == 403


def test_section_can_be_renamed_while_the_exam_is_a_draft(client, seed_roles, admin_token):
    examiner_headers = auth_headers(_create_examiner_and_login(client, admin_token))
    exam = client.post("/api/v1/exams", json={"title": "Renamable", "duration_minutes": 10},
                       headers=examiner_headers).json()
    section = client.post(f"/api/v1/exams/{exam['id']}/sections", json={"title": "Typo Setcion", "order_index": 0},
                          headers=examiner_headers).json()

    renamed = client.patch(f"/api/v1/exams/sections/{section['id']}", json={"title": "Section A"},
                           headers=examiner_headers)
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["title"] == "Section A"

    detail = client.get(f"/api/v1/exams/{exam['id']}", headers=examiner_headers).json()
    assert detail["sections"][0]["title"] == "Section A"


def test_section_rename_is_refused_once_the_exam_is_published(client, seed_roles, admin_token):
    """Same freeze rule as questions/options: a section title is part of the
    paper a candidate sees, so it must not change under a live attempt."""
    examiner_headers = auth_headers(_create_examiner_and_login(client, admin_token))
    exam_id = _build_published_exam(client, examiner_headers)
    detail = client.get(f"/api/v1/exams/{exam_id}", headers=examiner_headers).json()
    section_id = detail["sections"][0]["id"]

    response = client.patch(f"/api/v1/exams/sections/{section_id}", json={"title": "Too late"},
                            headers=examiner_headers)
    assert response.status_code == 400
    assert "published" in response.json()["detail"].lower()


def test_section_rename_is_refused_for_an_examiner_who_does_not_own_the_exam(client, seed_roles, admin_token):
    owner_headers = auth_headers(_create_examiner_and_login(client, admin_token, email="sec-owner@example.com"))
    other_headers = auth_headers(_create_examiner_and_login(client, admin_token, email="sec-other@example.com"))
    exam = client.post("/api/v1/exams", json={"title": "Owned", "duration_minutes": 10},
                       headers=owner_headers).json()
    section = client.post(f"/api/v1/exams/{exam['id']}/sections", json={"title": "Mine", "order_index": 0},
                          headers=owner_headers).json()

    response = client.patch(f"/api/v1/exams/sections/{section['id']}", json={"title": "Hijacked"},
                            headers=other_headers)
    assert response.status_code == 403


def test_proctored_exam_requires_identity_verification_before_attempt(client, seed_roles, admin_token):
    """A proctored exam now requires BOTH face registration and ID-card
    verification. 403 rather than 400: the request is well-formed, the caller
    just isn't permitted to start this exam yet."""
    examiner_headers = auth_headers(_create_examiner_and_login(client, admin_token))
    exam_id = _build_published_exam(client, examiner_headers, proctoring_enabled=True)

    student_headers = auth_headers(_register_student_and_login(client))
    response = client.post(f"/api/v1/attempts/start/{exam_id}", headers=student_headers)
    assert response.status_code == 403
    detail = response.json()["detail"].lower()
    # The message must name both outstanding steps so the UI can act on it.
    assert "register your face" in detail
    assert "verify your id card" in detail


def test_unproctored_exam_does_not_require_identity_verification(client, seed_roles, admin_token):
    """The identity gate is scoped to proctored exams only -- an unverified
    student must still be able to sit a normal, unproctored exam."""
    examiner_headers = auth_headers(_create_examiner_and_login(client, admin_token))
    exam_id = _build_published_exam(client, examiner_headers, proctoring_enabled=False)

    student_headers = auth_headers(_register_student_and_login(client))
    response = client.post(f"/api/v1/attempts/start/{exam_id}", headers=student_headers)
    assert response.status_code == 200, response.text


def test_student_cannot_attempt_same_exam_twice(client, seed_roles, admin_token):
    examiner_headers = auth_headers(_create_examiner_and_login(client, admin_token))
    exam_id = _build_published_exam(client, examiner_headers)
    student_headers = auth_headers(_register_student_and_login(client))

    first = client.post(f"/api/v1/attempts/start/{exam_id}", headers=student_headers)
    assert first.status_code == 200
    client.post(f"/api/v1/attempts/{first.json()['attempt_id']}/submit", headers=student_headers)

    second = client.post(f"/api/v1/attempts/start/{exam_id}", headers=student_headers)
    assert second.status_code == 400
