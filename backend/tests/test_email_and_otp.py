"""
Tests for the outbound-email features: the SMTP wrapper's failure behaviour,
the one-time passcode lifecycle, the account-flow notifications, and the
pre-exam reminder scheduler.

No SMTP server is involved anywhere. `email_service.send` is monkeypatched to
record into a list, which is also the only honest way to assert "this flow sends
exactly one message to exactly this address" -- the thing that actually matters
about an email feature.
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.core.config import settings
from app.models.exam import Exam
from app.models.otp import OtpPurpose
from app.services import email_service, otp_service, reminder_service
from tests.conftest import auth_headers


@pytest.fixture
def outbox(monkeypatch):
    """Capture every message the app tries to send.

    Patched at `email_service.send`, the single choke point every other helper
    funnels through, so a new caller added later is covered by this fixture
    automatically rather than needing its own stub.
    """
    sent = []

    def _capture(*, to, subject, text_body, html_body=None):
        sent.append({"to": to, "subject": subject, "text": text_body, "html": html_body})
        return True

    monkeypatch.setattr(email_service, "send", _capture)
    # `queue` closes over the module-global `send`, so patching send is enough
    # for the inline path -- but BackgroundTasks defers the call past the point
    # TestClient returns, so route it inline to make assertions deterministic.
    monkeypatch.setattr(email_service, "queue",
                        lambda background, **kw: sent.append({**kw, "html_body": kw.get("html_body")}) or None)
    return sent


@pytest.fixture
def email_on(monkeypatch):
    """Pretend this deployment has working SMTP configured."""
    monkeypatch.setattr(email_service, "is_enabled", lambda: True)


# --- the SMTP wrapper ---------------------------------------------------------

def test_send_is_a_no_op_when_email_is_disabled(monkeypatch):
    """The default for a fresh clone and the whole test suite. Must not raise."""
    monkeypatch.setattr(settings, "EMAIL_ENABLED", False)
    assert email_service.send(to="a@b.com", subject="hi", text_body="hi") is False


def test_send_never_raises_when_the_mail_server_fails(monkeypatch):
    """The property every caller depends on: a broken mail server is a logged
    False, never an exception that turns into a 500 for the user."""
    monkeypatch.setattr(email_service, "is_enabled", lambda: True)
    monkeypatch.setattr(settings, "SMTP_USE_TLS", True)

    def _explode(*_args, **_kwargs):
        raise OSError("connection refused")

    monkeypatch.setattr(email_service.smtplib, "SMTP", _explode)
    assert email_service.send(to="a@b.com", subject="hi", text_body="hi") is False


def test_messages_carry_both_a_text_and_an_html_part():
    subject, text, html = email_service.otp_message(code="123456", purpose_label="test", ttl_minutes=10)
    assert "123456" in text and "123456" in html
    assert subject.endswith("123456")


# --- OTP lifecycle ------------------------------------------------------------

def _issue(db, email="student@example.com", purpose=OtpPurpose.SIGNUP):
    """Issue a code and dig the plaintext back out.

    The service never returns the code (it only mails it), and the row only
    holds a digest -- so the test brute-forces the six digits against the same
    HMAC the service uses. Slightly awkward, and deliberately so: a test helper
    that could read the code straight out of the database would mean the
    database held it in the clear, which is exactly what must not be true.
    """
    otp_service.request_code(db, email=email, purpose=purpose)
    from app.repositories import otp_repository
    row = otp_repository.get_latest(db, email=email, purpose=purpose)
    for candidate in range(10 ** settings.OTP_LENGTH):
        code = str(candidate).zfill(settings.OTP_LENGTH)
        if otp_service._digest(code) == row.code_hash:
            return code, row
    raise AssertionError("issued code did not match any value in the keyspace")


def test_a_valid_code_verifies_once_and_then_is_dead(db_session, outbox):
    code, _ = _issue(db_session)
    otp_service.verify_code(db_session, email="student@example.com",
                            purpose=OtpPurpose.SIGNUP, code=code)

    # Same code, second time: single-use means this must now fail.
    with pytest.raises(Exception) as caught:
        otp_service.verify_code(db_session, email="student@example.com",
                                purpose=OtpPurpose.SIGNUP, code=code)
    assert caught.value.status_code == 400


def test_a_code_issued_for_signup_cannot_be_spent_on_a_password_reset(db_session, outbox):
    """Purpose is part of the lookup key, not a label -- so a code mailed for
    one flow is simply not found by the other."""
    code, _ = _issue(db_session, purpose=OtpPurpose.SIGNUP)
    with pytest.raises(Exception) as caught:
        otp_service.verify_code(db_session, email="student@example.com",
                                purpose=OtpPurpose.PASSWORD_RESET, code=code)
    assert caught.value.status_code == 400


def test_an_expired_code_is_rejected(db_session, outbox):
    code, row = _issue(db_session)
    row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db_session.commit()

    with pytest.raises(Exception) as caught:
        otp_service.verify_code(db_session, email="student@example.com",
                                purpose=OtpPurpose.SIGNUP, code=code)
    assert caught.value.status_code == 400


def test_wrong_guesses_are_counted_and_eventually_lock_the_code(db_session, outbox, monkeypatch):
    monkeypatch.setattr(settings, "OTP_MAX_ATTEMPTS", 3)
    code, _ = _issue(db_session)
    wrong = "0" * settings.OTP_LENGTH if code != "0" * settings.OTP_LENGTH else "1" * settings.OTP_LENGTH

    for _ in range(3):
        with pytest.raises(Exception):
            otp_service.verify_code(db_session, email="student@example.com",
                                    purpose=OtpPurpose.SIGNUP, code=wrong)

    # Budget exhausted: even the CORRECT code is now refused, which is the
    # point -- otherwise the limit would only slow an attacker down, not stop
    # them, once they eventually guessed right.
    with pytest.raises(Exception) as caught:
        otp_service.verify_code(db_session, email="student@example.com",
                                purpose=OtpPurpose.SIGNUP, code=code)
    assert caught.value.status_code == 429


def test_issuing_a_new_code_supersedes_the_previous_one(db_session, outbox, monkeypatch):
    monkeypatch.setattr(settings, "OTP_RESEND_COOLDOWN_SECONDS", 0)
    first, _ = _issue(db_session)
    second, _ = _issue(db_session)
    assert first != second

    with pytest.raises(Exception):
        otp_service.verify_code(db_session, email="student@example.com",
                                purpose=OtpPurpose.SIGNUP, code=first)
    otp_service.verify_code(db_session, email="student@example.com",
                            purpose=OtpPurpose.SIGNUP, code=second)


def test_resend_cooldown_blocks_an_immediate_second_request(db_session, outbox, monkeypatch):
    monkeypatch.setattr(settings, "OTP_RESEND_COOLDOWN_SECONDS", 60)
    otp_service.request_code(db_session, email="student@example.com", purpose=OtpPurpose.SIGNUP)
    with pytest.raises(Exception) as caught:
        otp_service.request_code(db_session, email="student@example.com", purpose=OtpPurpose.SIGNUP)
    assert caught.value.status_code == 429


def test_the_code_is_never_stored_in_plaintext(db_session, outbox):
    code, row = _issue(db_session)
    assert code not in row.code_hash
    assert len(row.code_hash) == 64


# --- HTTP surface -------------------------------------------------------------

def test_otp_endpoints_refuse_to_pretend_when_email_is_not_configured(client, monkeypatch):
    """Better an honest 503 than "check your inbox" for a mail that cannot be
    sent -- the same point the old ForgotPassword page made in prose."""
    monkeypatch.setattr(email_service, "is_enabled", lambda: False)
    response = client.post("/api/v1/auth/password-reset/request", json={"email": "nobody@example.com"})
    assert response.status_code == 503


def test_password_reset_request_does_not_reveal_whether_an_account_exists(client, email_on, outbox, monkeypatch):
    monkeypatch.setattr(settings, "OTP_RESEND_COOLDOWN_SECONDS", 0)
    known = client.post("/api/v1/auth/password-reset/request", json={"email": "admin@example.com"})
    unknown = client.post("/api/v1/auth/password-reset/request", json={"email": "ghost@example.com"})

    assert known.status_code == unknown.status_code == 200
    assert known.json()["message"] == unknown.json()["message"]


def test_password_reset_end_to_end_changes_the_password(client, seed_roles, db_session, email_on, outbox):
    client.post("/api/v1/auth/register/student", json={
        "first_name": "Reset", "last_name": "Me", "email": "reset@example.com",
        "password": "OldPass123",
    })
    code, _ = _issue(db_session, email="reset@example.com", purpose=OtpPurpose.PASSWORD_RESET)

    response = client.post("/api/v1/auth/password-reset/confirm", json={
        "email": "reset@example.com", "code": code, "new_password": "BrandNew456",
    })
    assert response.status_code == 200

    assert client.post("/api/v1/auth/login", json={
        "email": "reset@example.com", "password": "BrandNew456"}).status_code == 200
    assert client.post("/api/v1/auth/login", json={
        "email": "reset@example.com", "password": "OldPass123"}).status_code == 401


def test_password_reset_rejects_a_wrong_code_without_changing_anything(client, seed_roles, db_session, email_on, outbox):
    client.post("/api/v1/auth/register/student", json={
        "first_name": "Safe", "last_name": "User", "email": "safe@example.com",
        "password": "OldPass123",
    })
    _issue(db_session, email="safe@example.com", purpose=OtpPurpose.PASSWORD_RESET)

    response = client.post("/api/v1/auth/password-reset/confirm", json={
        "email": "safe@example.com", "code": "000000", "new_password": "Attacker999",
    })
    assert response.status_code in (400, 429)
    assert client.post("/api/v1/auth/login", json={
        "email": "safe@example.com", "password": "OldPass123"}).status_code == 200


# --- account-flow notifications ----------------------------------------------

def test_submitting_an_access_request_emails_an_administrator(client, seed_roles, email_on, outbox, monkeypatch):
    monkeypatch.setattr(settings, "ADMIN_NOTIFICATION_EMAIL", "admin.merit.ai@gmail.com")
    response = client.post("/api/v1/access-requests", json={
        "first_name": "Asha", "last_name": "Rao", "email": "asha@college.edu",
        "organization_name": "Example College",
        "purpose": "Running end-of-semester assessments for around 300 students.",
    })
    assert response.status_code == 201
    assert [m for m in outbox if m["to"] == "admin.merit.ai@gmail.com"]


def test_a_repeat_access_request_does_not_email_again(client, seed_roles, email_on, outbox, monkeypatch):
    """De-duping the admin's queue must not become a way to flood their inbox."""
    monkeypatch.setattr(settings, "ADMIN_NOTIFICATION_EMAIL", "admin.merit.ai@gmail.com")
    body = {"first_name": "Asha", "last_name": "Rao", "email": "asha@college.edu",
            "organization_name": "Example College",
            "purpose": "Running end-of-semester assessments for around 300 students."}
    client.post("/api/v1/access-requests", json=body)
    client.post("/api/v1/access-requests", json=body)
    assert len(outbox) == 1


