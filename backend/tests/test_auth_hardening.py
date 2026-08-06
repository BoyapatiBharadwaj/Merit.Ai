"""
The authentication defects, each pinned by the property it violated.

Grouped by what an attacker or an unlucky candidate could do before:

  * Create an account without ever proving they own the email address, because
    both registration endpoints were public and only one checked a code.
  * Keep using a stolen token after the owner reset the password -- the action
    everyone is told to take when they suspect a compromise, which did nothing.
  * Learn which email addresses have accounts by timing the login endpoint.
  * Lock an entire exam hall out of logging in with ten wrong passwords, because
    behind the proxy every candidate counted as one IP.
  * Burn their own valid signup code on a typo in the student ID field.
  * Register with a name of nothing but spaces, or a password of `aaaaaaaa`
    while the screen promised uppercase, numbers and symbols were required.

Several of these need settings that differ from the suite's defaults, so they
set them explicitly rather than relying on the ambient configuration.
"""
import time

import pytest
from starlette.requests import Request

from app.core import passwords, rate_limit
from app.core.config import settings
from tests.conftest import auth_headers
from tests.test_exam_workflow import _create_examiner_and_login, _register_student_and_login

GOOD_PASSWORD = "Ur5aMinor!Lab"


def _signup_body(**overrides):
    body = {
        "first_name": "Aisha", "last_name": "Khan",
        "email": "aisha@example.com", "password": GOOD_PASSWORD,
    }
    body.update(overrides)
    return body


# --- the verification bypass --------------------------------------------------

def test_unverified_registration_is_refused_when_verification_is_required(client, seed_roles, monkeypatch):
    """The bypass, exactly as it was reachable: POST straight to the endpoint the
    frontend's OTP flow was supposed to protect."""
    monkeypatch.setattr(settings, "REQUIRE_EMAIL_VERIFICATION", True)

    response = client.post("/api/v1/auth/register/student", json=_signup_body())
    assert response.status_code == 403
    assert "verification" in response.json()["detail"].lower()


def test_unverified_registration_still_works_when_verification_is_off(client, seed_roles, monkeypatch):
    """The setting has to be a real switch, not a one-way removal: an offline
    lab with no SMTP must still be able to enrol candidates."""
    monkeypatch.setattr(settings, "REQUIRE_EMAIL_VERIFICATION", False)
    response = client.post("/api/v1/auth/register/student", json=_signup_body())
    assert response.status_code == 201, response.text


def test_a_deployment_cannot_require_verification_it_cannot_perform(monkeypatch):
    """Verification required + email disabled means nobody can ever register.
    Two settings that are each individually fine and together lock the door."""
    from app.core.config import Settings

    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("SECRET_KEY", "x" * 64)
    monkeypatch.setenv("CORS_ORIGINS", "https://exams.example.com")
    monkeypatch.setenv("REQUIRE_EMAIL_VERIFICATION", "true")
    monkeypatch.setenv("EMAIL_ENABLED", "false")

    with pytest.raises(ValueError) as caught:
        Settings()
    assert "REQUIRE_EMAIL_VERIFICATION" in str(caught.value)


def test_only_the_verified_path_marks_the_address_verified(client, seed_roles, db_session, monkeypatch):
    monkeypatch.setattr(settings, "REQUIRE_EMAIL_VERIFICATION", False)
    client.post("/api/v1/auth/register/student", json=_signup_body(email="plain@example.com"))

    from app.models.user import User
    user = db_session.query(User).filter(User.email == "plain@example.com").first()
    assert user.email_verified_at is None, "an unverified signup was recorded as verified"
    assert user.is_email_verified is False


