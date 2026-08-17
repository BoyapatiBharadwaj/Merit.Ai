"""Tracked, retryable email delivery (the email_outbox table), and the shared
examiner-provisioning path both account-creation flows now use.
"""
import re
from urllib.parse import unquote

import pytest

from tests.conftest import auth_headers
from tests.test_exam_workflow import _create_examiner_and_login


def _extract_token(text: str) -> str:
    match = re.search(r"[?&]token=([A-Za-z0-9_\-%]+)", text)
    assert match, text
    return unquote(match.group(1))


# --- email_outbox: tracked delivery --------------------------------------------

def test_a_successful_send_is_recorded_sent(db_session, email_on, outbox):
    from app.models.email_outbox import EmailOutboxStatus
    from app.services import email_service

    # Queues run eagerly in the test suite (see conftest.py's _fake_redis fixture), so the job
    # behind enqueue_tracked has already run by the time this call returns.
    row = email_service.enqueue_tracked(db_session, to="ok@example.com", subject="Hi", text_body="body")
    db_session.refresh(row)

    assert row.status == EmailOutboxStatus.SENT
    assert row.sent_at is not None
    assert row.attempts == 0


def test_a_failed_send_is_queued_for_retry_not_lost(db_session, email_on, monkeypatch):
    from app.models.email_outbox import EmailOutboxStatus
    from app.services import email_service

    monkeypatch.setattr(email_service, "send", lambda **kw: False)

    row = email_service.enqueue_tracked(db_session, to="broken@example.com", subject="Hi", text_body="body")
    db_session.refresh(row)

    assert row.status == EmailOutboxStatus.PENDING
    assert row.attempts == 1
    assert row.last_error
    # Retry TIMING is RQ's job now (app/core/queues.py's Retry/backoff), not a
    # Postgres column the worker polls -- next_retry_at is no longer set.
    assert row.next_retry_at is None


def test_never_silently_reports_success_on_failure(db_session, email_on, monkeypatch):
    """The exact property the whole outbox exists for."""
    from app.models.email_outbox import EmailOutboxStatus
    from app.services import email_service

    monkeypatch.setattr(email_service, "send", lambda **kw: False)
    row = email_service.enqueue_tracked(db_session, to="x@example.com", subject="s", text_body="b")
    db_session.refresh(row)
    assert row.status != EmailOutboxStatus.SENT


def test_a_previously_failed_message_delivers_once_retried(db_session, email_on, monkeypatch):
    """Stands in for RQ's own Retry/backoff re-running the job (see app/core/queues.py)."""
    from app.models.email_outbox import EmailOutboxStatus
    from app.services import email_service
    from app.worker import jobs

    monkeypatch.setattr(email_service, "send", lambda **kw: False)
    row = email_service.enqueue_tracked(db_session, to="retry@example.com", subject="s", text_body="b")
    db_session.refresh(row)
    assert row.status == EmailOutboxStatus.PENDING
    assert row.attempts == 1

    monkeypatch.setattr(email_service, "send", lambda **kw: True)
    jobs.deliver_outbox_email(row.id)

    db_session.refresh(row)
    assert row.status == EmailOutboxStatus.SENT


def test_an_email_stops_retrying_once_its_attempt_budget_is_exhausted(db_session, email_on, monkeypatch):
    from app.models.email_outbox import EmailOutboxStatus
    from app.services import email_service
    from app.worker import jobs

    monkeypatch.setattr(email_service, "send", lambda **kw: False)
    row = email_service.enqueue_tracked(db_session, to="doomed@example.com", subject="s", text_body="b",
                                        max_attempts=2)
    db_session.refresh(row)
    assert row.attempts == 1
    assert row.status == EmailOutboxStatus.PENDING

    jobs.deliver_outbox_email(row.id)  # attempt 2 of 2
    db_session.refresh(row)
    assert row.status == EmailOutboxStatus.FAILED
    assert row.attempts == 2


# --- access-request notification: only marked notified on real delivery -------

