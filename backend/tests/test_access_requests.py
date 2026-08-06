"""
Examiner access request workflow: public submit, admin review, approval
minting a real examiner account.
"""
import re
from urllib.parse import unquote

import pytest

from tests.conftest import auth_headers
from tests.test_exam_workflow import _create_examiner_and_login, _register_student_and_login

VALID = {
    "first_name": "Jane",
    "last_name": "Doe",
    "email": "jane@institute.edu",
    "organization_name": "Acme Institute of Technology",
    "purpose": "Running end-of-semester programming assessments for around 300 students.",
}


def _token_from(outbox, email):
    """The raw token, recovered the way its recipient recovers it.

    Only the digest is stored, so the test cannot read the token out of the
    database -- which is the property under test. It comes out of the message
    body instead, exactly as it would for a person clicking the link.
    """
    message = next(m for m in reversed(outbox) if m["to"] == email)
    match = re.search(r"[?&]token=([A-Za-z0-9_\-%]+)", message["text"])
    assert match, message["text"]
    return unquote(match.group(1))


def submit(client, **overrides):
    return client.post("/api/v1/access-requests", json={**VALID, **overrides})


# --------------------------------------------------------------------------
# Public submission
# --------------------------------------------------------------------------

def test_anyone_can_submit_a_request(client, seed_roles):
    response = submit(client)
    assert response.status_code == 201, response.text
    assert response.json()["submitted"] is True


def test_submit_requires_a_meaningful_purpose(client, seed_roles):
    assert submit(client, purpose="hi").status_code == 422


def test_submit_requires_an_organization(client, seed_roles):
    assert submit(client, organization_name="").status_code == 422


def test_duplicate_pending_submission_is_deduped_not_rejected(client, seed_roles, admin_token):
    """A double-click or refresh-resubmit must not stack the admin's queue,
    and must not surface an error to the requester."""
    assert submit(client).status_code == 201
    assert submit(client).status_code == 201

    listed = client.get("/api/v1/access-requests", headers=auth_headers(admin_token)).json()
    assert len(listed) == 1


def test_submitting_for_an_existing_account_still_looks_normal(client, seed_roles, admin_token):
    """The public endpoint must not become an account-enumeration oracle --
    the response for a known email is indistinguishable from an unknown one."""
    _create_examiner_and_login(client, admin_token, email="taken@example.com")

    known = submit(client, email="taken@example.com")
    unknown = submit(client, email="brand-new@example.com")
    assert known.status_code == unknown.status_code == 201
    assert known.json() == unknown.json()


# --------------------------------------------------------------------------
# Access control
# --------------------------------------------------------------------------

def test_listing_requests_requires_admin(client, seed_roles):
    assert client.get("/api/v1/access-requests").status_code == 401


def test_student_cannot_list_requests(client, seed_roles):
    token = _register_student_and_login(client)
    assert client.get("/api/v1/access-requests", headers=auth_headers(token)).status_code == 403


def test_examiner_cannot_list_requests(client, seed_roles, admin_token):
    token = _create_examiner_and_login(client, admin_token)
    assert client.get("/api/v1/access-requests", headers=auth_headers(token)).status_code == 403


# --------------------------------------------------------------------------
# Review
# --------------------------------------------------------------------------

def _first_request_id(client, admin_token):
    return client.get("/api/v1/access-requests", headers=auth_headers(admin_token)).json()[0]["id"]


def test_approving_creates_an_account_that_only_its_owner_can_open(client, seed_roles, admin_token, db_session):
    """Approval mints the account and mails a link -- it does not mint a password.

    This test used to assert the opposite: that the admin chose a password in
    the approve payload and the new examiner could immediately log in with it.
    That behaviour was the bug. It meant the password existed in the admin's
    head and in an inbox in plain text, so "only this examiner could have done
    that" was never true of anything the account did.
    """
    submit(client)
    request_id = _first_request_id(client, admin_token)

    approved = client.post(
        f"/api/v1/access-requests/{request_id}/approve",
        json={"review_note": "Verified by phone."},
        headers=auth_headers(admin_token),
    )
    assert approved.status_code == 200, approved.text
    body = approved.json()
    assert body["status"] == "approved"
    assert body["created_user_id"] is not None

    # The account exists and is listed, with its organization resolved...
    examiners = client.get("/api/v1/users/examiners", headers=auth_headers(admin_token)).json()
    match = next(e for e in examiners if e["email"] == VALID["email"])
    assert match["organization_name"] == VALID["organization_name"]

    # ...but nobody can sign in yet. The stored secret is 256 random bits that
    # no human has ever seen, so there is no password to guess or to have been
    # mailed. Two plausible guesses, to make the point concretely.
    for guess in ("Sup3rSecret!", VALID["organization_name"]):
        denied = client.post("/api/v1/auth/login",
                             json={"email": VALID["email"], "password": guess})
        assert denied.status_code == 401, guess

    # The only way in is the activation token, which exists and is live.
    from app.models.otp import OtpCode, OtpPurpose

    row = (db_session.query(OtpCode)
           .filter(OtpCode.email == VALID["email"], OtpCode.purpose == OtpPurpose.ACTIVATION)
           .one())
    assert row.consumed_at is None
    # Stored as a hash, never as the token itself -- a leaked database row must
    # not be replayable into somebody's account.
    assert len(row.code_hash) == 64


