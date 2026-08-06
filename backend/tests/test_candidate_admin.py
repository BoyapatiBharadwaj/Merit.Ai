"""
Administrator control over a candidate account: editing it, and asking the
candidate to prove their identity again.

Deleting, deactivating and resetting a password are covered in
test_activity_and_account_admin.py -- those verbs belong to every role and live
in users.py. What is tested here is what is specific to a CANDIDATE: the
details their identity was verified against, and the evidence behind it.
"""
from datetime import datetime, timezone

from app.models.student import Student
from app.models.user import User
from tests.conftest import auth_headers
from tests.test_exam_workflow import _register_student_and_login


def _student(db_session, email="student@example.com"):
    return (db_session.query(Student)
            .join(User, Student.user_id == User.id)
            .filter(User.email == email)
            .one())


def _verify_fully(db_session, student, *, face=True):
    """Put a candidate in the state a real one reaches after both checks.

    The face half is faked with a FaceProfile row rather than by driving the
    real recogniser: this module is about the administrative rules on top of
    verification, not about whether ArcFace works.
    """
    from app.models.face_profile import FaceProfile

    student.id_verified = True
    student.id_verified_at = datetime.now(timezone.utc)
    student.id_verified_name = "Test Student"
    student.identity_locked = True
    student.identity_locked_at = datetime.now(timezone.utc)
    if face and not db_session.query(FaceProfile).filter(FaceProfile.student_id == student.id).first():
        db_session.add(FaceProfile(student_id=student.id, image_path="uploads/faces/example.jpg",
                                   encoding="[]"))
    db_session.commit()
    db_session.refresh(student)
    return student


# ------------------------------------------------------------------------------
# Editing
# ------------------------------------------------------------------------------

def test_admin_can_correct_an_unverified_candidates_details(client, seed_roles, admin_token, db_session):
    _register_student_and_login(client)
    student = _student(db_session)

    res = client.patch(f"/api/v1/admin/candidates/{student.id}",
                       json={"first_name": "Corrected", "roll_number": "CS-42"},
                       headers=auth_headers(admin_token))
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["first_name"] == "Corrected"
    assert body["full_name"] == "Corrected Student"
    assert body["roll_number"] == "CS-42"
    # Nothing was verified, so nothing is disturbed.
    assert body["identity_unlocked"] is False
    assert body["reverification_required"] is False


def test_editing_a_verified_name_unlocks_and_requires_reverification(client, seed_roles,
                                                                     admin_token, db_session):
    """The point of the whole feature.

    The stored name was matched against the name printed on an ID card, and
    identity_locked records that this happened. Renaming it silently would
    leave a "verified" badge attached to a name nobody has ever checked --
    worse than no badge, because the badge is what an examiner relies on when
    deciding whether the person on the webcam is the person enrolled.
    """
    _register_student_and_login(client)
    student = _verify_fully(db_session, _student(db_session))
    assert student.identity_locked is True

    res = client.patch(f"/api/v1/admin/candidates/{student.id}",
                       json={"last_name": "Different"},
                       headers=auth_headers(admin_token))
    assert res.status_code == 200, res.text
    assert res.json()["identity_unlocked"] is True
    assert res.json()["reverification_required"] is True

    db_session.expire_all()
    student = _student(db_session)
    assert student.identity_locked is False
    assert student.reverification_required_at is not None


def test_changing_only_the_roll_number_costs_nothing(client, seed_roles, admin_token, db_session):
    """A roll number was never matched against the ID card.

    Charging a candidate a full re-verification for a change that has nothing
    to do with their identity evidence would make administrators avoid fixing
    typos, which is how bad data becomes permanent.
    """
    _register_student_and_login(client)
    student = _verify_fully(db_session, _student(db_session))

    res = client.patch(f"/api/v1/admin/candidates/{student.id}",
                       json={"roll_number": "NEW-99"}, headers=auth_headers(admin_token))
    assert res.status_code == 200
    assert res.json()["identity_unlocked"] is False
    assert res.json()["reverification_required"] is False

    db_session.expire_all()
    assert _student(db_session).identity_locked is True


def test_an_edit_cannot_take_another_candidates_email_or_roll_number(client, seed_roles,
                                                                     admin_token, db_session):
    _register_student_and_login(client, "first@example.com")
    _register_student_and_login(client, "second@example.com")
    first = _student(db_session, "first@example.com")

    clash = client.patch(f"/api/v1/admin/candidates/{first.id}",
                         json={"email": "second@example.com"}, headers=auth_headers(admin_token))
    assert clash.status_code == 400
    assert "already in use" in clash.json()["detail"]