def test_access_request_is_not_marked_notified_when_email_fails(client, seed_roles, db_session,
                                                                 email_on, monkeypatch):
    from app.core.config import settings
    from app.models.access_request import AccessRequest
    from app.services import email_service

    monkeypatch.setattr(settings, "ADMIN_NOTIFICATION_EMAIL", "ops@institute.edu")
    monkeypatch.setattr(email_service, "send", lambda **kw: False)

    client.post("/api/v1/access-requests", json={
        "first_name": "Jane", "last_name": "Doe", "email": "jane@institute.edu",
        "organization_name": "Acme Institute", "purpose": "Running a semester assessment for students.",
    })

    row = db_session.query(AccessRequest).filter(AccessRequest.email == "jane@institute.edu").one()
    assert row.last_notified_at is None

    from app.models.email_outbox import EmailOutbox
    outbox_row = db_session.query(EmailOutbox).filter(EmailOutbox.to_address == "ops@institute.edu").first()
    assert outbox_row is not None
    assert outbox_row.status == "pending"


def test_access_request_is_marked_notified_only_after_real_delivery(client, seed_roles, db_session,
                                                                     email_on, outbox, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "ADMIN_NOTIFICATION_EMAIL", "ops@institute.edu")

    client.post("/api/v1/access-requests", json={
        "first_name": "Jane", "last_name": "Doe", "email": "jane2@institute.edu",
        "organization_name": "Acme Institute", "purpose": "Running a semester assessment for students.",
    })

    from app.models.access_request import AccessRequest
    row = db_session.query(AccessRequest).filter(AccessRequest.email == "jane2@institute.edu").one()
    assert row.last_notified_at is not None


# --- manual examiner creation: shared provisioning, no plaintext password ------

VALID_EXAMINER = {
    "first_name": "Pat", "last_name": "Reviewer", "email": "pat@institute.edu",
    "organization_name": "Acme Institute",
}


def test_creating_an_examiner_never_returns_or_requires_a_password(client, seed_roles, admin_token):
    response = client.post("/api/v1/auth/examiners", json=VALID_EXAMINER, headers=auth_headers(admin_token))
    assert response.status_code == 201, response.text
    body = response.json()
    assert "password" not in body
    assert "access_token" not in body
    assert body["email"] == VALID_EXAMINER["email"]
    assert "activation_sent" in body


def test_a_manually_created_examiner_activates_the_same_way_an_approved_one_does(client, seed_roles,
                                                                                 admin_token, outbox, email_on):
    client.post("/api/v1/auth/examiners", json=VALID_EXAMINER, headers=auth_headers(admin_token))
    message = next(m for m in reversed(outbox) if m["to"] == VALID_EXAMINER["email"])
    token = _extract_token(message["text"])

    activated = client.post("/api/v1/auth/activate", json={
        "email": VALID_EXAMINER["email"], "token": token, "password": "brisk-harbour-lantern",
    })
    assert activated.status_code == 200, activated.text
    assert activated.json()["role"] == "examiner"

    login = client.post("/api/v1/auth/login",
                        json={"email": VALID_EXAMINER["email"], "password": "brisk-harbour-lantern"})
    assert login.status_code == 200


def test_manual_creation_queues_the_activation_email_even_when_delivery_will_fail(client, seed_roles,
                                                                                  admin_token, email_on, monkeypatch):
    """`activation_sent` now means "queued for delivery", not "confirmed delivered"."""
    from app.models.email_outbox import EmailOutbox, EmailOutboxStatus
    from app.database.session import SessionLocal
    from app.services import email_service

    monkeypatch.setattr(email_service, "send", lambda **kw: False)
    response = client.post("/api/v1/auth/examiners", json=VALID_EXAMINER, headers=auth_headers(admin_token))
    assert response.status_code == 201
    assert response.json()["activation_sent"] is True

    with SessionLocal() as db:
        row = db.query(EmailOutbox).filter(EmailOutbox.to_address == VALID_EXAMINER["email"]).one()
        assert row.status != EmailOutboxStatus.SENT
        assert row.attempts >= 1
        assert row.last_error


# --- resend activation ----------------------------------------------------------

def test_admin_can_resend_activation_for_an_examiner_who_has_not_activated(client, seed_roles,
                                                                           admin_token, outbox, email_on):
    created = client.post("/api/v1/auth/examiners", json=VALID_EXAMINER, headers=auth_headers(admin_token))
    user_id = created.json()["user_id"]
    outbox.clear()

    resent = client.post(f"/api/v1/auth/examiners/{user_id}/resend-activation",
                         headers=auth_headers(admin_token))
    assert resent.status_code == 200, resent.text
    assert resent.json()["activation_sent"] is True
    assert len(outbox) == 1