def test_activation_sets_the_password_signs_in_and_burns_the_link(client, seed_roles, admin_token, db_session, outbox):
    submit(client)
    request_id = _first_request_id(client, admin_token)
    client.post(f"/api/v1/access-requests/{request_id}/approve",
                json={"review_note": None}, headers=auth_headers(admin_token))

    token = _token_from(outbox, VALID["email"])

    # Checking does not spend it: the screen asks before showing the form.
    check = client.post("/api/v1/auth/activate/check",
                        json={"email": VALID["email"], "token": token})
    assert check.status_code == 200 and check.json()["valid"] is True

    activated = client.post("/api/v1/auth/activate", json={
        "email": VALID["email"], "token": token, "password": "brisk-harbour-lantern",
    })
    assert activated.status_code == 200, activated.text
    assert activated.json()["role"] == "examiner"
    # Signed in already -- they proved mailbox control and just chose the
    # password; retyping it would establish nothing.
    assert activated.json()["access_token"]
    # And no longer holding somebody else's password.
    assert activated.json()["must_change_password"] is False

    # The password is now real...
    login = client.post("/api/v1/auth/login",
                        json={"email": VALID["email"], "password": "brisk-harbour-lantern"})
    assert login.status_code == 200

    # ...and the link is spent. A forwarded email cannot be replayed.
    replay = client.post("/api/v1/auth/activate", json={
        "email": VALID["email"], "token": token, "password": "second-attempt-passphrase",
    })
    assert replay.status_code == 400
    still_mine = client.post("/api/v1/auth/login",
                             json={"email": VALID["email"], "password": "second-attempt-passphrase"})
    assert still_mine.status_code == 401


def test_activation_also_verifies_the_address(client, seed_roles, admin_token, db_session, outbox):
    """The token only reached someone who can read that mailbox, which is
    exactly what verification establishes. Asking again would be theatre."""
    from app.models.user import User

    submit(client)
    request_id = _first_request_id(client, admin_token)
    client.post(f"/api/v1/access-requests/{request_id}/approve",
                json={"review_note": None}, headers=auth_headers(admin_token))
    token = _token_from(outbox, VALID["email"])

    user = db_session.query(User).filter(User.email == VALID["email"]).one()
    assert user.email_verified_at is None
    assert user.must_change_password is True

    client.post("/api/v1/auth/activate", json={
        "email": VALID["email"], "token": token, "password": "brisk-harbour-lantern",
    })
    db_session.expire_all()
    user = db_session.query(User).filter(User.email == VALID["email"]).one()
    assert user.email_verified_at is not None
    assert user.must_change_password is False


def test_a_wrong_token_cannot_activate(client, seed_roles, admin_token, db_session):
    submit(client)
    request_id = _first_request_id(client, admin_token)
    client.post(f"/api/v1/access-requests/{request_id}/approve",
                json={"review_note": None}, headers=auth_headers(admin_token))

    refused = client.post("/api/v1/auth/activate", json={
        "email": VALID["email"], "token": "n0t-the-token-that-was-issued-at-all",
        "password": "brisk-harbour-lantern",
    })
    assert refused.status_code == 400

    # An address with no account is refused identically -- the endpoint must not
    # become an account-enumeration oracle.
    unknown = client.post("/api/v1/auth/activate", json={
        "email": "nobody@example.com", "token": "n0t-the-token-that-was-issued-at-all",
        "password": "brisk-harbour-lantern",
    })
    assert unknown.status_code == 400
    assert unknown.json()["detail"] == refused.json()["detail"]