def test_changing_an_email_drops_its_verification(db_session, seed_roles):
    """Otherwise: verify an address you control, switch to one you do not, keep
    the verified badge on it. That is the whole manoeuvre verification exists to
    stop."""
    from datetime import datetime, timezone
    from app.models.enums import RoleName
    from app.models.user import Role, User

    role = db_session.query(Role).filter(Role.name == RoleName.STUDENT.value).first()
    user = User(email="before@example.com", hashed_password="x", role_id=role.id)
    user.set_name("A", "B")
    user.email_verified_at = datetime.now(timezone.utc)
    db_session.add(user)
    db_session.commit()

    user.set_email("After@Example.com")
    assert user.email == "after@example.com", "the new address was not normalised"
    assert user.email_verified_at is None, "verification survived an email change"


def test_setting_the_same_email_again_keeps_the_verification(db_session, seed_roles):
    """A profile save that does not actually change the address must not quietly
    un-verify someone."""
    from datetime import datetime, timezone
    from app.models.enums import RoleName
    from app.models.user import Role, User

    role = db_session.query(Role).filter(Role.name == RoleName.STUDENT.value).first()
    user = User(email="same@example.com", hashed_password="x", role_id=role.id)
    user.set_name("A", "B")
    user.email_verified_at = datetime.now(timezone.utc)
    db_session.add(user)
    db_session.commit()

    user.set_email("SAME@example.com")  # same address, different case
    assert user.email_verified_at is not None


# --- the OTP that got burnt for nothing ---------------------------------------

def _issue_signup_code(client, db_session, monkeypatch, email):
    """Request a real code and read it out of the log the service writes when
    email is disabled."""
    from app.models.otp import OtpPurpose
    from app.services import otp_service

    captured = {}
    real_digest = otp_service._digest

    def _capture(code):
        captured["code"] = code
        return real_digest(code)

    monkeypatch.setattr(otp_service, "_digest", _capture)
    monkeypatch.setattr(otp_service.email_service, "is_enabled", lambda: True)
    monkeypatch.setattr(otp_service.email_service, "send", lambda **kw: True)

    otp_service.request_code(db_session, email=email, purpose=OtpPurpose.SIGNUP, background=None)
    return captured["code"]


def test_a_failed_registration_now_does_burn_the_code(client, seed_roles, db_session, monkeypatch):
    """Rewritten rather than deleted -- the behaviour this pins flipped on
    purpose, and a silently-dropped test would leave nothing watching it.

    Signup/password-reset OTP state moved to Redis (see
    app/services/otp_redis_store.py), and that rewrite's brief is explicit:
    OTP data is deleted immediately on a successful verification, with no
    "hold the consumption open until an outer transaction commits" option --
    Redis has no transaction to defer into the way the Postgres-backed
    version (`verify_code(..., commit=False)`) did. The consequence is this
    test's old name: a registration that verifies its code correctly but then
    fails for an unrelated reason (a duplicate student ID) now DOES lose the
    code, and the candidate must request a fresh one to retry. That is a
    real, documented regression from the previous behaviour, accepted
    because the new storage backend's one-time-use guarantee is stricter, not
    because the trade-off is free.
    """
    from app.services import email_service
    monkeypatch.setattr(email_service, "is_enabled", lambda: True)
    monkeypatch.setattr(settings, "REQUIRE_EMAIL_VERIFICATION", False)

    # Someone already holds this student ID.
    client.post("/api/v1/auth/register/student",
                json=_signup_body(email="first@example.com", roll_number="CS-2026-001"))

    code = _issue_signup_code(client, db_session, monkeypatch, "second@example.com")

    # The candidate's registration fails on the duplicate ID...
    failed = client.post("/api/v1/auth/register/student/verified", json=_signup_body(
        email="second@example.com", roll_number="CS-2026-001", code=code))
    assert failed.status_code == 409, failed.text

    # ...and their code is gone with it -- the same code cannot be retried,
    # even against a corrected student ID.
    retried = client.post("/api/v1/auth/register/student/verified", json=_signup_body(
        email="second@example.com", roll_number="CS-2026-002", code=code))
    assert retried.status_code == 400, "expected the already-verified code to be burned"


