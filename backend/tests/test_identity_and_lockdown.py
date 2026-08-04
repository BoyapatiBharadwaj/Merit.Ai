"""
Identity verification lock and exam lockdown strike enforcement.

These cover the two rules that are easy to get subtly wrong:
  * a verified student's name/email must become immutable, while their
    password stays changeable
  * lockdown strikes must be counted from persisted rows (so a refresh can't
    reset them) and must force-submit the attempt at the limit
"""
import pytest

from app.core.config import settings
from app.models.enums import AttemptStatus
from app.repositories import attempt_repository, user_repository
from app.services import identity_service, lockdown_service
from tests.conftest import auth_headers
from tests.test_exam_workflow import (
    _build_published_exam,
    _create_examiner_and_login,
    _register_student_and_login,
)


def _login_student(client, email="student@example.com", password="Sup3rSecret!"):
    """Fresh token for an already-registered student, for tests that need to act
    as them again after changing their row directly."""
    return client.post("/api/v1/auth/login", json={"email": email, "password": password}).json()["access_token"]


def _student_row(db_session, email="student@example.com"):
    user = user_repository.get_user_by_email(db_session, email)
    return user_repository.get_student_by_user_id(db_session, user.id)


# --------------------------------------------------------------------------
# Identity lock
# --------------------------------------------------------------------------

def test_identity_locks_only_when_both_checks_pass(client, seed_roles, db_session):
    _register_student_and_login(client)
    student = _student_row(db_session)

    # ID verified alone is not enough -- the face profile is still missing.
    identity_service.record_id_verification(db_session, student, True, "Test Student")
    assert student.id_verified is True
    assert student.identity_locked is False
    assert identity_service.verification_state(db_session, student)["exam_ready"] is False


def test_failed_id_match_does_not_flag_the_student(client, seed_roles, db_session):
    """A bad scan must be retryable, never a permanent mark on the account."""
    _register_student_and_login(client)
    student = _student_row(db_session)

    identity_service.record_id_verification(db_session, student, False, "Someone Else")
    assert student.id_verified is False
    assert student.identity_locked is False


def test_locked_student_cannot_change_name_or_email(client, seed_roles, db_session):
    token = _register_student_and_login(client)
    student = _student_row(db_session)

    # Force the locked state directly rather than driving the camera flow.
    student.id_verified = True
    student.identity_locked = True
    db_session.commit()

    response = client.patch(
        "/api/v1/users/me",
        json={"first_name": "Someone", "last_name": "Else"},
        headers=auth_headers(token),
    )
    assert response.status_code == 403
    assert "locked" in response.json()["detail"].lower()


