"""
Authorization for GET /proctoring/id-card/photo/{student_id}.

Mirrors test_face_photo_access.py exactly -- this endpoint shares the same
_can_view_student_identity gate in app/api/v1/proctoring.py (self, admin, or
an examiner connected via organization_service.examiner_can_view_student),
so it needs the same coverage. The image itself is written directly onto
Student.id_card_image_path rather than going through the real OCR pipeline
(POST /proctoring/id-card/verify) -- these tests are about the HTTP-layer
authorization check, not ID-card OCR.
"""
import base64
import io

from PIL import Image

from app.ai import ocr_service
from app.models.student import Student
from app.models.user import User
from tests.conftest import auth_headers
from tests.test_exam_workflow import _build_published_exam, _create_examiner_and_login, _register_student_and_login


def _student_id(db_session, email: str) -> int:
    user = db_session.query(User).filter(User.email == email).first()
    return user.student_profile.id


def _b64_png(size=(16, 16), color=(90, 90, 90)):
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


def _write_id_card_image(db_session, tmp_path, student_id: int) -> str:
    """Save a tiny real JPEG to disk and point this student's
    id_card_image_path at it, bypassing OCR entirely."""
    image_path = tmp_path / f"idcard_{student_id}.jpg"
    Image.new("RGB", (16, 16), (30, 60, 90)).save(image_path, format="JPEG")
    student = db_session.query(Student).filter(Student.id == student_id).first()
    student.id_card_image_path = str(image_path)
    db_session.commit()
    return str(image_path)


def test_unauthenticated_request_is_rejected(client, seed_roles, db_session, tmp_path):
    _register_student_and_login(client, email="idowner@example.com")
    owner_id = _student_id(db_session, "idowner@example.com")
    _write_id_card_image(db_session, tmp_path, owner_id)

    response = client.get(f"/api/v1/proctoring/id-card/photo/{owner_id}")
    assert response.status_code == 401


def test_a_different_student_cannot_view_another_students_id_card(client, seed_roles, db_session, tmp_path):
    _register_student_and_login(client, email="idowner2@example.com")
    owner_id = _student_id(db_session, "idowner2@example.com")
    _write_id_card_image(db_session, tmp_path, owner_id)

    stranger_token = _register_student_and_login(client, email="idstranger@example.com")

    response = client.get(f"/api/v1/proctoring/id-card/photo/{owner_id}", headers=auth_headers(stranger_token))
    assert response.status_code == 403


def test_the_owning_student_can_view_their_own_id_card(client, seed_roles, db_session, tmp_path):
    owner_token = _register_student_and_login(client, email="idowner3@example.com")
    owner_id = _student_id(db_session, "idowner3@example.com")
    _write_id_card_image(db_session, tmp_path, owner_id)

    response = client.get(f"/api/v1/proctoring/id-card/photo/{owner_id}", headers=auth_headers(owner_token))
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"


def test_admin_can_view_any_students_id_card(client, seed_roles, admin_token, db_session, tmp_path):
    _register_student_and_login(client, email="idowner4@example.com")
    owner_id = _student_id(db_session, "idowner4@example.com")
    _write_id_card_image(db_session, tmp_path, owner_id)

    response = client.get(f"/api/v1/proctoring/id-card/photo/{owner_id}", headers=auth_headers(admin_token))
    assert response.status_code == 200


def test_examiner_can_view_an_id_card_for_a_student_in_their_own_organization(client, seed_roles, admin_token, db_session, tmp_path):
    examiner_token = _create_examiner_and_login(client, admin_token, email="examiner-idphoto@example.com")
    _register_student_and_login(client, email="idowner5@example.com")
    owner_id = _student_id(db_session, "idowner5@example.com")
    _write_id_card_image(db_session, tmp_path, owner_id)

    client.post("/api/v1/organizations/me/roster", json={"emails": ["idowner5@example.com"]},
                headers=auth_headers(examiner_token))

    response = client.get(f"/api/v1/proctoring/id-card/photo/{owner_id}", headers=auth_headers(examiner_token))
    assert response.status_code == 200


def test_examiner_cannot_view_an_id_card_for_a_student_outside_their_organization(client, seed_roles, admin_token, db_session, tmp_path):
    _register_student_and_login(client, email="idowner6@example.com")
    owner_id = _student_id(db_session, "idowner6@example.com")
    _write_id_card_image(db_session, tmp_path, owner_id)

    outsider = _create_examiner_and_login(client, admin_token, email="id-outsider@example.com")

    response = client.get(f"/api/v1/proctoring/id-card/photo/{owner_id}", headers=auth_headers(outsider))
    assert response.status_code == 403


def test_examiner_can_view_an_id_card_via_a_direct_exam_invite_outside_their_organization(client, seed_roles, admin_token, db_session, tmp_path):
    """Same broadened rule as the face-photo endpoint: a per-exam invite is
    enough on its own, independent of organization membership. See
    organization_service.examiner_can_view_student."""
    examiner_token = _create_examiner_and_login(client, admin_token, email="examiner-id-invite@example.com")
    exam_id = _build_published_exam(client, auth_headers(examiner_token))

    _register_student_and_login(client, email="id-outside-invitee@example.com", enrol=False)
    owner_id = _student_id(db_session, "id-outside-invitee@example.com")
    _write_id_card_image(db_session, tmp_path, owner_id)

    add = client.post(f"/api/v1/organizations/exams/{exam_id}/participants",
                      json={"emails": ["id-outside-invitee@example.com"]},
                      headers=auth_headers(examiner_token))
    assert add.status_code == 201, add.text

    response = client.get(f"/api/v1/proctoring/id-card/photo/{owner_id}", headers=auth_headers(examiner_token))
    assert response.status_code == 200


def test_verifying_an_id_card_saves_a_retrievable_photo_even_on_a_name_mismatch(client, seed_roles, monkeypatch, db_session):
    """End-to-end wiring check: POST /id-card/verify -> proctor_service.
    verify_id_card -> identity_service.record_id_verification should leave a
    real file behind that GET /id-card/photo/{id} can then serve -- not just
    the authorization gate the tests above cover.

    Mismatch is the deliberate case here, not a match: the photo is kept
    regardless of whether OCR matched (see record_id_verification), because a
    student who keeps failing verification is exactly who staff need to see
    the actual submitted photo for, not just a "name didn't match" message.
    """
    monkeypatch.setattr(ocr_service, "extract_text", lambda image: "COMPLETELY UNRELATED TEXT")
    token = _register_student_and_login(client, email="mismatched-id@example.com")
    headers = auth_headers(token)

    verify = client.post("/api/v1/proctoring/id-card/verify",
                         json={"image_base64": _b64_png()}, headers=headers)
    assert verify.status_code == 200, verify.text
    assert verify.json()["name_matched"] is False

    owner_id = _student_id(db_session, "mismatched-id@example.com")
    photo = client.get(f"/api/v1/proctoring/id-card/photo/{owner_id}", headers=headers)
    assert photo.status_code == 200
    assert photo.headers["content-type"] == "image/jpeg"


def test_missing_id_card_is_a_clean_404_not_a_500(client, seed_roles, admin_token, db_session):
    """A student who never verified an ID has no id_card_image_path at
    all -- someone WITH access (admin here) hitting that case must get a
    clean 404, not an unhandled exception."""
    _register_student_and_login(client, email="noidcard@example.com")
    owner_id = _student_id(db_session, "noidcard@example.com")

    response = client.get(f"/api/v1/proctoring/id-card/photo/{owner_id}", headers=auth_headers(admin_token))
    assert response.status_code == 404