def test_a_successful_verified_registration_consumes_the_code(client, seed_roles, db_session, monkeypatch):
    """The other half: a code must be spendable exactly once."""
    from app.services import email_service
    monkeypatch.setattr(email_service, "is_enabled", lambda: True)

    code = _issue_signup_code(client, db_session, monkeypatch, "once@example.com")
    first = client.post("/api/v1/auth/register/student/verified",
                        json=_signup_body(email="once@example.com", code=code))
    assert first.status_code == 201, first.text

    second = client.post("/api/v1/auth/register/student/verified",
                         json=_signup_body(email="once@example.com", code=code))
    assert second.status_code == 400


def test_a_verified_registration_records_the_verification(client, seed_roles, db_session, monkeypatch):
    from app.models.user import User
    from app.services import email_service
    monkeypatch.setattr(email_service, "is_enabled", lambda: True)

    code = _issue_signup_code(client, db_session, monkeypatch, "proven@example.com")
    client.post("/api/v1/auth/register/student/verified",
                json=_signup_body(email="proven@example.com", code=code))

    user = db_session.query(User).filter(User.email == "proven@example.com").first()
    assert user.email_verified_at is not None


# --- tokens that outlived the password ----------------------------------------

def test_a_token_stops_working_once_the_password_is_reset_by_an_admin(client, seed_roles, admin_token):
    """The one that matters. "Reset your password" is what everyone is told to
    do when they think they have been compromised, and the attacker's token kept
    working for up to two hours afterwards."""
    stolen = _register_student_and_login(client, email="victim@example.com")
    assert client.get("/api/v1/users/me", headers=auth_headers(stolen)).status_code == 200

    students = client.get("/api/v1/users/students", headers=auth_headers(admin_token)).json()
    target = next(s for s in students if s["email"] == "victim@example.com")
    client.post(f"/api/v1/users/{target['user_id']}/reset-password",
                json={"new_password": "N3wPhrase!Here"}, headers=auth_headers(admin_token))

    after = client.get("/api/v1/users/me", headers=auth_headers(stolen))
    assert after.status_code == 401, "the stolen token survived the password reset"
    assert "password was changed" in after.json()["detail"].lower()


def test_a_token_stops_working_after_a_self_service_change(client, seed_roles, admin_token):
    token = _create_examiner_and_login(client, admin_token)
    assert client.get("/api/v1/users/me", headers=auth_headers(token)).status_code == 200

    # "Sup3rSecret!123" is the password _create_examiner_and_login sets while
    # completing the account's activation link -- there is no admin-chosen
    # password anymore to assert against (see examiner_provisioning_service).
    changed = client.post("/api/v1/users/me/password", json={
        "current_password": "Sup3rSecret!123", "new_password": "An0therGoodOne!",
    }, headers=auth_headers(token))
    assert changed.status_code == 200, changed.text

    assert client.get("/api/v1/users/me", headers=auth_headers(token)).status_code == 401


def test_a_token_stops_working_after_an_emailed_reset(client, seed_roles, db_session, monkeypatch):
    from app.models.otp import OtpPurpose
    from app.services import email_service, otp_service
    monkeypatch.setattr(email_service, "is_enabled", lambda: True)

    token = _register_student_and_login(client, email="forgot@example.com")

    captured = {}
    real_digest = otp_service._digest
    monkeypatch.setattr(otp_service, "_digest",
                        lambda code: (captured.setdefault("code", code), real_digest(code))[1])
    monkeypatch.setattr(otp_service.email_service, "send", lambda **kw: True)
    otp_service.request_code(db_session, email="forgot@example.com",
                             purpose=OtpPurpose.PASSWORD_RESET, background=None)

    reset = client.post("/api/v1/auth/password-reset/confirm", json={
        "email": "forgot@example.com", "code": captured["code"], "new_password": "R3coveredPhrase!",
    })
    assert reset.status_code == 200, reset.text

    assert client.get("/api/v1/users/me", headers=auth_headers(token)).status_code == 401