def test_rejecting_creates_no_account(client, seed_roles, admin_token):
    submit(client)
    request_id = _first_request_id(client, admin_token)

    rejected = client.post(
        f"/api/v1/access-requests/{request_id}/reject",
        json={"review_note": "Could not verify the institution."},
        headers=auth_headers(admin_token),
    )
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["status"] == "rejected"
    assert rejected.json()["created_user_id"] is None

    login = client.post("/api/v1/auth/login", json={"email": VALID["email"], "password": "Sup3rSecret!"})
    assert login.status_code == 401


def test_a_request_cannot_be_reviewed_twice(client, seed_roles, admin_token):
    submit(client)
    request_id = _first_request_id(client, admin_token)
    client.post(
        f"/api/v1/access-requests/{request_id}/approve",
        json={"password": "Sup3rSecret!"},
        headers=auth_headers(admin_token),
    )

    again = client.post(
        f"/api/v1/access-requests/{request_id}/reject",
        json={"review_note": "changed my mind"},
        headers=auth_headers(admin_token),
    )
    assert again.status_code == 400
    assert "already" in again.json()["detail"].lower()


def test_approving_when_the_email_already_has_an_account_is_refused(client, seed_roles, admin_token):
    """Guards the window between someone requesting access and an admin
    separately creating that account by hand."""
    submit(client)
    request_id = _first_request_id(client, admin_token)

    client.post(
        "/api/v1/auth/examiners",
        json={
            "first_name": "Jane", "last_name": "Doe", "email": VALID["email"],
            "organization_name": "Acme Institute of Technology", "password": "Sup3rSecret!",
        },
        headers=auth_headers(admin_token),
    )

    response = client.post(
        f"/api/v1/access-requests/{request_id}/approve",
        json={"password": "An0therSecret!"},
        headers=auth_headers(admin_token),
    )
    assert response.status_code == 400
    assert "already exists" in response.json()["detail"].lower()

    # The request must stay pending so the admin can still reject it.
    listed = client.get("/api/v1/access-requests", headers=auth_headers(admin_token)).json()
    assert listed[0]["status"] == "pending"


def test_pending_count_reflects_the_queue(client, seed_roles, admin_token):
    assert client.get("/api/v1/access-requests/pending-count", headers=auth_headers(admin_token)).json()["pending"] == 0

    submit(client)
    submit(client, email="second@institute.edu")
    assert client.get("/api/v1/access-requests/pending-count", headers=auth_headers(admin_token)).json()["pending"] == 2

    request_id = _first_request_id(client, admin_token)
    client.post(f"/api/v1/access-requests/{request_id}/reject", json={}, headers=auth_headers(admin_token))
    assert client.get("/api/v1/access-requests/pending-count", headers=auth_headers(admin_token)).json()["pending"] == 1


# ------------------------------------------------------------------------------
# Invitation-only registration
#
# REGISTRATION_MODE defaults to "open", which is right for evaluating the
# software and wrong for an institution running degree examinations: signup was
# available to anyone on the internet who found the URL, so the candidate list
# was whoever happened to sign up.
# ------------------------------------------------------------------------------

def _signup(client, email="outsider@example.com"):
    return client.post("/api/v1/auth/register/student", json={
        "first_name": "Sam", "last_name": "Rivers", "email": email,
        "password": "brisk-harbour-lantern",
        "accepted_terms": True, "accepted_proctoring": True,
    })


def test_open_mode_still_lets_anyone_register(client, seed_roles, monkeypatch):
    """The default must not change. Someone trying the software out should not
    have to seed a roster before they can create the first account."""
    from app.core.config import settings
    monkeypatch.setattr(settings, "REGISTRATION_MODE", "open")
    assert _signup(client).status_code == 201


def test_invite_mode_refuses_an_address_nobody_enrolled(client, seed_roles, monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "REGISTRATION_MODE", "invite")

    refused = _signup(client)
    assert refused.status_code == 403
    assert "not been invited" in refused.json()["detail"]


