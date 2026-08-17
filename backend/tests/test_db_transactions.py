"""Database transaction behavior: failed multi-step writes must not leave partial rows behind,
get_db must roll back on error, and the background-task screenshot write path must not crash
the request if something goes wrong.
"""
import pytest
from fastapi import HTTPException

from app.database.session import SessionLocal, get_db
from app.models.enums import RoleName
from app.models.user import Role, User
from app.repositories import proctor_repository
from app.services import auth_service, proctor_service


def test_register_student_rolls_back_fully_on_duplicate_email(db_session, seed_roles):
    auth_service.register_student(db_session, "First", "User", "shared@example.com", "Sup3rSecret!", None)
    assert db_session.query(User).count() == 1

    with pytest.raises(HTTPException):
        auth_service.register_student(db_session, "Second", "User", "shared@example.com", "Sup3rSecret!", None)

    # The duplicate attempt must not have left a partial user/student row behind.
    assert db_session.query(User).count() == 1


def test_get_db_rolls_back_session_on_exception(db_session, seed_roles):
    role = db_session.query(Role).filter(Role.name == RoleName.STUDENT.value).first()
    gen = get_db()
    session = next(gen)
    orphan = User(email="orphan@example.com", hashed_password="x", role_id=role.id)
    orphan.set_name("Orphan", "User")
    session.add(orphan)
    session.flush()  # push the INSERT to the open transaction without committing it
    # Don't commit -- simulate a request that fails after adding to the session.
    with pytest.raises(RuntimeError):
        gen.throw(RuntimeError("simulated failure mid-request"))

    # A fresh session must not see the uncommitted, now-rolled-back row.
    fresh = SessionLocal()
    try:
        assert fresh.query(User).filter(User.email == "orphan@example.com").first() is None
    finally:
        fresh.close()


def test_set_event_screenshot_is_a_safe_no_op_for_unknown_event(db_session):
    # Should not raise even though no event with this id exists.
    proctor_repository.set_event_screenshot(db_session, event_id=999999, screenshot_path="uploads/violations/x.jpg")


def test_save_violation_screenshot_discards_invalid_base64():
    # Should log and return quietly rather than raising and breaking the
    # (fire-and-forget) background task.
    proctor_service._save_violation_screenshot(event_id=1, attempt_id=1, screenshot_base64="%%%not-base64%%%")


def test_save_violation_screenshot_discards_oversized_payload():
    import base64
    huge_base64 = base64.b64encode(b"0" * (proctor_service.MAX_SCREENSHOT_BYTES + 1000)).decode()
    proctor_service._save_violation_screenshot(event_id=1, attempt_id=1, screenshot_base64=huge_base64)