def test_the_new_token_issued_after_a_change_works(client, seed_roles, admin_token):
    """The revocation must not be so blunt that it also invalidates the session
    the user is about to be given."""
    students_before = _register_student_and_login(client, email="renew@example.com")
    assert students_before

    students = client.get("/api/v1/users/students", headers=auth_headers(admin_token)).json()
    target = next(s for s in students if s["email"] == "renew@example.com")
    client.post(f"/api/v1/users/{target['user_id']}/reset-password",
                json={"new_password": "Fr3shStart!Now"}, headers=auth_headers(admin_token))

    fresh = client.post("/api/v1/auth/login", json={
        "email": "renew@example.com", "password": "Fr3shStart!Now"})
    assert fresh.status_code == 200
    assert client.get("/api/v1/users/me",
                      headers=auth_headers(fresh.json()["access_token"])).status_code == 200


def test_an_unrelated_users_token_is_unaffected(client, seed_roles, admin_token):
    """The epoch is per account, not global. A reset must not sign out the
    whole institution."""
    bystander = _register_student_and_login(client, email="bystander@example.com")
    _register_student_and_login(client, email="reset-me@example.com")

    students = client.get("/api/v1/users/students", headers=auth_headers(admin_token)).json()
    target = next(s for s in students if s["email"] == "reset-me@example.com")
    client.post(f"/api/v1/users/{target['user_id']}/reset-password",
                json={"new_password": "S0meoneElse!Pass"}, headers=auth_headers(admin_token))

    assert client.get("/api/v1/users/me", headers=auth_headers(bystander)).status_code == 200


# --- the login timing oracle --------------------------------------------------

def test_an_unknown_address_still_pays_the_bcrypt_cost(client, seed_roles, monkeypatch):
    """`if not user or not verify_password(...)` short-circuits, so an unknown
    address answered in about a millisecond and a real one spent ~100ms hashing
    first. That difference is remotely measurable and turns the login form into
    a "does this person have an account here?" oracle -- which, on an exam
    platform, leaks the roster.

    Counting the calls rather than timing them: wall-clock assertions in CI are
    flaky, and the property that matters is that the hash runs on both paths.
    """
    from app.services import auth_service

    calls = []
    real_verify = auth_service.verify_password
    monkeypatch.setattr(auth_service, "verify_password",
                        lambda plain, hashed: (calls.append(hashed), real_verify(plain, hashed))[1])

    _register_student_and_login(client, email="known@example.com")
    calls.clear()

    client.post("/api/v1/auth/login", json={"email": "known@example.com", "password": "wrong"})
    known_calls = len(calls)
    calls.clear()

    client.post("/api/v1/auth/login", json={"email": "nobody@example.com", "password": "wrong"})
    unknown_calls = len(calls)

    assert unknown_calls == known_calls == 1, (
        "the unknown-address branch skipped password verification, so it answers faster"
    )


def test_both_branches_return_the_same_message(client, seed_roles):
    _register_student_and_login(client, email="present@example.com")
    a = client.post("/api/v1/auth/login", json={"email": "present@example.com", "password": "wrong"})
    b = client.post("/api/v1/auth/login", json={"email": "absent@example.com", "password": "wrong"})
    assert a.status_code == b.status_code == 401
    assert a.json() == b.json()


# --- rate limiting behind the proxy -------------------------------------------

def _request_from(peer: str, forwarded: str | None = None) -> Request:
    headers = [(b"x-forwarded-for", forwarded.encode())] if forwarded else []
    return Request({
        "type": "http", "http_version": "1.1", "method": "POST", "path": "/",
        "scheme": "http", "server": ("test", 80), "query_string": b"",
        "headers": headers, "client": (peer, 40000),
    })


def test_a_forwarded_address_is_believed_from_the_proxy_network(monkeypatch):
    """Behind nginx the TCP peer is the proxy container, so without this every
    candidate in the building shares one login budget."""
    monkeypatch.setattr(settings, "TRUSTED_PROXY_IPS", "172.16.0.0/12")
    rate_limit._is_trusted_proxy.cache_clear()

    assert rate_limit._client_ip(_request_from("172.20.0.5", "203.0.113.7")) == "203.0.113.7"