def test_invite_mode_accepts_someone_on_an_exam_roster(client, seed_roles, admin_token, db_session, monkeypatch):
    """The invite is the one the institution already had to create.

    Deliberately not a second allow-list. Exam participants and organization
    members are both keyed on email precisely because they are written before
    the account exists -- so the roster an examiner builds to run the exam is
    the same roster that authorises signing up for it, and there is no separate
    list to drift out of date.
    """
    from app.core.config import settings
    from app.models.organization import ExamParticipant
    from app.services import organization_service

    examiner_token = _create_examiner_and_login(client, admin_token)
    exam_id = client.post("/api/v1/exams", json={
        "title": "Invited candidates only", "duration_minutes": 30,
    }, headers=auth_headers(examiner_token)).json()["id"]

    from app.models.exam import Exam
    exam = db_session.query(Exam).get(exam_id)
    organization_service.add_exam_participants(db_session, exam, ["invited@example.com"],
                                               added_by_id=None)

    monkeypatch.setattr(settings, "REGISTRATION_MODE", "invite")
    assert _signup(client, "invited@example.com").status_code == 201
    # ...and still nobody else.
    assert _signup(client, "stranger@example.com").status_code == 403
    assert db_session.query(ExamParticipant).count() == 1


def test_invite_mode_accepts_an_organization_member(client, seed_roles, db_session, monkeypatch):
    from app.core.config import settings
    from app.services import organization_service

    org = organization_service.get_or_create(db_session, "Acme Institute of Technology")
    organization_service.enrol_emails(db_session, org.id, ["cohort@example.com"], invited_by_id=None)

    monkeypatch.setattr(settings, "REGISTRATION_MODE", "invite")
    assert _signup(client, "cohort@example.com").status_code == 201


# ------------------------------------------------------------------------------
# Staff sign-in notices
# ------------------------------------------------------------------------------

def test_staff_logins_are_emailed_and_candidate_logins_are_not(client, seed_roles, admin_token,
                                                               outbox, monkeypatch):
    """Staff only, and that restriction is the design.

    An examiner account can read candidate identity photographs and alter
    results, so an unexpected sign-in is worth an inbox interruption. Doing the
    same for candidates would be one email per student per exam, which is how a
    security notice becomes something people filter away unread.
    """
    from app.services import email_service

    monkeypatch.setattr(email_service, "is_enabled", lambda: True)

    _create_examiner_and_login(client, admin_token)
    staff_mail = [m for m in outbox if m["subject"] == "New sign-in to your Merit.Ai account"]
    assert len(staff_mail) == 1
    assert "reset your password" in staff_mail[0]["text"].lower()

    outbox.clear()
    # An EXPLICIT login. _register_student_and_login returns the token that
    # registration itself hands back and never touches /auth/login -- so using
    # it here made this half of the test vacuous: no login happened, so of
    # course no notice was sent, and the assertion held just as well with the
    # staff filter deleted. Removing the filter is now what makes this fail.
    _register_student_and_login(client, "candidate@example.com")
    signed_in = client.post("/api/v1/auth/login",
                            json={"email": "candidate@example.com", "password": "Sup3rSecret!"})
    assert signed_in.status_code == 200
    assert [m for m in outbox if m["subject"] == "New sign-in to your Merit.Ai account"] == []


def test_the_sign_in_notice_can_be_switched_off(client, seed_roles, admin_token, outbox, monkeypatch):
    from app.core.config import settings
    from app.services import email_service

    monkeypatch.setattr(email_service, "is_enabled", lambda: True)
    monkeypatch.setattr(settings, "NOTIFY_STAFF_ON_LOGIN", False)

    _create_examiner_and_login(client, admin_token)
    assert [m for m in outbox if m["subject"] == "New sign-in to your Merit.Ai account"] == []


def test_reissuing_an_activation_link_kills_the_previous_one(client, seed_roles, admin_token,
                                                             db_session, outbox, email_on):
    """"We resent it" must not quietly mean "there are now two".

    An address gets mistyped, or a message lands in spam, and somebody asks for
    the link again. If the first one stays live, an invitation that went to the
    wrong inbox is still usable by whoever received it -- and revoking access
    would mean tracking down every link ever issued.

    This test exists because removing the invalidation broke nothing: the suite
    passed with two live tokens, which is precisely the failure it should have
    caught.
    """
    from app.models.otp import OtpCode, OtpPurpose
    from app.services import otp_service

    submit(client)
    request_id = _first_request_id(client, admin_token)
    client.post(f"/api/v1/access-requests/{request_id}/approve",
                json={"review_note": None}, headers=auth_headers(admin_token))
    first = _token_from(outbox, VALID["email"])

    # A second link, as an admin re-sending the invitation would produce.
    second, _ = otp_service.issue_activation_token(db_session, email=VALID["email"])
    db_session.commit()

    assert client.post("/api/v1/auth/activate/check",
                       json={"email": VALID["email"], "token": first}).json()["valid"] is False
    assert client.post("/api/v1/auth/activate/check",
                       json={"email": VALID["email"], "token": second}).json()["valid"] is True

    # The old one cannot be redeemed either -- not merely reported as stale.
    stale = client.post("/api/v1/auth/activate", json={
        "email": VALID["email"], "token": first, "password": "brisk-harbour-lantern",
    })
    assert stale.status_code == 400
    assert client.post("/api/v1/auth/login",
                       json={"email": VALID["email"], "password": "brisk-harbour-lantern"}).status_code == 401

    # Exactly one live row, not two.
    live = (db_session.query(OtpCode)
            .filter(OtpCode.email == VALID["email"],
                    OtpCode.purpose == OtpPurpose.ACTIVATION,
                    OtpCode.consumed_at.is_(None))
            .count())
    assert live == 1


