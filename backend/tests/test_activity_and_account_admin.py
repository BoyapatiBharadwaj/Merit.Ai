"""
The account activity trail, and the admin's delete/reset powers over accounts.

The trail's whole value is that it is complete and honest, so these tests check
the properties that make it worth trusting: it records who acted (not just what
happened), it survives the account being deleted, and it never contains a
secret.
"""
import pytest

from tests.conftest import auth_headers
from tests.test_exam_workflow import _create_examiner_and_login, _register_student_and_login


def _activity(client, admin_token, user_id):
    response = client.get(f"/api/v1/admin/users/{user_id}/activity", headers=auth_headers(admin_token))
    assert response.status_code == 200, response.text
    return response.json()


def _types(entries):
    return [e["type"] for e in entries]


# --- what gets recorded -------------------------------------------------------

def test_signing_up_and_signing_in_are_recorded(client, seed_roles, admin_token):
    token = _register_student_and_login(client, email="tracked@example.com")
    me = client.get("/api/v1/users/me", headers=auth_headers(token)).json()

    # Registration returns a session directly, so a real sign-in is a separate
    # call -- and must produce its own entry.
    assert client.post("/api/v1/auth/login", json={
        "email": "tracked@example.com", "password": "Sup3rSecret!"}).status_code == 200

    entries = _activity(client, admin_token, me["id"])
    assert "signed_up" in _types(entries)
    assert "logged_in" in _types(entries)
    # Newest first -- the login happened after the signup.
    assert _types(entries).index("logged_in") < _types(entries).index("signed_up")


def test_a_failed_login_is_recorded_even_for_an_unknown_address(client, seed_roles, admin_token):
    """The entries most worth having are the ones with no account behind them --
    requiring a real user row would drop exactly those."""
    client.post("/api/v1/auth/login", json={"email": "ghost@example.com", "password": "wrong"})

    feed = client.get("/api/v1/admin/activity", headers=auth_headers(admin_token)).json()
    failures = [e for e in feed if e["type"] == "login_failed"]
    assert failures, "a failed login against an unknown address was not recorded"
    assert failures[0]["subject_email"] == "ghost@example.com"


def test_an_admin_action_records_who_did_it(client, seed_roles, admin_token):
    """The point of separating actor from subject: 'who reset this password?'
    has to be answerable."""
    _register_student_and_login(client, email="target@example.com")
    students = client.get("/api/v1/users/students", headers=auth_headers(admin_token)).json()
    target = next(s for s in students if s["email"] == "target@example.com")

    client.post(f"/api/v1/users/{target['user_id']}/reset-password",
                json={"new_password": "AdminSetPass123"}, headers=auth_headers(admin_token))

    entries = _activity(client, admin_token, target["user_id"])
    reset = next(e for e in entries if e["type"] == "password_reset_by_admin")
    assert reset["actor_email"] == "admin@example.com"
    assert reset["subject_email"] == "target@example.com"
    assert reset["by_someone_else"] is True
    assert reset["notable"] is True


def test_disabling_and_re_enabling_an_account_is_recorded(client, seed_roles, admin_token):
    _register_student_and_login(client, email="onoff@example.com")
    students = client.get("/api/v1/users/students", headers=auth_headers(admin_token)).json()
    target = next(s for s in students if s["email"] == "onoff@example.com")

    client.post(f"/api/v1/users/{target['user_id']}/deactivate", headers=auth_headers(admin_token))
    client.post(f"/api/v1/users/{target['user_id']}/activate", headers=auth_headers(admin_token))

    types = _types(_activity(client, admin_token, target["user_id"]))
    assert "account_disabled" in types
    assert "account_enabled" in types


def test_the_trail_never_contains_a_password(client, seed_roles, admin_token):
    """An audit log is read by more people than the data it describes."""
    _register_student_and_login(client, email="secret@example.com")
    students = client.get("/api/v1/users/students", headers=auth_headers(admin_token)).json()
    target = next(s for s in students if s["email"] == "secret@example.com")
    client.post(f"/api/v1/users/{target['user_id']}/reset-password",
                json={"new_password": "SuperSecret999"}, headers=auth_headers(admin_token))

    blob = str(_activity(client, admin_token, target["user_id"]))
    assert "SuperSecret999" not in blob
    assert "Sup3rSecret" not in blob


# --- deletion -----------------------------------------------------------------