def test_a_forwarded_address_is_ignored_from_an_untrusted_peer(monkeypatch):
    """The dangerous half. Believing the header unconditionally would hand an
    attacker a fresh budget per request for the price of one header."""
    monkeypatch.setattr(settings, "TRUSTED_PROXY_IPS", "172.16.0.0/12")
    rate_limit._is_trusted_proxy.cache_clear()

    assert rate_limit._client_ip(_request_from("203.0.113.9", "1.2.3.4")) == "203.0.113.9"


def test_a_client_supplied_hop_cannot_impersonate_another_address(monkeypatch):
    """A client that sends its own X-Forwarded-For prepends to the chain, so the
    leftmost entry is whatever it chose. Only the rightmost non-proxy entry was
    actually observed by our own infrastructure."""
    monkeypatch.setattr(settings, "TRUSTED_PROXY_IPS", "172.16.0.0/12")
    rate_limit._is_trusted_proxy.cache_clear()

    spoofed = rate_limit._client_ip(_request_from("172.20.0.5", "9.9.9.9, 203.0.113.7"))
    assert spoofed == "203.0.113.7", "the client's own forged hop was used as the identity"


def test_trusting_nobody_falls_back_to_the_peer(monkeypatch):
    monkeypatch.setattr(settings, "TRUSTED_PROXY_IPS", "")
    rate_limit._is_trusted_proxy.cache_clear()

    assert rate_limit._client_ip(_request_from("172.20.0.5", "203.0.113.7")) == "172.20.0.5"


def test_two_candidates_behind_one_proxy_get_separate_budgets(monkeypatch):
    """The behaviour the whole fix is for: one candidate exhausting the login
    limit must not lock out the person sitting next to them."""
    monkeypatch.setattr(settings, "TRUSTED_PROXY_IPS", "172.16.0.0/12")
    rate_limit._is_trusted_proxy.cache_clear()
    rate_limit._reset_all()
    monkeypatch.setattr(settings, "TRUSTED_PROXY_IPS", "172.16.0.0/12")

    first = rate_limit._client_ip(_request_from("172.20.0.5", "203.0.113.7"))
    second = rate_limit._client_ip(_request_from("172.20.0.5", "203.0.113.8"))
    assert first != second

    for _ in range(5):
        rate_limit.consume("login", first, limit=5, window=60)
    with pytest.raises(Exception) as caught:
        rate_limit.consume("login", first, limit=5, window=60)
    assert caught.value.status_code == 429

    rate_limit.consume("login", second, limit=5, window=60)  # must not raise


def test_a_malformed_forwarded_header_does_not_break_the_request(monkeypatch):
    monkeypatch.setattr(settings, "TRUSTED_PROXY_IPS", "172.16.0.0/12")
    rate_limit._is_trusted_proxy.cache_clear()

    assert rate_limit._client_ip(_request_from("172.20.0.5", "not-an-ip")) == "not-an-ip"
    assert rate_limit._client_ip(_request_from("172.20.0.5", " , , ")) == "172.20.0.5"


# --- duplicate student ID -----------------------------------------------------

def test_a_duplicate_student_id_returns_409_not_503(client, seed_roles, monkeypatch):
    """students.roll_number is unique but only the email was checked, so a
    repeated ID reached the INSERT and surfaced as a generic 503 -- telling the
    candidate the server was broken when one field was wrong."""
    monkeypatch.setattr(settings, "REQUIRE_EMAIL_VERIFICATION", False)
    client.post("/api/v1/auth/register/student",
                json=_signup_body(email="one@example.com", roll_number="ME-42"))

    clash = client.post("/api/v1/auth/register/student",
                        json=_signup_body(email="two@example.com", roll_number="ME-42"))
    assert clash.status_code == 409, clash.text
    assert "student id" in clash.json()["detail"].lower()


