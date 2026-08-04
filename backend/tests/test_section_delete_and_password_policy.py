"""
Two policy changes, both enforced server-side rather than by hiding UI:

  * Examiners can delete a section (and everything in it) while the exam is a
    draft.
  * STUDENT passwords are administrator-managed -- a candidate account is an
    institutional identity issued for a sitting. Examiners and admins manage
    their own. A forgotten student password is still self-recoverable by
    emailed code, so this is central administration, not a lockout.
"""
import pytest

from tests.conftest import auth_headers
from tests.test_exam_workflow import _create_examiner_and_login, _register_student_and_login


def _exam_with_two_sections(client, examiner_token):
    exam = client.post("/api/v1/exams", json={
        "title": "Sectioned", "duration_minutes": 60, "proctoring_enabled": False,
    }, headers=auth_headers(examiner_token)).json()

    sections = []
    for title in ("First", "Second"):
        section = client.post(f"/api/v1/exams/{exam['id']}/sections", json={"title": title},
                              headers=auth_headers(examiner_token)).json()
        client.post(f"/api/v1/exams/sections/{section['id']}/questions", json={
            "text": f"Q in {title}", "marks": 1, "question_type": "mcq",
            "options": [{"text": "A", "is_correct": True}, {"text": "B", "is_correct": False}],
        }, headers=auth_headers(examiner_token))
        sections.append(section)
    return exam, sections


# --- deleting a section -------------------------------------------------------

def test_examiner_can_delete_a_draft_section_and_its_questions(client, seed_roles, admin_token):
    examiner_token = _create_examiner_and_login(client, admin_token)
    exam, sections = _exam_with_two_sections(client, examiner_token)

    response = client.delete(f"/api/v1/exams/sections/{sections[0]['id']}",
                             headers=auth_headers(examiner_token))
    assert response.status_code == 200, response.text
    assert response.json()["questions_deleted"] == 1
    assert response.json()["title"] == "First"

    detail = client.get(f"/api/v1/exams/{exam['id']}", headers=auth_headers(examiner_token)).json()
    assert [s["title"] for s in detail["sections"]] == ["Second"]


def test_the_last_section_cannot_be_deleted(client, seed_roles, admin_token):
    """Otherwise the examiner is left with an exam that cannot be published and
    whose only exit is deleting the exam -- which should be a deliberate,
    separately-labelled action, not something a section delete backs them into."""
    examiner_token = _create_examiner_and_login(client, admin_token)
    exam, sections = _exam_with_two_sections(client, examiner_token)

    client.delete(f"/api/v1/exams/sections/{sections[0]['id']}", headers=auth_headers(examiner_token))
    response = client.delete(f"/api/v1/exams/sections/{sections[1]['id']}",
                             headers=auth_headers(examiner_token))
    assert response.status_code == 400
    assert "at least one section" in response.json()["detail"]


def test_a_published_exams_section_cannot_be_deleted(client, seed_roles, admin_token):
    """A candidate may be mid-attempt against this exact paper; removing a
    section would invalidate their question_order and their answers with it."""
    examiner_token = _create_examiner_and_login(client, admin_token)
    exam, sections = _exam_with_two_sections(client, examiner_token)
    client.post(f"/api/v1/exams/{exam['id']}/publish", headers=auth_headers(examiner_token))

    response = client.delete(f"/api/v1/exams/sections/{sections[0]['id']}",
                             headers=auth_headers(examiner_token))
    assert response.status_code in (400, 403)


def test_an_examiner_cannot_delete_another_examiners_section(client, seed_roles, admin_token):
    owner_token = _create_examiner_and_login(client, admin_token, email="owner@example.com")
    other_token = _create_examiner_and_login(client, admin_token, email="other@example.com")
    _, sections = _exam_with_two_sections(client, owner_token)

    response = client.delete(f"/api/v1/exams/sections/{sections[0]['id']}",
                             headers=auth_headers(other_token))
    assert response.status_code in (403, 404)