def test_approving_a_request_emails_the_examiner_their_credentials(client, admin_token, email_on, outbox):
    client.post("/api/v1/access-requests", json={
        "first_name": "Asha", "last_name": "Rao", "email": "asha@college.edu",
        "organization_name": "Example College",
        "purpose": "Running end-of-semester assessments for around 300 students.",
    })
    outbox.clear()

    requests = client.get("/api/v1/access-requests", headers=auth_headers(admin_token)).json()
    response = client.post(f"/api/v1/access-requests/{requests[0]['id']}/approve",
                           json={"password": "Examiner@123"}, headers=auth_headers(admin_token))
    assert response.status_code == 200

    delivered = [m for m in outbox if m["to"] == "asha@college.edu"]
    assert len(delivered) == 1
    assert "Examiner@123" in delivered[0]["text_body"]


# --- exam notifications and the reminder scheduler ----------------------------

def _published_exam(db_session, *, notify_email, start_time, reminder_sent_at=None):
    exam = Exam(
        title="Data Structures Final", duration_minutes=60, examiner_id=1,
        status="published", notify_email=notify_email, start_time=start_time,
        reminder_sent_at=reminder_sent_at, pass_percentage=40,
    )
    db_session.add(exam)
    db_session.commit()
    return exam


def test_reminder_is_sent_for_an_exam_starting_inside_the_window(db_session, email_on, outbox, monkeypatch):
    monkeypatch.setattr(email_service, "send", lambda **kw: outbox.append(kw) or True)
    _published_exam(db_session, notify_email="dept@college.edu",
                    start_time=datetime.now(timezone.utc) + timedelta(minutes=4))

    assert reminder_service.send_due_reminders(db_session) == 1
    assert outbox[-1]["to"] == "dept@college.edu"