# ------------------------------------------------------------------------------
# Re-notification
#
# The de-dupe that protects the admin queue used to be permanent: once a pending
# row existed, no further email was ever sent for that address. A request made
# while email was misconfigured therefore stayed unannounced forever, and
# resubmitting -- the obvious remedy -- silently did nothing.
# ------------------------------------------------------------------------------

def _requests_mailed(outbox):
    return [m for m in outbox if m["subject"].startswith("New examiner access request")]


@pytest.fixture
def admin_inbox(monkeypatch):
    """Configure the shared operational mailbox these notifications go to.

    Without it the tests below exercise the no-recipient path instead of the
    one under test -- which is itself worth knowing, and is covered separately.
    """
    from app.core.config import settings

    monkeypatch.setattr(settings, "ADMIN_NOTIFICATION_EMAIL", "ops@institute.edu")


def test_a_first_submission_notifies_the_admin(client, seed_roles, email_on, outbox, admin_inbox):
    submit(client)
    assert len(_requests_mailed(outbox)) == 1


def test_a_double_click_does_not_send_two_emails(client, seed_roles, email_on, outbox, admin_inbox):
    """The behaviour the de-dupe exists for, and which must survive the fix."""
    submit(client)
    submit(client)
    submit(client)
    assert len(_requests_mailed(outbox)) == 1


def test_resubmitting_after_the_cooldown_notifies_again(client, seed_roles, db_session,
                                                        email_on, outbox, admin_inbox):
    """The bug: this used to stay at one email forever.

    Someone whose first request was submitted while SMTP was misconfigured had
    no way to make the notification happen -- the queue showed the request, the
    inbox never did, and submitting again did nothing at all.
    """
    from datetime import timedelta

    from app.models.access_request import AccessRequest

    submit(client)
    assert len(_requests_mailed(outbox)) == 1

    # Wind the clock back past the cooldown, as if this were an hour later.
    row = db_session.query(AccessRequest).filter(AccessRequest.email == VALID["email"]).one()
    row.last_notified_at = row.last_notified_at - timedelta(hours=1)
    db_session.commit()

    submit(client)
    assert len(_requests_mailed(outbox)) == 2
    # Still exactly one request in the queue -- re-notifying must not duplicate
    # the row an admin has to review.
    assert db_session.query(AccessRequest).count() == 1


def test_a_request_that_was_never_notified_gets_notified(client, seed_roles, db_session,
                                                         email_on, outbox, admin_inbox):
    """NULL means 'never told', not 'told at the beginning of time'.

    This is the row migration 0027 is written for: a request already sitting in
    the queue from before the column existed, or one whose notification failed.
    It must break out of the permanent silence, not stay in it.
    """
    from app.models.access_request import AccessRequest

    submit(client)
    row = db_session.query(AccessRequest).filter(AccessRequest.email == VALID["email"]).one()
    row.last_notified_at = None
    db_session.commit()
    outbox.clear()

    submit(client)
    assert len(_requests_mailed(outbox)) == 1


def test_no_admin_recipient_is_logged_rather_than_silently_dropped(client, seed_roles, db_session,
                                                                   email_on, outbox, monkeypatch, caplog):
    """Silence here looks exactly like the platform working.

    With no ADMIN_NOTIFICATION_EMAIL and no admin account, the request is still
    recorded -- but somebody has to be able to find out that nobody was told.
    """
    import logging

    from app.core.config import settings
    from app.services import access_request_service

    monkeypatch.setattr(settings, "ADMIN_NOTIFICATION_EMAIL", "")
    monkeypatch.setattr(access_request_service.user_repository, "list_admin_emails", lambda db: [])

    with caplog.at_level(logging.WARNING, logger="app"):
        submit(client)

    assert _requests_mailed(outbox) == []
    assert any("no admin recipient is configured" in r.message for r in caplog.records)