def test_only_an_admin_can_edit_a_candidate(client, seed_roles, db_session):
    token = _register_student_and_login(client)
    student = _student(db_session)
    res = client.patch(f"/api/v1/admin/candidates/{student.id}",
                       json={"first_name": "Self"}, headers=auth_headers(token))
    assert res.status_code == 403


# ------------------------------------------------------------------------------
# Re-verification
# ------------------------------------------------------------------------------

def test_requiring_reverification_blocks_the_exam_gate(client, seed_roles, admin_token, db_session):
    from app.services import identity_service

    _register_student_and_login(client)
    student = _verify_fully(db_session, _student(db_session))
    assert identity_service.verification_state(db_session, student)["exam_ready"] is True

    res = client.post(f"/api/v1/admin/candidates/{student.id}/require-reverification",
                      json={"reason": "The registered photo did not match at the last sitting.",
                            "notify": False},
                      headers=auth_headers(admin_token))
    assert res.status_code == 200, res.text

    db_session.expire_all()
    state = identity_service.verification_state(db_session, _student(db_session))
    assert state["reverification_required"] is True
    assert state["exam_ready"] is False
    assert "did not match" in state["reverification_reason"]


def test_the_evidence_is_kept_not_erased(client, seed_roles, admin_token, db_session):
    """Erasing biometrics and requiring re-verification are opposite actions.

    The moment somebody doubts the enrolled face is exactly the moment it must
    be preserved -- it is what a review will look at. If this ever starts
    deleting, the feature has become a worse version of the erase endpoint that
    already exists.
    """
    from app.models.face_profile import FaceProfile

    _register_student_and_login(client)
    student = _verify_fully(db_session, _student(db_session))
    student.id_card_image_path = "uploads/id_cards/example.jpg"
    db_session.commit()

    client.post(f"/api/v1/admin/candidates/{student.id}/require-reverification",
                json={"reason": "Spot check", "notify": False}, headers=auth_headers(admin_token))

    db_session.expire_all()
    student = _student(db_session)
    assert db_session.query(FaceProfile).filter(FaceProfile.student_id == student.id).count() == 1
    assert student.id_card_image_path == "uploads/id_cards/example.jpg"


def test_the_gate_message_names_this_situation(client, seed_roles, admin_token, db_session):
    """Telling someone whose face and ID are both on file that their
    verification is "incomplete" is false, and sends them to a Profile page
    showing two green ticks -- so they conclude the platform is broken."""
    import pytest
    from fastapi import HTTPException

    from app.services import identity_service

    _register_student_and_login(client)
    student = _verify_fully(db_session, _student(db_session))
    client.post(f"/api/v1/admin/candidates/{student.id}/require-reverification",
                json={"reason": "Photo quality", "notify": False}, headers=auth_headers(admin_token))
    db_session.expire_all()

    with pytest.raises(HTTPException) as excinfo:
        identity_service.require_exam_ready(db_session, _student(db_session))
    detail = excinfo.value.detail
    assert "verify your identity again" in detail
    assert "Photo quality" in detail
    assert "incomplete" not in detail


def test_reverification_clears_only_when_BOTH_halves_are_redone(client, seed_roles,
                                                                admin_token, db_session):
    """Clearing after the face alone would let a candidate whose ID card was
    the problem walk straight back through the gate."""
    from app.models.face_profile import FaceProfile
    from app.services import identity_service

    _register_student_and_login(client)
    student = _verify_fully(db_session, _student(db_session))
    client.post(f"/api/v1/admin/candidates/{student.id}/require-reverification",
                json={"reason": "Spot check", "notify": False}, headers=auth_headers(admin_token))
    db_session.expire_all()
    student = _student(db_session)

    # Face redone; ID still outstanding (require_reverification cleared it).
    db_session.query(FaceProfile).filter(FaceProfile.student_id == student.id).delete()
    db_session.add(FaceProfile(student_id=student.id, image_path="uploads/faces/example.jpg",
                                   encoding="[]"))
    db_session.commit()
    identity_service.record_face_registration(db_session, student)
    db_session.refresh(student)
    assert student.reverification_required_at is not None, "cleared with the ID card still outstanding"

    # Now the ID card too.
    identity_service.record_id_verification(db_session, student, name_matched=True,
                                            extracted_name="Test Student")
    db_session.refresh(student)
    assert student.reverification_required_at is None
    assert identity_service.verification_state(db_session, student)["exam_ready"] is True