def test_a_student_cannot_delete_a_section(client, seed_roles, admin_token):
    examiner_token = _create_examiner_and_login(client, admin_token)
    _, sections = _exam_with_two_sections(client, examiner_token)
    student_token = _register_student_and_login(client)

    response = client.delete(f"/api/v1/exams/sections/{sections[0]['id']}",
                             headers=auth_headers(student_token))
    assert response.status_code == 403


# --- password policy ----------------------------------------------------------

def test_a_student_cannot_change_their_own_password(client, seed_roles):
    token = _register_student_and_login(client)
    response = client.post("/api/v1/users/me/password", json={
        "current_password": "Str0ngPass!", "new_password": "SomethingElse123",
    }, headers=auth_headers(token))

    assert response.status_code == 403
    # The refusal must point somewhere useful, or it reads as a dead end.
    assert "forgot password" in response.json()["detail"].lower()


def test_an_examiner_can_change_their_own_password(client, seed_roles, admin_token):
    """Examiners are staff running the platform, so they manage their own
    credential. Students are the one role that stays administrator-managed --
    a candidate account is an institutional identity issued for a sitting."""
    token = _create_examiner_and_login(client, admin_token)
    response = client.post("/api/v1/users/me/password", json={
        "current_password": "Sup3rSecret!", "new_password": "SomethingElse123",
    }, headers=auth_headers(token))
    assert response.status_code == 200, response.text

    assert client.post("/api/v1/auth/login", json={
        "email": "examiner@example.com", "password": "SomethingElse123"}).status_code == 200


def test_an_admin_can_still_change_their_own_password(client, seed_roles, admin_token):
    response = client.post("/api/v1/users/me/password", json={
        "current_password": "Admin@12345", "new_password": "NewAdminPass123",
    }, headers=auth_headers(admin_token))
    assert response.status_code == 200

    assert client.post("/api/v1/auth/login", json={
        "email": "admin@example.com", "password": "NewAdminPass123"}).status_code == 200


def test_an_admin_can_still_reset_a_students_password(client, seed_roles, admin_token, db_session):
    """The counterpart of the restriction: if students cannot change their own,
    the admin path they are being pointed at had better work."""
    _register_student_and_login(client, email="managed@example.com")
    students = client.get("/api/v1/users/students", headers=auth_headers(admin_token)).json()
    target = next(s for s in students if s["email"] == "managed@example.com")

    response = client.post(f"/api/v1/users/{target['user_id']}/reset-password",
                           json={"new_password": "AdminSetPass123"},
                           headers=auth_headers(admin_token))
    assert response.status_code == 200
    assert client.post("/api/v1/auth/login", json={
        "email": "managed@example.com", "password": "AdminSetPass123"}).status_code == 200


def test_a_student_can_still_recover_by_email(client, seed_roles, db_session, monkeypatch):
    """Central administration, not a lockout -- the OTP path stays open so a
    locked-out candidate does not have to find an administrator before an exam."""
    from app.core.config import settings
    from app.models.otp import OtpPurpose
    from app.repositories import otp_repository
    from app.services import email_service, otp_service

    monkeypatch.setattr(email_service, "is_enabled", lambda: True)
    monkeypatch.setattr(email_service, "send", lambda **kw: True)
    monkeypatch.setattr(email_service, "queue", lambda background, **kw: None)

    _register_student_and_login(client, email="recover@example.com")
    otp_service.request_code(db_session, email="recover@example.com", purpose=OtpPurpose.PASSWORD_RESET)
    row = otp_repository.get_latest(db_session, email="recover@example.com",
                                    purpose=OtpPurpose.PASSWORD_RESET)
    code = next(c for c in (str(i).zfill(settings.OTP_LENGTH) for i in range(10 ** settings.OTP_LENGTH))
                if otp_service._digest(c) == row.code_hash)

    response = client.post("/api/v1/auth/password-reset/confirm", json={
        "email": "recover@example.com", "code": code, "new_password": "RecoveredPass123",
    })
    assert response.status_code == 200
    assert client.post("/api/v1/auth/login", json={
        "email": "recover@example.com", "password": "RecoveredPass123"}).status_code == 200