def test_a_blank_student_id_is_stored_as_absent_not_empty(client, seed_roles, db_session, monkeypatch):
    """Otherwise the second candidate to leave the field blank collides with the
    first on an empty string."""
    monkeypatch.setattr(settings, "REQUIRE_EMAIL_VERIFICATION", False)
    for email in ("blank1@example.com", "blank2@example.com"):
        response = client.post("/api/v1/auth/register/student",
                               json=_signup_body(email=email, roll_number="   "))
        assert response.status_code == 201, response.text


# --- input normalisation ------------------------------------------------------

def test_a_name_of_only_spaces_is_rejected(client, seed_roles, monkeypatch):
    """min_length=1 measures the raw string, so "   " passed and set_name then
    stripped it to empty -- a real account with no name, which every downstream
    reader inherits."""
    monkeypatch.setattr(settings, "REQUIRE_EMAIL_VERIFICATION", False)
    response = client.post("/api/v1/auth/register/student", json=_signup_body(first_name="   "))
    assert response.status_code == 422


def test_names_are_trimmed_and_internal_whitespace_collapsed(client, seed_roles, db_session, monkeypatch):
    monkeypatch.setattr(settings, "REQUIRE_EMAIL_VERIFICATION", False)
    client.post("/api/v1/auth/register/student", json=_signup_body(
        first_name="  Mary   Jane ", last_name=" Watson ", email="mj@example.com"))

    from app.models.user import User
    user = db_session.query(User).filter(User.email == "mj@example.com").first()
    assert user.first_name == "Mary Jane"
    assert user.full_name == "Mary Jane Watson"


def test_legitimate_unicode_names_are_accepted(client, seed_roles, monkeypatch):
    """A validator that "sanitised" names would reject the people this platform
    is being built for."""
    monkeypatch.setattr(settings, "REQUIRE_EMAIL_VERIFICATION", False)
    for first, last, email in [
        ("Siobhán", "O'Neill", "so@example.com"),
        ("ಸಂಧ್ಯಾ", "ರಾವ್", "sr@example.com"),
        ("Jean-Luc", "Sagar-Fenton", "jl@example.com"),
    ]:
        response = client.post("/api/v1/auth/register/student", json=_signup_body(
            first_name=first, last_name=last, email=email))
        assert response.status_code == 201, f"{first} {last} was rejected: {response.text}"


def test_an_email_longer_than_the_column_is_a_422(client, seed_roles, monkeypatch):
    monkeypatch.setattr(settings, "REQUIRE_EMAIL_VERIFICATION", False)
    long_email = ("a" * 140) + "@example.com"  # 152 characters, column is 150
    response = client.post("/api/v1/auth/register/student", json=_signup_body(email=long_email))
    assert response.status_code == 422


def test_emails_are_stored_lowercased(client, seed_roles, db_session, monkeypatch):
    monkeypatch.setattr(settings, "REQUIRE_EMAIL_VERIFICATION", False)
    client.post("/api/v1/auth/register/student", json=_signup_body(email="  MiXeD@Example.COM "))

    from app.models.user import User
    assert db_session.query(User).filter(User.email == "mixed@example.com").first() is not None


def test_registering_the_same_address_in_a_different_case_is_a_duplicate(client, seed_roles, monkeypatch):
    monkeypatch.setattr(settings, "REQUIRE_EMAIL_VERIFICATION", False)
    client.post("/api/v1/auth/register/student", json=_signup_body(email="dup@example.com"))
    again = client.post("/api/v1/auth/register/student", json=_signup_body(email="DUP@Example.com"))
    assert again.status_code == 400


# --- the password policy ------------------------------------------------------