def test_an_admin_can_delete_a_student_account(client, seed_roles, admin_token):
    _register_student_and_login(client, email="doomed@example.com")
    students = client.get("/api/v1/users/students", headers=auth_headers(admin_token)).json()
    target = next(s for s in students if s["email"] == "doomed@example.com")

    response = client.delete(f"/api/v1/users/{target['user_id']}", headers=auth_headers(admin_token))
    assert response.status_code == 200, response.text
    assert response.json()["email"] == "doomed@example.com"

    remaining = client.get("/api/v1/users/students", headers=auth_headers(admin_token)).json()
    assert not [s for s in remaining if s["email"] == "doomed@example.com"]
    assert client.post("/api/v1/auth/login", json={
        "email": "doomed@example.com", "password": "Sup3rSecret!"}).status_code == 401


def test_the_deletion_record_outlives_the_account(client, seed_roles, admin_token):
    """The FKs are ON DELETE SET NULL rather than CASCADE precisely so the most
    important line in the trail is not destroyed along with its subject."""
    _register_student_and_login(client, email="gone@example.com")
    students = client.get("/api/v1/users/students", headers=auth_headers(admin_token)).json()
    target = next(s for s in students if s["email"] == "gone@example.com")

    client.delete(f"/api/v1/users/{target['user_id']}", headers=auth_headers(admin_token))

    feed = client.get("/api/v1/admin/activity", headers=auth_headers(admin_token)).json()
    deletions = [e for e in feed if e["type"] == "account_deleted"]
    assert deletions, "the deletion left no record"
    # Readable after the account is gone, thanks to the denormalised email.
    assert deletions[0]["subject_email"] == "gone@example.com"
    assert deletions[0]["actor_email"] == "admin@example.com"


def test_an_admin_cannot_delete_their_own_account(client, seed_roles, admin_token, db_session):
    from app.models.user import User
    admin = db_session.query(User).filter(User.email == "admin@example.com").first()
    response = client.delete(f"/api/v1/users/{admin.id}", headers=auth_headers(admin_token))
    assert response.status_code == 400


def test_a_non_admin_cannot_delete_accounts(client, seed_roles, admin_token):
    victim_token = _register_student_and_login(client, email="victim@example.com")
    me = client.get("/api/v1/users/me", headers=auth_headers(victim_token)).json()
    examiner_token = _create_examiner_and_login(client, admin_token)

    assert client.delete(f"/api/v1/users/{me['id']}",
                         headers=auth_headers(examiner_token)).status_code == 403
    assert client.delete(f"/api/v1/users/{me['id']}",
                         headers=auth_headers(victim_token)).status_code == 403


# --- the one-time password reveal ---------------------------------------------

def test_reset_returns_the_new_password_once_for_the_admin_to_pass_on(client, seed_roles, admin_token):
    """The admin needs a password they can read and hand over; it is simply
    never STORED readably. Same one-time-reveal pattern as the examiner
    credential handoff."""
    _register_student_and_login(client, email="handoff@example.com")
    students = client.get("/api/v1/users/students", headers=auth_headers(admin_token)).json()
    target = next(s for s in students if s["email"] == "handoff@example.com")

    response = client.post(f"/api/v1/users/{target['user_id']}/reset-password",
                           json={"new_password": "HandedOver123"}, headers=auth_headers(admin_token))
    assert response.status_code == 200
    assert response.json()["password"] == "HandedOver123"
    assert response.json()["email"] == "handoff@example.com"

    # Stored as a hash -- the plaintext is not retrievable from any read endpoint.
    detail = client.get(f"/api/v1/users/students", headers=auth_headers(admin_token)).json()
    assert "HandedOver123" not in str(detail)


# --- access control -----------------------------------------------------------

def test_only_an_admin_can_read_the_activity_trail(client, seed_roles, admin_token):
    student_token = _register_student_and_login(client)
    me = client.get("/api/v1/users/me", headers=auth_headers(student_token)).json()
    examiner_token = _create_examiner_and_login(client, admin_token)

    assert client.get(f"/api/v1/admin/users/{me['id']}/activity",
                      headers=auth_headers(student_token)).status_code == 403
    assert client.get(f"/api/v1/admin/users/{me['id']}/activity",
                      headers=auth_headers(examiner_token)).status_code == 403
    assert client.get("/api/v1/admin/activity", headers=auth_headers(student_token)).status_code == 403


def test_recording_failure_never_breaks_the_action_it_describes(client, seed_roles, monkeypatch):
    """The logging system must not be able to take down the thing it observes."""
    from app.services import activity_service

    def _explode(*_args, **_kwargs):
        raise RuntimeError("audit backend is on fire")

    monkeypatch.setattr(activity_service.ActivityLog, "__init__", _explode)

    response = client.post("/api/v1/auth/register/student", json={
        "first_name": "Still", "last_name": "Works", "email": "resilient@example.com",
        "password": "Str0ngPass!",
    })
    assert response.status_code == 201, "a failing audit write broke registration"
