"""Auth flow: registration, login, and admin-gated examiner creation."""
from tests.conftest import auth_headers


def register_student(client, email, first="Ada", last="Lovelace", password="Sup3rSecret!", roll_number=None):
    payload = {"first_name": first, "last_name": last, "email": email, "password": password}
    if roll_number:
        payload["roll_number"] = roll_number
    return client.post("/api/v1/auth/register/student", json=payload)


def test_register_student_success(client, seed_roles):
    response = register_student(client, "ada@example.com", roll_number="R100")
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["role"] == "student"
    assert body["first_name"] == "Ada"
    assert body["last_name"] == "Lovelace"
    # full_name stays a derived convenience field for OCR/PDF consumers.
    assert body["full_name"] == "Ada Lovelace"
    assert body["access_token"]


def test_register_student_requires_both_name_parts(client, seed_roles):
    response = client.post("/api/v1/auth/register/student", json={
        "first_name": "Ada", "email": "nolast@example.com", "password": "Sup3rSecret!",
    })
    assert response.status_code == 422


def test_register_student_duplicate_email_is_rejected(client, seed_roles):
    assert register_student(client, "dupe@example.com").status_code == 201
    second = register_student(client, "dupe@example.com")
    assert second.status_code == 400
    assert "already registered" in second.json()["detail"].lower()


def test_login_with_wrong_password_is_rejected(client, seed_roles):
    register_student(client, "grace@example.com", "Grace", "Hopper", password="Correct-Horse1")
    response = client.post("/api/v1/auth/login", json={"email": "grace@example.com", "password": "wrong-password"})
    assert response.status_code == 401


def test_login_with_unknown_email_is_rejected(client, seed_roles):
    response = client.post("/api/v1/auth/login", json={"email": "nobody@example.com", "password": "whatever123"})
    assert response.status_code == 401


def test_examiner_creation_requires_admin(client, seed_roles):
    response = client.post("/api/v1/auth/examiners", json={
        "first_name": "Jane", "last_name": "Examiner", "email": "jane@example.com",
        "organization_name": "Acme Institute", "password": "Sup3rSecret!",
    })
    # No Authorization header at all -> unauthenticated, not authorized.
    assert response.status_code == 401


def test_admin_can_create_examiner(client, seed_roles, admin_token):
    """No password field: the account is created with an unusable random secret and activated
    later through a single-use emailed link -- see examiner_provisioning_service.
    """
    response = client.post(
        "/api/v1/auth/examiners",
        json={
            "first_name": "Jane", "last_name": "Examiner", "email": "jane@example.com",
            "organization_name": "Acme Institute",
        },
        headers=auth_headers(admin_token),
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["email"] == "jane@example.com"
    assert "password" not in body
    assert "access_token" not in body


def test_examiner_creation_requires_organization_name(client, seed_roles, admin_token):
    response = client.post(
        "/api/v1/auth/examiners",
        json={"first_name": "Jane", "last_name": "Examiner", "email": "j3@example.com", "password": "Sup3rSecret!"},
        headers=auth_headers(admin_token),
    )
    assert response.status_code == 422


def test_non_admin_cannot_create_examiner(client, seed_roles):
    student = register_student(client, "student@example.com", "Regular", "Student").json()
    response = client.post(
        "/api/v1/auth/examiners",
        json={
            "first_name": "Jane", "last_name": "Examiner", "email": "jane2@example.com",
            "organization_name": "Acme Institute", "password": "Sup3rSecret!",
        },
        headers=auth_headers(student["access_token"]),
    )
    assert response.status_code == 403