def test_resending_invalidates_the_previous_link(client, seed_roles, admin_token, outbox, email_on):
    created = client.post("/api/v1/auth/examiners", json=VALID_EXAMINER, headers=auth_headers(admin_token))
    user_id = created.json()["user_id"]
    first_message = next(m for m in reversed(outbox) if m["to"] == VALID_EXAMINER["email"])
    first_token = _extract_token(first_message["text"])
    outbox.clear()

    client.post(f"/api/v1/auth/examiners/{user_id}/resend-activation", headers=auth_headers(admin_token))
    second_message = next(m for m in reversed(outbox) if m["to"] == VALID_EXAMINER["email"])
    second_token = _extract_token(second_message["text"])

    assert client.post("/api/v1/auth/activate/check",
                       json={"email": VALID_EXAMINER["email"], "token": first_token}).json()["valid"] is False
    assert client.post("/api/v1/auth/activate/check",
                       json={"email": VALID_EXAMINER["email"], "token": second_token}).json()["valid"] is True


def test_cannot_resend_activation_for_an_already_activated_examiner(client, seed_roles, admin_token, db_session):
    from app.models.user import User

    _create_examiner_and_login(client, admin_token, email="already-active@example.com")
    user = db_session.query(User).filter(User.email == "already-active@example.com").one()

    refused = client.post(f"/api/v1/auth/examiners/{user.id}/resend-activation",
                          headers=auth_headers(admin_token))
    assert refused.status_code == 400


def test_resend_activation_requires_admin(client, seed_roles, admin_token):
    created = client.post("/api/v1/auth/examiners", json=VALID_EXAMINER, headers=auth_headers(admin_token))
    user_id = created.json()["user_id"]
    examiner_token = _create_examiner_and_login(client, admin_token, email="other-examiner@example.com")

    refused = client.post(f"/api/v1/auth/examiners/{user_id}/resend-activation",
                          headers=auth_headers(examiner_token))
    assert refused.status_code == 403


def test_examiner_list_flags_pending_activation(client, seed_roles, admin_token):
    client.post("/api/v1/auth/examiners", json=VALID_EXAMINER, headers=auth_headers(admin_token))
    listed = client.get("/api/v1/admin/examiners", headers=auth_headers(admin_token)).json()
    match = next(e for e in listed["items"] if e["email"] == VALID_EXAMINER["email"])
    assert match["pending_activation"] is True


def test_examiner_list_shows_activated_examiners_as_not_pending(client, seed_roles, admin_token):
    _create_examiner_and_login(client, admin_token, email="activated@example.com")
    listed = client.get("/api/v1/admin/examiners", headers=auth_headers(admin_token)).json()
    match = next(e for e in listed["items"] if e["email"] == "activated@example.com")
    assert match["pending_activation"] is False


# --- forgot-password: account-existence check ----------------------------------

def test_check_account_reports_false_for_an_unregistered_address(client, seed_roles):
    response = client.post("/api/v1/auth/password-reset/check-account",
                           json={"email": "nobody@example.com"})
    assert response.status_code == 200
    assert response.json()["exists"] is False


def test_check_account_reports_true_for_a_registered_address(client, seed_roles):
    client.post("/api/v1/auth/register/student", json={
        "first_name": "Sam", "last_name": "Rivers", "email": "sam@example.com",
        "password": "brisk-harbour-lantern", "accepted_terms": True, "accepted_proctoring": True,
    })
    response = client.post("/api/v1/auth/password-reset/check-account",
                           json={"email": "sam@example.com"})
    assert response.json()["exists"] is True


def test_check_account_reports_false_for_a_deactivated_account(client, seed_roles, db_session):
    from app.models.user import User

    client.post("/api/v1/auth/register/student", json={
        "first_name": "Sam", "last_name": "Rivers", "email": "gone@example.com",
        "password": "brisk-harbour-lantern", "accepted_terms": True, "accepted_proctoring": True,
    })
    user = db_session.query(User).filter(User.email == "gone@example.com").one()
    user.is_active = False
    db_session.commit()

    response = client.post("/api/v1/auth/password-reset/check-account", json={"email": "gone@example.com"})
    assert response.json()["exists"] is False


def test_check_account_does_not_require_email_to_be_configured(client, seed_roles):
    """Existence is a database fact, not an email-delivery outcome -- this must
    work even on a deployment with no SMTP configured at all."""
    response = client.post("/api/v1/auth/password-reset/check-account", json={"email": "anyone@example.com"})
    assert response.status_code == 200