@pytest.mark.parametrize("password,why", [
    ("aaaaaaaa", "eight characters of one letter -- the exact value the UI implied was refused"),
    ("Password1!", "satisfies every classic composition rule and is in every wordlist"),
    ("passw0rd123", "a common password with digits stapled on"),
    ("abcdefghij", "a straight alphabetical run"),
    ("abababababab", "one unit repeated"),
    ("short1!", "under the length floor"),
])
def test_weak_passwords_are_refused_at_signup(client, seed_roles, monkeypatch, password, why):
    monkeypatch.setattr(settings, "REQUIRE_EMAIL_VERIFICATION", False)
    response = client.post("/api/v1/auth/register/student", json=_signup_body(password=password))
    assert response.status_code == 422, f"accepted a weak password ({why})"


def test_a_passphrase_is_accepted(client, seed_roles, monkeypatch):
    """The policy has to leave a good option open, or it just teaches people to
    append `1!` to a short word."""
    monkeypatch.setattr(settings, "REQUIRE_EMAIL_VERIFICATION", False)
    response = client.post("/api/v1/auth/register/student",
                           json=_signup_body(password="correct horse battery staple"))
    assert response.status_code == 201, response.text


def test_a_password_built_from_the_email_is_refused(client, seed_roles, monkeypatch):
    monkeypatch.setattr(settings, "REQUIRE_EMAIL_VERIFICATION", False)
    response = client.post("/api/v1/auth/register/student",
                           json=_signup_body(email="sandhya@example.com", password="sandhya2005"))
    assert response.status_code == 400


def test_a_password_merely_containing_a_common_word_is_fine(client, seed_roles, monkeypatch):
    """The context rule must not be so blunt that it rejects good passwords.
    `Sup3rSecret!` is not derived from secret@example.com, and telling that
    candidate to think again would be the validator being wrong at them."""
    assert passwords.check_with_context("Sup3rSecret!", email="secret@example.com") is None


def test_examiner_creation_no_longer_accepts_a_password_at_all(client, seed_roles, admin_token):
    """POST /auth/examiners used to take an admin-chosen password, and the
    password policy applied to it right there. That field is gone -- the
    account is created with an unusable random secret and its owner chooses a
    password later, through activation (see the next test) -- so an old
    caller still sending one gets it silently ignored rather than rejected;
    there is nothing left here for a password policy to apply to.
    """
    response = client.post("/api/v1/auth/examiners", json={
        "first_name": "New", "last_name": "Examiner", "email": "weak@example.com",
        "organization_name": "Dept", "password": "aaaaaaaa",
    }, headers=auth_headers(admin_token))
    assert response.status_code == 201, response.text
    assert "password" not in response.json()


def test_the_policy_applies_when_an_examiner_activates_their_account(client, seed_roles, admin_token,
                                                                      outbox, email_on):
    """The password policy's new home for this flow: the activation link is
    where an examiner's password is actually chosen."""
    import re
    from urllib.parse import unquote

    client.post("/api/v1/auth/examiners", json={
        "first_name": "New", "last_name": "Examiner", "email": "weak2@example.com",
        "organization_name": "Dept",
    }, headers=auth_headers(admin_token))
    message = next(m for m in reversed(outbox) if m["to"] == "weak2@example.com")
    token = unquote(re.search(r"[?&]token=([A-Za-z0-9_\-%]+)", message["text"]).group(1))

    response = client.post("/api/v1/auth/activate", json={
        "email": "weak2@example.com", "token": token, "password": "aaaaaaaa",
    })
    assert response.status_code == 422


def test_the_policy_applies_to_an_admin_reset(client, seed_roles, admin_token):
    _register_student_and_login(client, email="target2@example.com")
    students = client.get("/api/v1/users/students", headers=auth_headers(admin_token)).json()
    target = next(s for s in students if s["email"] == "target2@example.com")

    response = client.post(f"/api/v1/users/{target['user_id']}/reset-password",
                           json={"new_password": "password123"}, headers=auth_headers(admin_token))
    assert response.status_code == 422


def test_the_policy_applies_to_a_self_service_change(client, seed_roles, admin_token):
    token = _create_examiner_and_login(client, admin_token)
    response = client.post("/api/v1/users/me/password", json={
        "current_password": "Sup3rSecret!123", "new_password": "aaaaaaaa",
    }, headers=auth_headers(token))
    assert response.status_code == 422


