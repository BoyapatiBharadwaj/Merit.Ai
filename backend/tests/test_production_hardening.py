"""
Production-hardening guarantees.

These cover the failure modes that are invisible in development and only bite
in a real deployment: booting with a forgeable signing key, missing security
headers, a load balancer that can't tell a broken instance from a healthy
one, and correctly-signed-but-malformed tokens crashing the auth dependency.
"""
import pytest
from jose import jwt

from app.core.config import PLACEHOLDER_SECRET_KEY, Settings, settings
from app.core.security import create_access_token
from tests.conftest import auth_headers
from tests.test_exam_workflow import _register_student_and_login


# --------------------------------------------------------------------------
# Configuration validation
# --------------------------------------------------------------------------

def test_production_refuses_to_boot_with_the_placeholder_secret():
    """The single highest-severity misconfiguration: the placeholder key is
    published in .env.example, so a deployment still using it accepts tokens
    forged by anyone who has read the repo -- including an admin token."""
    with pytest.raises(ValueError, match="insecure production configuration"):
        Settings(
            _env_file=None,
            ENVIRONMENT="production",
            SECRET_KEY=PLACEHOLDER_SECRET_KEY,
            CORS_ORIGINS="https://exams.example.com",
        )


def test_production_refuses_to_boot_with_a_short_secret():
    with pytest.raises(ValueError, match="at least 32"):
        Settings(
            _env_file=None,
            ENVIRONMENT="production",
            SECRET_KEY="tooshort",
            CORS_ORIGINS="https://exams.example.com",
        )


def test_production_refuses_to_boot_with_no_cors_origins():
    with pytest.raises(ValueError, match="CORS_ORIGINS is empty"):
        Settings(
            _env_file=None,
            ENVIRONMENT="production",
            SECRET_KEY="x" * 64,
            CORS_ORIGINS="",
        )


def test_production_boots_with_a_proper_secret():
    config = Settings(
        _env_file=None,
        ENVIRONMENT="production",
        SECRET_KEY="x" * 64,
        CORS_ORIGINS="https://exams.example.com",
    )
    assert config.is_production is True
    assert config.cors_origins == ["https://exams.example.com"]


def test_development_only_warns_about_the_placeholder(caplog):
    """A fresh clone must still run with zero setup -- the same problem that
    is fatal in production is a log line locally."""
    config = Settings(_env_file=None, ENVIRONMENT="development", SECRET_KEY=PLACEHOLDER_SECRET_KEY)
    assert config.is_production is False
    assert any("Insecure configuration" in record.message for record in caplog.records)


def test_a_wildcard_cors_origin_is_always_stripped():
    config = Settings(_env_file=None, SECRET_KEY="x" * 64, CORS_ORIGINS="*,https://real.example.com")
    assert config.cors_origins == ["https://real.example.com"]


# --------------------------------------------------------------------------
# Security headers
# --------------------------------------------------------------------------

def test_security_headers_are_present_on_every_response(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
    assert "camera=()" in response.headers["Permissions-Policy"]
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]


def test_every_response_carries_a_request_id(client):
    response = client.get("/api/health")
    assert response.headers.get("X-Request-ID")


def test_an_inbound_request_id_is_preserved_for_log_correlation(client):
    """So the id in a reverse proxy's log and the id in ours are the same."""
    response = client.get("/api/health", headers={"X-Request-ID": "abc123fromproxy"})
    assert response.headers["X-Request-ID"] == "abc123fromproxy"


# --------------------------------------------------------------------------
# Health / readiness
# --------------------------------------------------------------------------

def test_liveness_does_not_touch_the_database(client):
    """Liveness failures cause restarts, so a DB blip must not trigger one."""
    assert client.get("/api/health").json() == {"status": "ok"}


def test_readiness_reports_database_reachability(client):
    body = client.get("/api/health/ready").json()
    assert body == {"status": "ok", "database": "ok"}


# --------------------------------------------------------------------------
# Token handling
# --------------------------------------------------------------------------

def test_a_signed_token_with_a_non_numeric_subject_is_rejected_as_401(client, seed_roles):
    """Correctly signed but malformed. This used to raise ValueError out of
    the auth dependency and surface as a 500 -- wrong for the client, and
    noise in the error logs for what is a routine unauthenticated request."""
    token = create_access_token(subject="not-a-number", role="student")
    response = client.get("/api/v1/users/me", headers=auth_headers(token))
    assert response.status_code == 401


def test_a_signed_token_with_no_subject_is_rejected_as_401(client, seed_roles):
    token = jwt.encode({"role": "admin"}, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
    response = client.get("/api/v1/users/me", headers=auth_headers(token))
    assert response.status_code == 401


def test_a_token_signed_with_the_wrong_key_is_rejected(client, seed_roles):
    token = jwt.encode({"sub": "1", "role": "admin"}, "a-completely-different-key", algorithm="HS256")
    response = client.get("/api/v1/users/me", headers=auth_headers(token))
    assert response.status_code == 401


def test_a_deactivated_user_cannot_use_an_already_issued_token(client, seed_roles, admin_token, db_session):
    """Tokens are stateless, so deactivation has to be enforced on every
    request rather than at issue time."""
    from app.repositories import user_repository

    student_token = _register_student_and_login(client)
    assert client.get("/api/v1/users/me", headers=auth_headers(student_token)).status_code == 200

    user = user_repository.get_user_by_email(db_session, "student@example.com")
    client.post(f"/api/v1/users/{user.id}/deactivate", headers=auth_headers(admin_token))

    assert client.get("/api/v1/users/me", headers=auth_headers(student_token)).status_code == 401
