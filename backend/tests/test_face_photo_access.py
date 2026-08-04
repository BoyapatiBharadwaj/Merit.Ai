"""
Authorization for GET /proctoring/face/photo/{student_id}.

This endpoint replaced the old `app.mount("/uploads", StaticFiles(...))` in
main.py, which served every file under UPLOAD_DIR -- including every
student's face photo -- to anyone on the network with no authentication at
all. These tests exist specifically to pin down who can and cannot fetch a
given student's photo now that it requires auth.
"""
import io

from PIL import Image

from app.models.user import User
from app.repositories import proctor_repository
from tests.conftest import auth_headers
from tests.test_exam_workflow import _build_published_exam, _create_examiner_and_login, _register_student_and_login


def _student_id(db_session, email: str) -> int:
    user = db_session.query(User).filter(User.email == email).first()
    return user.student_profile.id


def _write_face_profile(db_session, tmp_path, student_id: int) -> str:
    """Save a tiny real JPEG to disk and register it as this student's face
    profile, bypassing the real ArcFace pipeline entirely -- these tests are
    about the HTTP-layer authorization check, not face recognition itself."""
    image_path = tmp_path / f"student_{student_id}.jpg"
    Image.new("RGB", (16, 16), (200, 100, 50)).save(image_path, format="JPEG")
    proctor_repository.save_face_profile(db_session, student_id, str(image_path), "[]")
    return str(image_path)


def test_unauthenticated_request_is_rejected(client, seed_roles, db_session, tmp_path):
    _register_student_and_login(client, email="owner@example.com")
    owner_id = _student_id(db_session, "owner@example.com")
    _write_face_profile(db_session, tmp_path, owner_id)

    response = client.get(f"/api/v1/proctoring/face/photo/{owner_id}")
    assert response.status_code == 401


def test_a_different_student_cannot_view_another_students_photo(client, seed_roles, db_session, tmp_path):
    _register_student_and_login(client, email="owner2@example.com")
    owner_id = _student_id(db_session, "owner2@example.com")
    _write_face_profile(db_session, tmp_path, owner_id)

    stranger_token = _register_student_and_login(client, email="stranger@example.com")

    response = client.get(f"/api/v1/proctoring/face/photo/{owner_id}", headers=auth_headers(stranger_token))
    assert response.status_code == 403


def test_the_owning_student_can_view_their_own_photo(client, seed_roles, db_session, tmp_path):
    owner_token = _register_student_and_login(client, email="owner3@example.com")
    owner_id = _student_id(db_session, "owner3@example.com")
    _write_face_profile(db_session, tmp_path, owner_id)

    response = client.get(f"/api/v1/proctoring/face/photo/{owner_id}", headers=auth_headers(owner_token))
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"


def test_admin_can_view_any_students_photo(client, seed_roles, admin_token, db_session, tmp_path):
    _register_student_and_login(client, email="owner4@example.com")
    owner_id = _student_id(db_session, "owner4@example.com")
    _write_face_profile(db_session, tmp_path, owner_id)

    response = client.get(f"/api/v1/proctoring/face/photo/{owner_id}", headers=auth_headers(admin_token))
    assert response.status_code == 200


def test_examiner_can_view_a_photo_for_a_student_in_their_own_organization(client, seed_roles, admin_token, db_session, tmp_path):
    """Note the enrolment step. This test used to create an examiner and a
    completely unrelated student and assert 200 -- which passed only because
    *every* examiner counted as "staff" for *every* student's biometrics.
    That is now scoped to the examiner's own organization, so the student has
    to actually be theirs."""
    examiner_token = _create_examiner_and_login(client, admin_token, email="examiner-photo@example.com")
    _register_student_and_login(client, email="owner5@example.com")
    owner_id = _student_id(db_session, "owner5@example.com")
    _write_face_profile(db_session, tmp_path, owner_id)

    client.post("/api/v1/organizations/me/roster", json={"emails": ["owner5@example.com"]},
                headers=auth_headers(examiner_token))

    response = client.get(f"/api/v1/proctoring/face/photo/{owner_id}", headers=auth_headers(examiner_token))
    assert response.status_code == 200


def test_examiner_cannot_view_a_photo_for_a_student_outside_their_organization(client, seed_roles, admin_token, db_session, tmp_path):
    _register_student_and_login(client, email="owner6@example.com")
    owner_id = _student_id(db_session, "owner6@example.com")
    _write_face_profile(db_session, tmp_path, owner_id)

    outsider = _create_examiner_and_login(client, admin_token, email="outsider@example.com")

    response = client.get(f"/api/v1/proctoring/face/photo/{owner_id}", headers=auth_headers(outsider))
    assert response.status_code == 403


def test_examiner_can_view_a_photo_via_a_direct_exam_invite_outside_their_organization(client, seed_roles, admin_token, db_session, tmp_path):
    """The whole point of a per-exam invite (organization_service.
    can_student_access_exam) is that it grants access independently of
    organization membership. Identity access has to follow the same rule --
    an examiner proctoring an outside invitee's attempt must be able to
    confirm who they are, same as for any other participant on that exam.
    See organization_service.examiner_can_view_student."""
    examiner_token = _create_examiner_and_login(client, admin_token, email="examiner-invite@example.com")
    exam_id = _build_published_exam(client, auth_headers(examiner_token))

    # Genuinely unaffiliated -- never enrolled in any organization's roster.
    _register_student_and_login(client, email="outside-invitee@example.com", enrol=False)
    owner_id = _student_id(db_session, "outside-invitee@example.com")
    _write_face_profile(db_session, tmp_path, owner_id)

    add = client.post(f"/api/v1/organizations/exams/{exam_id}/participants",
                      json={"emails": ["outside-invitee@example.com"]},
                      headers=auth_headers(examiner_token))
    assert add.status_code == 201, add.text

    response = client.get(f"/api/v1/proctoring/face/photo/{owner_id}", headers=auth_headers(examiner_token))
    assert response.status_code == 200


def test_an_examiner_with_no_relationship_to_the_student_still_cannot_view_it(client, seed_roles, admin_token, db_session, tmp_path):
    """Companion to the invite test above: being *some* examiner is still not
    enough on its own. Never enrolling the student and never inviting them to
    any exam of this examiner's must still deny access."""
    examiner_token = _create_examiner_and_login(client, admin_token, email="examiner-unrelated@example.com")
    _build_published_exam(client, auth_headers(examiner_token))  # an exam this student is not on

    _register_student_and_login(client, email="unrelated-student@example.com", enrol=False)
    owner_id = _student_id(db_session, "unrelated-student@example.com")
    _write_face_profile(db_session, tmp_path, owner_id)

    response = client.get(f"/api/v1/proctoring/face/photo/{owner_id}", headers=auth_headers(examiner_token))
    assert response.status_code == 403


def test_missing_profile_is_a_clean_404_not_a_500(client, seed_roles, admin_token, db_session):
    """A student who never registered a face has no FaceProfile row at all --
    someone WITH access (admin here) hitting that case must get a clean 404,
    not an unhandled exception."""
    _register_student_and_login(client, email="noface@example.com")
    owner_id = _student_id(db_session, "noface@example.com")

    response = client.get(f"/api/v1/proctoring/face/photo/{owner_id}", headers=auth_headers(admin_token))
    assert response.status_code == 404