def test_asking_twice_is_refused(client, seed_roles, admin_token, db_session):
    _register_student_and_login(client)
    student = _verify_fully(db_session, _student(db_session))
    first = client.post(f"/api/v1/admin/candidates/{student.id}/require-reverification",
                        json={"notify": False}, headers=auth_headers(admin_token))
    assert first.status_code == 200
    again = client.post(f"/api/v1/admin/candidates/{student.id}/require-reverification",
                        json={"notify": False}, headers=auth_headers(admin_token))
    assert again.status_code == 400
    assert "already been asked" in again.json()["detail"]


def test_asking_an_unverified_candidate_is_refused(client, seed_roles, admin_token, db_session):
    """They are already blocked. A second, differently-worded block telling
    them to redo something they have never done once is just confusing."""
    _register_student_and_login(client)
    student = _student(db_session)
    res = client.post(f"/api/v1/admin/candidates/{student.id}/require-reverification",
                      json={"notify": False}, headers=auth_headers(admin_token))
    assert res.status_code == 400
    assert "nothing to re-verify" in res.json()["detail"]


def test_the_candidate_is_emailed_with_the_reason(client, seed_roles, admin_token, db_session,
                                                  outbox, email_on):
    _register_student_and_login(client)
    student = _verify_fully(db_session, _student(db_session))

    res = client.post(f"/api/v1/admin/candidates/{student.id}/require-reverification",
                      json={"reason": "Your ID card photo was unreadable.", "notify": True},
                      headers=auth_headers(admin_token))
    assert res.status_code == 200
    assert res.json()["emailed"] is True

    sent = [m for m in outbox if m["to"] == "student@example.com"]
    assert len(sent) == 1
    assert "verify your identity again" in sent[0]["subject"]
    assert "Your ID card photo was unreadable." in sent[0]["text"]


def test_not_notifying_is_reported_rather_than_assumed(client, seed_roles, admin_token,
                                                       db_session, outbox, email_on):
    """"We told them" and "we recorded it and told nobody" must not look the
    same to the administrator who pressed the button."""
    _register_student_and_login(client)
    student = _verify_fully(db_session, _student(db_session))

    res = client.post(f"/api/v1/admin/candidates/{student.id}/require-reverification",
                      json={"reason": "Quiet check", "notify": False},
                      headers=auth_headers(admin_token))
    assert res.json()["emailed"] is False
    assert [m for m in outbox if m["to"] == "student@example.com"] == []


def test_the_request_is_written_to_the_audit_trail(client, seed_roles, admin_token, db_session):
    from app.models.activity_log import ActivityLog, ActivityType

    _register_student_and_login(client)
    student = _verify_fully(db_session, _student(db_session))
    client.post(f"/api/v1/admin/candidates/{student.id}/require-reverification",
                json={"reason": "Spot check", "notify": False}, headers=auth_headers(admin_token))

    entry = (db_session.query(ActivityLog)
             .filter(ActivityLog.activity_type == ActivityType.REVERIFICATION_REQUIRED)
             .one())
    assert entry.subject_user_id == student.user_id
    assert "Spot check" in (entry.description or "")


# ------------------------------------------------------------------------------
# Deletion, and what it would take with it
#
# The guard against deleting an account with real exam history existed only on
# DELETE /admin/examiners/{id}. The admin UI deletes through DELETE
# /users/{id} -- the route that serves both roles -- which had no check at all.
# So the protection depended on which button you happened to press, and
# candidates had none.
# ------------------------------------------------------------------------------

def _attempt_for(db_session, student):
    from app.models.attempt import StudentExamAttempt
    from app.models.enums import AttemptStatus
    from app.models.exam import Exam

    exam = Exam(title="Recorded paper", duration_minutes=30, examiner_id=1,
                status="published", pass_percentage=40)
    db_session.add(exam)
    db_session.flush()
    attempt = StudentExamAttempt(student_id=student.id, exam_id=exam.id,
                                 status=AttemptStatus.SUBMITTED,
                                 started_at=datetime.now(timezone.utc))
    db_session.add(attempt)
    db_session.commit()
    return attempt


def test_a_candidate_who_has_sat_an_exam_cannot_be_deleted(client, seed_roles, admin_token, db_session):
    """Deleting them would destroy the assessment record, not just the account.

    Answers, marks and the proctoring evidence behind them all cascade from the
    user row. That is correct for a genuine erasure request and completely
    wrong as the default for "this person has left".
    """
    _register_student_and_login(client)
    student = _student(db_session)
    _attempt_for(db_session, student)

    res = client.delete(f"/api/v1/users/{student.user_id}", headers=auth_headers(admin_token))
    assert res.status_code == 400, res.text
    assert "sat at least one exam" in res.json()["detail"]
    # Steered somewhere that achieves what the admin actually wanted.
    assert "Disable the account instead" in res.json()["detail"]

    # And the account really is still there.
    db_session.expire_all()
    assert db_session.query(User).filter(User.id == student.user_id).count() == 1


