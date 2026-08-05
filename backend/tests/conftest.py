"""
Pytest configuration for the backend test suite.

Uses an in-memory SQLite database (see the SQLite-specific engine options in
app/database/session.py) so the suite runs without a Postgres instance, and
overrides FastAPI's `get_db` dependency so every request made through the
TestClient uses a session bound to that database.
"""
import os
import tempfile

# These must be set before any `app.*` module is imported, since Settings is
# resolved (and lru_cache'd) at first import.
os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ.setdefault("SECRET_KEY", "test-secret-key-not-for-production")
os.environ.setdefault("CORS_ORIGINS", "http://testserver")
os.environ.setdefault("AI_SERVICE_URL", "")
os.environ.setdefault("UPLOAD_DIR", tempfile.mkdtemp(prefix="exam_proctor_test_uploads_"))
# The suite has no SMTP, so it IS the "cannot send email" deployment this
# setting exists for -- requiring verification here would mean no test could
# create a student, which would say nothing about verification and break
# everything else. The gate itself is covered directly in
# tests/test_auth_hardening.py, which turns it on and asserts the refusal.
os.environ.setdefault("REQUIRE_EMAIL_VERIFICATION", "false")
# Same reasoning: the suite's ~30 registrations exist to set up other tests, and
# threading two consent booleans through every one of them would obscure what
# each is actually about. The gate itself is covered directly in
# tests/test_auth_hardening.py, which turns it on and asserts both the refusal
# and that the acceptance is persisted with its version.
os.environ.setdefault("REQUIRE_CONSENT_ON_SIGNUP", "false")

import pytest
from fastapi.testclient import TestClient
from passlib.context import CryptContext

import app.models  # noqa: F401  registers every mapped model on Base.metadata
from app.core import security as _security
from app.core.rate_limit import _reset_all as _reset_rate_limit_counters

# Drop bcrypt's work factor to the minimum for tests only.
#
# bcrypt's cost is deliberately punishing -- that is the entire point of it in
# production -- but this suite creates and logs in users constantly, and at
# the default 12 rounds hashing dominated the runtime to the point where the
# full suite couldn't finish in a single CI step. Rounds are a property of
# each stored hash, so this changes nothing about the algorithm under test:
# hashes produced here still verify through exactly the same passlib code
# path, just cheaply. Production cost is untouched (app/core/security.py).
_security.pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto", bcrypt__rounds=4)

from app.core.security import hash_password  # noqa: E402  must follow the cost override above
from app.database.session import Base, SessionLocal, engine, get_db
from app.main import app as fastapi_app
from app.models.enums import RoleName
from app.models.user import Role, User


@pytest.fixture(autouse=True)
def _fresh_database():
    """Rebuild the schema before every test so state never leaks between tests."""
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture(autouse=True)
def _reset_rate_limits():
    """Starlette's TestClient sends every request from the same fake source
    IP for the life of the pytest process, so the per-IP counters in
    app/core/rate_limit.py would otherwise accumulate across unrelated test
    functions and start rejecting their login/register calls with spurious
    429s. Reset before every test, same rationale as _fresh_database above."""
    _reset_rate_limit_counters()
    yield


@pytest.fixture
def db_session():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client():
    def _override_get_db():
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    fastapi_app.dependency_overrides[get_db] = _override_get_db
    with TestClient(fastapi_app) as test_client:
        yield test_client
    fastapi_app.dependency_overrides.clear()


@pytest.fixture
def seed_roles(db_session):
    for role_name in (RoleName.ADMIN.value, RoleName.EXAMINER.value, RoleName.STUDENT.value):
        if not db_session.query(Role).filter(Role.name == role_name).first():
            db_session.add(Role(name=role_name))
    db_session.commit()


@pytest.fixture
def admin_token(client, seed_roles, db_session):
    admin_role = db_session.query(Role).filter(Role.name == RoleName.ADMIN.value).first()
    admin = User(
        email="admin@example.com",
        hashed_password=hash_password("Admin@12345"),
        role_id=admin_role.id,
    )
    admin.set_name("Test", "Admin")
    db_session.add(admin)
    db_session.commit()

    response = client.post("/api/v1/auth/login", json={"email": "admin@example.com", "password": "Admin@12345"})
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}
