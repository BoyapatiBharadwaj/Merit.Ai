"""
Consent, erasure and retention for biometric data.

The property under test throughout is the one that makes this safe to ship:
erasing biometrics must remove the ability to re-identify someone WITHOUT
touching their assessment record. Getting either half wrong is bad in a
different direction -- leaving the embedding behind defeats the deletion
request, and cascading into attempts destroys results that have to survive it.
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.core.config import settings
from app.models.face_profile import FaceProfile
from app.models.student import Student
from app.models.user import Role, User
from app.core.security import hash_password
from app.models.enums import RoleName
from app.services import biometric_service


def _student(db, email="bio@example.com"):
    role = db.query(Role).filter(Role.name == RoleName.STUDENT.value).first()
    user = User(email=email, hashed_password=hash_password("Ur5aMinor!Lab"), role_id=role.id)
    user.set_name("Bio", "Student")
    db.add(user)
    db.commit()
    student = Student(user_id=user.id)
    db.add(student)
    db.commit()
    return student


def _with_face(db, student, tmp_path, *, encoding="[0.1, 0.2]"):
    image = tmp_path / f"face_{student.id}.jpg"
    image.write_bytes(b"not-really-a-jpeg")
    profile = FaceProfile(student_id=student.id, image_path=str(image), encoding=encoding)
    db.add(profile)
    biometric_service.record_face_consent(profile)
    db.commit()
    return profile, image


# --- consent ------------------------------------------------------------------

def test_consent_is_stamped_with_the_version_in_force_at_capture(db_session, seed_roles, tmp_path):
    student = _student(db_session)
    profile, _ = _with_face(db_session, student, tmp_path)
    assert profile.consent_version == settings.BIOMETRIC_CONSENT_VERSION
    assert profile.consented_at is not None


def test_bumping_the_consent_version_marks_existing_consent_stale(db_session, seed_roles, tmp_path, monkeypatch):
    """The reason the version is stored per capture rather than assumed global:
    an institution changing the wording needs to see who has not agreed to it."""
    student = _student(db_session)
    _with_face(db_session, student, tmp_path)

    status = biometric_service.consent_status(db_session, student)
    assert status["face"]["stale"] is False

    monkeypatch.setattr(settings, "BIOMETRIC_CONSENT_VERSION", "2027-01-01.v2")
    status = biometric_service.consent_status(db_session, student)
    assert status["face"]["stale"] is True


# --- erasure ------------------------------------------------------------------

def test_erasure_removes_the_embedding_and_the_file(db_session, seed_roles, tmp_path):
    student = _student(db_session)
    profile, image = _with_face(db_session, student, tmp_path)
    student.id_card_image_path = str(tmp_path / "id.jpg")
    (tmp_path / "id.jpg").write_bytes(b"id-card")
    db_session.commit()

    removed = biometric_service.erase_student_biometrics(db_session, student, reason="test")

    db_session.refresh(profile)
    assert removed["embedding"] is True
    assert profile.encoding == "[]"
    assert profile.deleted_at is not None
    assert not image.exists()
    assert not (tmp_path / "id.jpg").exists()
    assert student.id_card_image_path is None


def test_erasure_keeps_the_row_as_an_audit_record(db_session, seed_roles, tmp_path):
    """Deleting the row would cascade into attempts and destroy assessment
    history -- and would lose the fact that an erasure ever happened."""
    student = _student(db_session)
    profile, _ = _with_face(db_session, student, tmp_path)
    profile_id = profile.id

    biometric_service.erase_student_biometrics(db_session, student, reason="student_request")

    still_there = db_session.query(FaceProfile).filter(FaceProfile.id == profile_id).first()
    assert still_there is not None
    assert still_there.deletion_reason == "student_request"


def test_erasure_unlocks_identity(db_session, seed_roles, tmp_path):
    """Leaving identity_locked set after erasing the data it was derived from
    would lock the student out of their own profile edits permanently."""
    student = _student(db_session)
    _with_face(db_session, student, tmp_path)
    student.identity_locked = True
    student.identity_locked_at = datetime.now(timezone.utc)
    db_session.commit()

    biometric_service.erase_student_biometrics(db_session, student, reason="test")
    assert student.identity_locked is False
    assert student.identity_locked_at is None


def test_erasure_is_idempotent(db_session, seed_roles, tmp_path):
    student = _student(db_session)
    _with_face(db_session, student, tmp_path)
    biometric_service.erase_student_biometrics(db_session, student, reason="first")
    second = biometric_service.erase_student_biometrics(db_session, student, reason="second")
    assert second["embedding"] is False


def test_erasure_survives_a_file_that_is_already_gone(db_session, seed_roles, tmp_path):
    """A stale path must not abort the request and leave the embedding in the
    database -- the database side is what actually prevents re-identification."""
    student = _student(db_session)
    profile, image = _with_face(db_session, student, tmp_path)
    image.unlink()

    removed = biometric_service.erase_student_biometrics(db_session, student, reason="test")
    db_session.refresh(profile)
    assert removed["embedding"] is True
    assert profile.encoding == "[]"


# --- retention ----------------------------------------------------------------

def test_nothing_is_purged_when_retention_is_disabled(db_session, seed_roles, tmp_path, monkeypatch):
    """The default. A retention job that deletes evidence nobody asked it to
    delete is worse than one that keeps too much."""
    monkeypatch.setattr(settings, "BIOMETRIC_RETENTION_DAYS", 0)
    student = _student(db_session)
    _with_face(db_session, student, tmp_path)
    student.created_at = datetime.now(timezone.utc) - timedelta(days=3650)
    db_session.commit()

    assert biometric_service.purge_expired(db_session) == 0


def test_a_long_dormant_student_is_purged_once_retention_is_configured(
    db_session, seed_roles, tmp_path, monkeypatch
):
    monkeypatch.setattr(settings, "BIOMETRIC_RETENTION_DAYS", 90)
    student = _student(db_session)
    profile, _ = _with_face(db_session, student, tmp_path)
    student.created_at = datetime.now(timezone.utc) - timedelta(days=200)
    db_session.commit()

    assert biometric_service.purge_expired(db_session) == 1
    db_session.refresh(profile)
    assert profile.deleted_at is not None


def test_a_recent_student_is_left_alone(db_session, seed_roles, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "BIOMETRIC_RETENTION_DAYS", 90)
    student = _student(db_session)
    profile, _ = _with_face(db_session, student, tmp_path)
    student.created_at = datetime.now(timezone.utc) - timedelta(days=10)
    db_session.commit()

    assert biometric_service.purge_expired(db_session) == 0
    db_session.refresh(profile)
    assert profile.deleted_at is None


def test_an_already_erased_profile_is_not_purged_again(db_session, seed_roles, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "BIOMETRIC_RETENTION_DAYS", 90)
    student = _student(db_session)
    _with_face(db_session, student, tmp_path)
    student.created_at = datetime.now(timezone.utc) - timedelta(days=200)
    db_session.commit()

    assert biometric_service.purge_expired(db_session) == 1
    assert biometric_service.purge_expired(db_session) == 0


# --- HTTP surface -------------------------------------------------------------

def test_a_student_can_see_and_erase_their_own_biometrics(client, seed_roles, db_session, tmp_path):
    registered = client.post("/api/v1/auth/register/student", json={
        "first_name": "Self", "last_name": "Serve", "email": "self@example.com",
        "password": "Ur5aMinor!Lab",
    })
    token = registered.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    student = db_session.query(Student).join(User).filter(User.email == "self@example.com").first()
    _with_face(db_session, student, tmp_path)

    status = client.get("/api/v1/users/me/biometrics", headers=headers)
    assert status.status_code == 200
    assert status.json()["face"]["captured"] is True

    erased = client.delete("/api/v1/users/me/biometrics", headers=headers)
    assert erased.status_code == 200
    assert erased.json()["erased"]["embedding"] is True

    assert client.get("/api/v1/users/me/biometrics", headers=headers).json()["face"]["captured"] is False


def test_a_student_cannot_erase_someone_elses_biometrics(client, seed_roles, db_session):
    victim = client.post("/api/v1/auth/register/student", json={
        "first_name": "Vic", "last_name": "Tim", "email": "victim@example.com",
        "password": "Ur5aMinor!Lab"})
    attacker = client.post("/api/v1/auth/register/student", json={
        "first_name": "At", "last_name": "Tacker", "email": "attacker@example.com",
        "password": "Ur5aMinor!Lab"})

    victim_user_id = victim.json()["user_id"]
    headers = {"Authorization": f"Bearer {attacker.json()['access_token']}"}
    response = client.delete(f"/api/v1/users/{victim_user_id}/biometrics", headers=headers)
    assert response.status_code == 403


def test_an_admin_can_erase_on_a_students_behalf(client, seed_roles, admin_token, db_session, tmp_path):
    registered = client.post("/api/v1/auth/register/student", json={
        "first_name": "By", "last_name": "Admin", "email": "byadmin@example.com",
        "password": "Ur5aMinor!Lab"})
    user_id = registered.json()["user_id"]
    student = db_session.query(Student).filter(Student.user_id == user_id).first()
    _with_face(db_session, student, tmp_path)

    response = client.delete(f"/api/v1/users/{user_id}/biometrics",
                             headers={"Authorization": f"Bearer {admin_token}"})
    assert response.status_code == 200
    assert response.json()["erased"]["embedding"] is True