def test_a_reminder_is_never_sent_twice(db_session, email_on, outbox, monkeypatch):
    """The property the whole reminder_sent_at column exists for: the polling
    window deliberately overlaps, so the same exam matches on consecutive
    passes and only the stamp stops a repeat."""
    monkeypatch.setattr(email_service, "send", lambda **kw: outbox.append(kw) or True)
    _published_exam(db_session, notify_email="dept@college.edu",
                    start_time=datetime.now(timezone.utc) + timedelta(minutes=4))

    assert reminder_service.send_due_reminders(db_session) == 1
    assert reminder_service.send_due_reminders(db_session) == 0
    assert len(outbox) == 1


def test_no_reminder_for_an_exam_that_is_still_far_away(db_session, email_on, outbox):
    _published_exam(db_session, notify_email="dept@college.edu",
                    start_time=datetime.now(timezone.utc) + timedelta(hours=3))
    assert reminder_service.send_due_reminders(db_session) == 0


def test_no_reminder_for_an_exam_that_has_already_started(db_session, email_on, outbox):
    """A late reminder is noise; after an outage it would be a burst of noise."""
    _published_exam(db_session, notify_email="dept@college.edu",
                    start_time=datetime.now(timezone.utc) - timedelta(minutes=1))
    assert reminder_service.send_due_reminders(db_session) == 0


def test_no_reminder_when_nobody_asked_to_be_notified(db_session, email_on, outbox):
    _published_exam(db_session, notify_email=None,
                    start_time=datetime.now(timezone.utc) + timedelta(minutes=4))
    assert reminder_service.send_due_reminders(db_session) == 0


def test_a_failed_send_still_marks_the_exam_so_it_cannot_retry_forever(db_session, email_on, monkeypatch):
    monkeypatch.setattr(email_service, "send", lambda **kw: False)
    exam = _published_exam(db_session, notify_email="bad@nowhere.invalid",
                           start_time=datetime.now(timezone.utc) + timedelta(minutes=4))

    reminder_service.send_due_reminders(db_session)
    db_session.refresh(exam)
    assert exam.reminder_sent_at is not None
    assert reminder_service.send_due_reminders(db_session) == 0


def test_the_scheduler_does_not_start_without_email_configured(monkeypatch):
    """Otherwise it would wake every minute to do nothing -- and worse, stamp
    reminder_sent_at on exams it never actually reminded."""
    monkeypatch.setattr(email_service, "is_enabled", lambda: False)
    started = []
    monkeypatch.setattr(reminder_service.asyncio, "create_task", lambda coro: started.append(coro))
    reminder_service.start()
    assert started == []
