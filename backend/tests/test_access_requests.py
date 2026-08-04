"""
Examiner access request workflow: public submit, admin review, approval
minting a real examiner account.
"""
from tests.conftest import auth_headers
from tests.test_exam_workflow import _create_examiner_and_login, _register_student_and_login

VALID = {
    "first_name": "Jane",
    "last_name": "Doe",
    "email": "jane@institute.edu",
    "organization_name": "Acme Institute of Technology",
    "purpose": "Running end-of-semester programming assessments for around 300 students.",
}


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


def test_approving_creates_a_working_examiner_account(client, seed_roles, admin_token):
    submit(client)
    request_id = _first_request_id(client, admin_token)

    approved = client.post(
        f"/api/v1/access-requests/{request_id}/approve",
        json={"password": "Sup3rSecret!", "review_note": "Verified by phone."},
        headers=auth_headers(admin_token),
    )
    assert approved.status_code == 200, approved.text
    body = approved.json()
    assert body["status"] == "approved"
    assert body["created_user_id"] is not None

    # The requester can now actually log in as an examiner.
    login = client.post("/api/v1/auth/login", json={"email": VALID["email"], "password": "Sup3rSecret!"})
    assert login.status_code == 200, login.text
    assert login.json()["role"] == "examiner"
    assert login.json()["full_name"] == "Jane Doe"

    # ...and shows up in the examiner directory with their organization.
    examiners = client.get("/api/v1/users/examiners", headers=auth_headers(admin_token)).json()
    match = next(e for e in examiners if e["email"] == VALID["email"])
    assert match["organization_name"] == VALID["organization_name"]


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