def test_a_password_past_bcrypts_limit_is_refused(client, seed_roles, monkeypatch):
    """bcrypt discards everything past 72 bytes, so without this two different
    long passwords would hash identically and both open the account."""
    monkeypatch.setattr(settings, "REQUIRE_EMAIL_VERIFICATION", False)
    response = client.post("/api/v1/auth/register/student",
                           json=_signup_body(password="a-long-but-varied-phrase-" * 5))
    assert response.status_code == 422


def test_the_policy_is_published_for_the_signup_form(client):
    """The UI listed uppercase/number/special from memory while the server
    checked only length. Serving the rules removes the copy that drifted."""
    response = client.get("/api/v1/auth/password-policy")
    assert response.status_code == 200
    body = response.json()
    assert body["min_length"] == settings.PASSWORD_MIN_LENGTH
    assert any(str(settings.PASSWORD_MIN_LENGTH) in rule for rule in body["rules"])


def test_the_otp_response_describes_its_own_code(client, seed_roles, monkeypatch):
    """The signup screen hard-coded a six-digit placeholder and a 60-second
    resend cooldown, neither of which tracked the settings they described."""
    from app.services import email_service
    monkeypatch.setattr(email_service, "is_enabled", lambda: True)
    monkeypatch.setattr(email_service, "send", lambda **kw: True)

    response = client.post("/api/v1/auth/otp/signup/request", json={"email": "shape@example.com"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["code_length"] == settings.OTP_LENGTH
    assert body["resend_after_seconds"] == settings.OTP_RESEND_COOLDOWN_SECONDS
    assert body["expires_in_minutes"] == settings.OTP_TTL_MINUTES


# --- consent ------------------------------------------------------------------

def test_registration_is_refused_without_consent(client, seed_roles, monkeypatch):
    """The two checkboxes were enforced entirely in React and never sent, so any
    direct API call created an account without agreeing to anything -- on a
    platform that collects a face photo and an ID card image."""
    monkeypatch.setattr(settings, "REQUIRE_EMAIL_VERIFICATION", False)
    monkeypatch.setattr(settings, "REQUIRE_CONSENT_ON_SIGNUP", True)

    response = client.post("/api/v1/auth/register/student", json=_signup_body())
    assert response.status_code == 400
    assert "accept" in response.json()["detail"].lower()


def test_partial_consent_is_still_refused(client, seed_roles, monkeypatch):
    """Agreeing to the terms is not agreeing to be recorded. They are separate
    checkboxes on the form for that reason, and must be separate here."""
    monkeypatch.setattr(settings, "REQUIRE_EMAIL_VERIFICATION", False)
    monkeypatch.setattr(settings, "REQUIRE_CONSENT_ON_SIGNUP", True)

    response = client.post("/api/v1/auth/register/student",
                           json=_signup_body(accepted_terms=True, accepted_proctoring=False))
    assert response.status_code == 400


def test_consent_is_recorded_with_its_version(client, seed_roles, db_session, monkeypatch):
    """Nothing recorded that a candidate had agreed, when, or which wording they
    saw -- so a later dispute had no record to appeal to, and changing the terms
    would have applied retroactively to everyone."""
    monkeypatch.setattr(settings, "REQUIRE_EMAIL_VERIFICATION", False)
    monkeypatch.setattr(settings, "REQUIRE_CONSENT_ON_SIGNUP", True)

    response = client.post("/api/v1/auth/register/student", json=_signup_body(
        email="consenting@example.com", accepted_terms=True, accepted_proctoring=True,
        terms_version="2026-08-05"))
    assert response.status_code == 201, response.text

    from app.models.user import User
    user = db_session.query(User).filter(User.email == "consenting@example.com").first()
    assert user.terms_accepted_at is not None
    assert user.proctoring_consent_at is not None
    assert user.terms_version == "2026-08-05"