def test_a_candidate_with_no_history_can_still_be_deleted(client, seed_roles, admin_token, db_session):
    """The guard must not become a blanket ban -- a mistakenly created account
    with nothing behind it should still be removable."""
    _register_student_and_login(client)
    student = _student(db_session)
    user_id = student.user_id

    res = client.delete(f"/api/v1/users/{user_id}", headers=auth_headers(admin_token))
    assert res.status_code == 200, res.text
    db_session.expire_all()
    assert db_session.query(User).filter(User.id == user_id).count() == 0


def test_deactivating_that_candidate_still_works(client, seed_roles, admin_token, db_session):
    """The alternative the refusal points at has to actually be available."""
    _register_student_and_login(client)
    student = _student(db_session)
    _attempt_for(db_session, student)

    res = client.post(f"/api/v1/users/{student.user_id}/deactivate", headers=auth_headers(admin_token))
    assert res.status_code == 200, res.text
    db_session.expire_all()
    assert db_session.query(User).filter(User.id == student.user_id).one().is_active is False

    denied = client.post("/api/v1/auth/login",
                         json={"email": "student@example.com", "password": "Sup3rSecret!"})
    assert denied.status_code in (401, 403)


def test_the_examiner_guard_is_not_route_dependent(client, seed_roles, admin_token, db_session):
    """The Examiners page refused this; /users/{id} did not.

    Two routes to the same destruction with one guard between them is the same
    as no guard, for anyone who uses the other door.
    """
    from app.models.examiner import Examiner
    from tests.test_exam_workflow import _create_examiner_and_login

    _create_examiner_and_login(client, admin_token)
    _register_student_and_login(client)
    examiner = db_session.query(Examiner).first()
    student = _student(db_session)

    from app.models.attempt import StudentExamAttempt
    from app.models.enums import AttemptStatus
    from app.models.exam import Exam

    exam = Exam(title="Their paper", duration_minutes=30, examiner_id=examiner.id,
                status="published", pass_percentage=40)
    db_session.add(exam)
    db_session.flush()
    db_session.add(StudentExamAttempt(student_id=student.id, exam_id=exam.id,
                                      status=AttemptStatus.SUBMITTED,
                                      started_at=datetime.now(timezone.utc)))
    db_session.commit()

    res = client.delete(f"/api/v1/users/{examiner.user_id}", headers=auth_headers(admin_token))
    assert res.status_code == 400, res.text
    assert "real candidate attempts" in res.json()["detail"]


def test_the_candidate_can_see_the_request_on_their_own_profile(client, seed_roles, admin_token,
                                                                db_session):
    """Otherwise the Profile page shows two green ticks and a blocked exam
    gate, with nothing anywhere explaining the contradiction."""
    token = _register_student_and_login(client)
    student = _verify_fully(db_session, _student(db_session))
    client.post(f"/api/v1/admin/candidates/{student.id}/require-reverification",
                json={"reason": "Photo was unreadable.", "notify": False},
                headers=auth_headers(admin_token))

    me = client.get("/api/v1/users/me", headers=auth_headers(token)).json()
    assert me["reverification_required"] is True
    assert me["reverification_reason"] == "Photo was unreadable."
    assert me["exam_ready"] is False
    # The two ticks are still true, and still shown -- the point is that they
    # are no longer sufficient.
    assert me["face_registered"] is True

    status_poll = client.get("/api/v1/proctoring/identity/status", headers=auth_headers(token)).json()
    assert status_poll["reverification_required"] is True
    assert status_poll["exam_ready"] is False


def test_the_admin_detail_page_shows_the_outstanding_request(client, seed_roles, admin_token,
                                                             db_session):
    _register_student_and_login(client)
    student = _verify_fully(db_session, _student(db_session))
    client.post(f"/api/v1/admin/candidates/{student.id}/require-reverification",
                json={"reason": "Spot check", "notify": False}, headers=auth_headers(admin_token))

    detail = client.get(f"/api/v1/admin/candidates/{student.id}",
                        headers=auth_headers(admin_token)).json()
    assert detail["reverification_required"] is True
    assert detail["reverification_reason"] == "Spot check"