def test_unlocked_student_can_change_name(client, seed_roles):
    token = _register_student_and_login(client)
    response = client.patch(
        "/api/v1/users/me",
        json={"first_name": "Renamed", "last_name": "Student"},
        headers=auth_headers(token),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["first_name"] == "Renamed"
    assert body["full_name"] == "Renamed Student"


def test_an_identity_locked_student_is_not_stuck_without_a_password_route(client, seed_roles, db_session, admin_token):
    """Identity locking freezes a student's name and email, and passwords are
    now administrator-managed -- so the thing worth proving is that the two
    together do not strand someone.

    This previously asserted that a locked student could change their own
    password. That route is gone by design (users.change_my_password is
    admin-only now), so the test asserts the remaining route instead: an admin
    can still reset it, and the new password works.
    """
    _register_student_and_login(client)
    student = _student_row(db_session)
    student.id_verified = True
    student.identity_locked = True
    db_session.commit()

    # The student's own attempt is refused...
    own = client.post(
        "/api/v1/users/me/password",
        json={"current_password": "Sup3rSecret!", "new_password": "An0therSecret!"},
        headers=auth_headers(_login_student(client)),
    )
    assert own.status_code == 403

    # ...but the administrator route works, and identity lock does not block it.
    reset = client.post(
        f"/api/v1/users/{student.user_id}/reset-password",
        json={"new_password": "An0therSecret!"},
        headers=auth_headers(admin_token),
    )
    assert reset.status_code == 200, reset.text

    login = client.post("/api/v1/auth/login", json={"email": "student@example.com", "password": "An0therSecret!"})
    assert login.status_code == 200


def test_password_change_rejects_wrong_current_password(client, seed_roles, admin_token):
    """The current-password check still matters -- a stolen session must not be
    enough to lock the real owner out. Exercised against an admin now, since
    that is the only role permitted to change its own password."""
    response = client.post(
        "/api/v1/users/me/password",
        json={"current_password": "not-the-password", "new_password": "An0therSecret!"},
        headers=auth_headers(admin_token),
    )
    assert response.status_code == 400


# --------------------------------------------------------------------------
# Role-based account access
# --------------------------------------------------------------------------

def test_examiner_can_list_students(client, seed_roles, admin_token):
    examiner_token = _create_examiner_and_login(client, admin_token)
    _register_student_and_login(client)

    response = client.get("/api/v1/users/students", headers=auth_headers(examiner_token))
    assert response.status_code == 200, response.text
    assert len(response.json()) == 1


def test_student_cannot_list_students(client, seed_roles):
    token = _register_student_and_login(client)
    response = client.get("/api/v1/users/students", headers=auth_headers(token))
    assert response.status_code == 403


def test_examiner_cannot_list_examiners(client, seed_roles, admin_token):
    """Examiner access is scoped to students only -- the examiner directory
    stays admin-only."""
    examiner_token = _create_examiner_and_login(client, admin_token)
    response = client.get("/api/v1/users/examiners", headers=auth_headers(examiner_token))
    assert response.status_code == 403


def test_admin_can_deactivate_and_reactivate_a_student(client, seed_roles, admin_token, db_session):
    _register_student_and_login(client)
    user = user_repository.get_user_by_email(db_session, "student@example.com")

    assert client.post(f"/api/v1/users/{user.id}/deactivate", headers=auth_headers(admin_token)).status_code == 200
    blocked = client.post("/api/v1/auth/login", json={"email": "student@example.com", "password": "Sup3rSecret!"})
    assert blocked.status_code == 403

    assert client.post(f"/api/v1/users/{user.id}/activate", headers=auth_headers(admin_token)).status_code == 200
    assert client.post("/api/v1/auth/login", json={"email": "student@example.com", "password": "Sup3rSecret!"}).status_code == 200


def test_admin_cannot_deactivate_their_own_account(client, seed_roles, admin_token, db_session):
    admin = user_repository.get_user_by_email(db_session, "admin@example.com")
    response = client.post(f"/api/v1/users/{admin.id}/deactivate", headers=auth_headers(admin_token))
    assert response.status_code == 400


# --------------------------------------------------------------------------
# Lockdown strikes
# --------------------------------------------------------------------------

def _start_unproctored_attempt(client, admin_token):
    examiner_headers = auth_headers(_create_examiner_and_login(client, admin_token))
    exam_id = _build_published_exam(client, examiner_headers, proctoring_enabled=False)
    student_token = _register_student_and_login(client)
    started = client.post(f"/api/v1/attempts/start/{exam_id}", headers=auth_headers(student_token))
    assert started.status_code == 200, started.text
    return student_token, started.json()["attempt_id"]


def test_strikes_accumulate_and_terminate_at_the_limit(client, seed_roles, admin_token, db_session, monkeypatch):
    # The debounce window exists to stop one Alt-Tab costing three strikes
    # (covered separately by test_burst_of_events_collapses_into_one_strike).
    # A test firing three requests back-to-back looks exactly like that burst,
    # so disable it here to exercise the accumulate-and-terminate path itself.
    monkeypatch.setattr(settings, "LOCKDOWN_STRIKE_DEBOUNCE_SECONDS", 0.0)

    student_token, attempt_id = _start_unproctored_attempt(client, admin_token)
    headers = auth_headers(student_token)
    limit = settings.LOCKDOWN_STRIKE_LIMIT

    last = None
    for expected in range(1, limit + 1):
        response = client.post(
            "/api/v1/proctoring/lockdown/strike",
            json={"attempt_id": attempt_id, "event_type": "tab_switch", "description": f"breach {expected}"},
            headers=headers,
        )
        assert response.status_code == 200, response.text
        last = response.json()
        assert last["strikes"] == expected

    assert last["terminated"] is True

    # The termination must be a fact in the database, not just a client hint.
    db_session.expire_all()
    attempt = attempt_repository.get_attempt(db_session, attempt_id)
    assert attempt.status == AttemptStatus.AUTO_SUBMITTED


def test_strike_count_survives_a_reload(client, seed_roles, admin_token):
    """The whole point of server-side counting: a student who refreshes must
    not get a fresh strike budget."""
    student_token, attempt_id = _start_unproctored_attempt(client, admin_token)
    headers = auth_headers(student_token)

    client.post(
        "/api/v1/proctoring/lockdown/strike",
        json={"attempt_id": attempt_id, "event_type": "fullscreen_exit", "description": "left fullscreen"},
        headers=headers,
    )

    status = client.get(f"/api/v1/proctoring/lockdown/status/{attempt_id}", headers=headers)
    assert status.status_code == 200
    assert status.json()["strikes"] == 1
    assert status.json()["remaining"] == settings.LOCKDOWN_STRIKE_LIMIT - 1


def test_screen_share_stopped_counts_as_a_strike(client, seed_roles, admin_token):
    """Ending the shared-screen stream mid-exam (lib/lockdown.js's
    watchScreenShare -> track 'ended') is as strike-worthy as leaving
    fullscreen or switching tabs -- it goes through the same endpoint and
    the same STRIKE_EVENT_TYPES list."""
    student_token, attempt_id = _start_unproctored_attempt(client, admin_token)
    headers = auth_headers(student_token)

    response = client.post(
        "/api/v1/proctoring/lockdown/strike",
        json={"attempt_id": attempt_id, "event_type": "screen_share_stopped", "description": "shared screen stopped"},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["strikes"] == 1


def test_strike_rejects_unknown_event_type(client, seed_roles, admin_token):
    student_token, attempt_id = _start_unproctored_attempt(client, admin_token)
    response = client.post(
        "/api/v1/proctoring/lockdown/strike",
        json={"attempt_id": attempt_id, "event_type": "copy_paste_attempt", "description": "nope"},
        headers=auth_headers(student_token),
    )
    # Copy/paste is a logged violation but never a terminating strike.
    assert response.status_code == 422


def test_student_cannot_strike_another_students_attempt(client, seed_roles, admin_token):
    _, attempt_id = _start_unproctored_attempt(client, admin_token)
    intruder_token = _register_student_and_login(client, email="intruder@example.com")

    response = client.post(
        "/api/v1/proctoring/lockdown/strike",
        json={"attempt_id": attempt_id, "event_type": "tab_switch", "description": "not mine"},
        headers=auth_headers(intruder_token),
    )
    assert response.status_code == 404


def test_burst_of_events_collapses_into_one_strike():
    """A single Alt-Tab fires blur + visibilitychange + fullscreenchange
    together; that must cost one strike, not three."""
    from datetime import datetime, timedelta, timezone

    base = datetime(2026, 8, 1, 12, 0, 0, tzinfo=timezone.utc)
    window = settings.LOCKDOWN_STRIKE_DEBOUNCE_SECONDS

    class FakeEvent:
        def __init__(self, created_at):
            self.created_at = created_at

    burst = [FakeEvent(base), FakeEvent(base + timedelta(seconds=0.2)), FakeEvent(base + timedelta(seconds=0.4))]
    assert lockdown_service._debounced_strike_count(burst) == 1

    # Two genuinely separate breaches, comfortably outside the debounce window.
    separate = burst + [FakeEvent(base + timedelta(seconds=window + 5))]
    assert lockdown_service._debounced_strike_count(separate) == 2
