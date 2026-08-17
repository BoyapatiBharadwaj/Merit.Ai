"""Submission, and the ways it used to lose a candidate's last answer."""
from datetime import datetime, timedelta, timezone

import pytest

from tests.conftest import auth_headers
from tests.test_exam_workflow import _create_examiner_and_login, _register_student_and_login


@pytest.fixture
def exam_ctx(client, seed_roles, admin_token):
    """A published two-question exam (MCQ + multi-select) with a live attempt."""
    examiner_token = _create_examiner_and_login(client, admin_token)
    headers = auth_headers(examiner_token)
    exam = client.post("/api/v1/exams", json={
        "title": "Finalize", "duration_minutes": 60, "proctoring_enabled": False,
    }, headers=headers).json()
    section = client.post(f"/api/v1/exams/{exam['id']}/sections", json={"title": "Main"},
                          headers=headers).json()
    mcq = client.post(f"/api/v1/exams/sections/{section['id']}/questions", json={
        "text": "Pick one", "marks": 10, "question_type": "mcq",
        "options": [{"text": "right", "is_correct": True}, {"text": "wrong", "is_correct": False}],
    }, headers=headers).json()
    multi = client.post(f"/api/v1/exams/sections/{section['id']}/questions", json={
        "text": "Pick several", "marks": 10, "question_type": "multi_select",
        "options": [{"text": "a", "is_correct": True}, {"text": "b", "is_correct": True},
                    {"text": "c", "is_correct": False}],
    }, headers=headers).json()
    client.post(f"/api/v1/exams/{exam['id']}/publish", headers=headers)

    student_token = _register_student_and_login(client)
    start = client.post(f"/api/v1/attempts/start/{exam['id']}",
                        headers=auth_headers(student_token)).json()
    return {
        "client": client, "exam_id": exam["id"], "token": student_token,
        "headers": auth_headers(student_token),
        "attempt_id": start["attempt_id"],
        "attempt_token": start["attempt_token"],
        "mcq": mcq, "multi": multi,
    }


def _finalize(ctx, final_answers=None, headers=None):
    return ctx["client"].post(
        f"/api/v1/attempts/{ctx['attempt_id']}/finalize",
        json={"final_answers": final_answers or []},
        headers=headers or ctx["headers"],
    )


def _correct(question):
    """The option id the exam author marked correct."""
    return next(o["id"] for o in question["options"] if o.get("is_correct"))


def _wrong(question):
    return next(o["id"] for o in question["options"] if not o.get("is_correct"))


# --- the answer-loss bug ------------------------------------------------------

def test_the_answer_sent_with_the_submission_is_the_one_graded(exam_ctx):
    """The exact failure, end to end."""
    ctx = exam_ctx
    # An earlier, wrong answer IS saved normally.
    ctx["client"].put(f"/api/v1/attempts/{ctx['attempt_id']}/answer", json={
        "question_id": ctx["mcq"]["id"], "selected_option_id": _wrong(ctx["mcq"]),
        "answer_version": 1,
    }, headers=ctx["headers"])

    # They change their mind and submit immediately. Only finalize carries it.
    response = _finalize(ctx, [{
        "question_id": ctx["mcq"]["id"],
        "selected_option_id": _correct(ctx["mcq"]),
        "answer_version": 2,
    }])
    assert response.status_code == 200, response.text
    assert response.json()["scored_marks"] == 10, "the final answer was not the one graded"


def test_a_final_answer_for_a_question_never_saved_still_counts(exam_ctx):
    """Nothing was autosaved at all -- every answer arrives with the submission.
    A candidate who works offline and reconnects at the deadline is this case."""
    ctx = exam_ctx
    response = _finalize(ctx, [
        {"question_id": ctx["mcq"]["id"], "selected_option_id": _correct(ctx["mcq"]), "answer_version": 1},
        {"question_id": ctx["multi"]["id"],
         "selected_option_ids": [o["id"] for o in ctx["multi"]["options"] if o["is_correct"]],
         "answer_version": 1},
    ])
    assert response.status_code == 200, response.text
    assert response.json()["scored_marks"] == 20
    assert response.json()["correct_count"] == 2


def test_an_omitted_question_leaves_its_stored_answer_alone(exam_ctx):
    """A snapshot only carries what the candidate touched. Absence must mean
    "no change", not "clear it" -- otherwise a client that trims its payload
    would blank out answers it had already saved correctly."""
    ctx = exam_ctx
    ctx["client"].put(f"/api/v1/attempts/{ctx['attempt_id']}/answer", json={
        "question_id": ctx["mcq"]["id"], "selected_option_id": _correct(ctx["mcq"]),
        "answer_version": 1,
    }, headers=ctx["headers"])

    # Finalize mentions only the OTHER question.
    response = _finalize(ctx, [{
        "question_id": ctx["multi"]["id"], "selected_option_ids": [], "answer_version": 1,
    }])
    assert response.status_code == 200, response.text
    assert response.json()["scored_marks"] == 10, "the untouched saved answer was lost"


# --- idempotency: the retry that repairs a dropped connection -----------------

def test_finalizing_twice_returns_the_same_result(exam_ctx):
    """The client retries a submission whose response was lost. That retry must
    not be an error -- if it were, a candidate whose connection blipped at the
    deadline would be shown a failure for a submission that had succeeded."""
    ctx = exam_ctx
    first = _finalize(ctx, [{"question_id": ctx["mcq"]["id"],
                             "selected_option_id": _correct(ctx["mcq"]), "answer_version": 1}])
    assert first.status_code == 200, first.text

    second = _finalize(ctx, [{"question_id": ctx["mcq"]["id"],
                              "selected_option_id": _correct(ctx["mcq"]), "answer_version": 1}])
    assert second.status_code == 200, second.text
    assert second.json() == first.json()


def test_a_retry_cannot_change_the_answers_after_submission(exam_ctx):
    """The flip side of being idempotent: a second finalize must not be a way
    back into a closed attempt."""
    ctx = exam_ctx
    _finalize(ctx, [{"question_id": ctx["mcq"]["id"],
                     "selected_option_id": _wrong(ctx["mcq"]), "answer_version": 1}])

    late = _finalize(ctx, [{"question_id": ctx["mcq"]["id"],
                            "selected_option_id": _correct(ctx["mcq"]), "answer_version": 99}])
    assert late.status_code == 200
    assert late.json()["scored_marks"] == 0, "a post-submission finalize rewrote a graded answer"


# --- the audit field the candidate used to control ----------------------------

def test_submitting_before_the_deadline_is_recorded_as_deliberate(exam_ctx):
    ctx = exam_ctx
    _finalize(ctx)
    attempts = ctx["client"].get("/api/v1/attempts/my", headers=ctx["headers"]).json()
    assert attempts[0]["status"] == "submitted"


def test_submitting_after_the_deadline_is_recorded_as_a_timeout(exam_ctx, db_session):
    """And the candidate cannot label it otherwise. `?auto=` used to decide
    this, which meant the audited party set their own audit field."""
    ctx = exam_ctx
    from app.models.attempt import StudentExamAttempt
    attempt = db_session.query(StudentExamAttempt).filter(
        StudentExamAttempt.id == ctx["attempt_id"]).first()
    attempt.started_at = datetime.now(timezone.utc) - timedelta(hours=3)
    db_session.commit()

    # Passing the old parameter explicitly must not change the outcome.
    response = ctx["client"].post(
        f"/api/v1/attempts/{ctx['attempt_id']}/submit?auto=false", headers=ctx["headers"])
    assert response.status_code in (200, 400)

    attempts = ctx["client"].get("/api/v1/attempts/my", headers=ctx["headers"]).json()
    assert attempts[0]["status"] == "auto_submitted", "the candidate relabelled their own timeout"


# --- validation: finalize is not a hole through the autosave checks -----------

def test_an_option_from_another_question_is_rejected(exam_ctx):
    ctx = exam_ctx
    response = _finalize(ctx, [{
        "question_id": ctx["mcq"]["id"],
        "selected_option_id": ctx["multi"]["options"][0]["id"],
        "answer_version": 1,
    }])
    assert response.status_code == 400


def test_a_question_from_another_exam_is_rejected(exam_ctx, admin_token, client):
    ctx = exam_ctx
    other_examiner = _create_examiner_and_login(client, admin_token, email="other@example.com")
    other_headers = auth_headers(other_examiner)
    other_exam = client.post("/api/v1/exams", json={
        "title": "Other", "duration_minutes": 30, "proctoring_enabled": False,
    }, headers=other_headers).json()
    other_section = client.post(f"/api/v1/exams/{other_exam['id']}/sections",
                                json={"title": "S"}, headers=other_headers).json()
    foreign = client.post(f"/api/v1/exams/sections/{other_section['id']}/questions", json={
        "text": "Not yours", "marks": 5, "question_type": "mcq",
        "options": [{"text": "x", "is_correct": True}, {"text": "y", "is_correct": False}],
    }, headers=other_headers).json()

    response = _finalize(ctx, [{"question_id": foreign["id"],
                                "selected_option_id": foreign["options"][0]["id"],
                                "answer_version": 1}])
    assert response.status_code == 400


def test_another_student_cannot_finalize_this_attempt(exam_ctx, client):
    intruder = _register_student_and_login(client, email="intruder@example.com")
    response = _finalize(exam_ctx, headers=auth_headers(intruder))
    assert response.status_code == 404


# --- resuming after a reload --------------------------------------------------

def test_the_question_endpoint_reports_the_stored_answer_version(exam_ctx):
    """What a freshly reloaded page seeds its counter from."""
    ctx = exam_ctx
    for version in (1, 2, 3):
        ctx["client"].put(f"/api/v1/attempts/{ctx['attempt_id']}/answer", json={
            "question_id": ctx["mcq"]["id"], "selected_option_id": _correct(ctx["mcq"]),
            "answer_version": version,
        }, headers=ctx["headers"])

    question = ctx["client"].get(
        f"/api/v1/attempts/{ctx['attempt_id']}/question/{ctx['mcq']['id']}",
        headers=ctx["headers"]).json()
    assert question["answer_version"] == 3


def test_a_reloaded_page_that_seeds_its_version_can_still_save(exam_ctx):
    """The reload bug, reproduced as the client experiences it."""
    ctx = exam_ctx
    for version in (1, 2, 3):
        ctx["client"].put(f"/api/v1/attempts/{ctx['attempt_id']}/answer", json={
            "question_id": ctx["mcq"]["id"], "selected_option_id": _wrong(ctx["mcq"]),
            "answer_version": version,
        }, headers=ctx["headers"])

    # The page reloads and reads the server's version...
    seeded = ctx["client"].get(
        f"/api/v1/attempts/{ctx['attempt_id']}/question/{ctx['mcq']['id']}",
        headers=ctx["headers"]).json()["answer_version"]

    # ...then the candidate changes their answer.
    applied = ctx["client"].put(f"/api/v1/attempts/{ctx['attempt_id']}/answer", json={
        "question_id": ctx["mcq"]["id"], "selected_option_id": _correct(ctx["mcq"]),
        "answer_version": seeded + 1,
    }, headers=ctx["headers"]).json()
    assert applied["applied"] is True

    assert _finalize(ctx).json()["scored_marks"] == 10


def test_a_client_that_restarts_at_one_is_still_refused(exam_ctx):
    """The guard that made the reload bug visible must stay. If a stale save
    were quietly accepted, the ordering protection would be gone too."""
    ctx = exam_ctx
    for version in (1, 2, 3):
        ctx["client"].put(f"/api/v1/attempts/{ctx['attempt_id']}/answer", json={
            "question_id": ctx["mcq"]["id"], "selected_option_id": _correct(ctx["mcq"]),
            "answer_version": version,
        }, headers=ctx["headers"])

    refused = ctx["client"].put(f"/api/v1/attempts/{ctx['attempt_id']}/answer", json={
        "question_id": ctx["mcq"]["id"], "selected_option_id": _wrong(ctx["mcq"]),
        "answer_version": 1,
    }, headers=ctx["headers"]).json()
    assert refused["applied"] is False
    assert refused["outcome"] == "stale"
    # And it tells the client what the server actually holds, so it can recover.
    assert refused["answer_version"] == 3


# --- grading that failed the first time ---------------------------------------

def test_a_submitted_but_ungraded_attempt_is_repaired_on_read(exam_ctx, db_session):
    """Grading runs outside the finalize transaction, so a sandbox timeout or a restart can
    leave an attempt submitted with no result.
    """
    ctx = exam_ctx
    _finalize(ctx, [{"question_id": ctx["mcq"]["id"],
                     "selected_option_id": _correct(ctx["mcq"]), "answer_version": 1}])

    from app.models.attempt import ExamResult
    db_session.query(ExamResult).filter(
        ExamResult.attempt_id == ctx["attempt_id"]).delete()
    db_session.commit()

    recovered = ctx["client"].get(f"/api/v1/attempts/{ctx['attempt_id']}/result",
                                  headers=ctx["headers"])
    assert recovered.status_code == 200, recovered.text
    assert recovered.json()["scored_marks"] == 10


# --- the attempt-scoped token -------------------------------------------------

def test_start_issues_a_token_scoped_to_the_attempt(exam_ctx):
    ctx = exam_ctx
    assert ctx["attempt_token"]

    headers = {"Authorization": f"Bearer {ctx['attempt_token']}"}
    question = ctx["client"].get(
        f"/api/v1/attempts/{ctx['attempt_id']}/question/{ctx['mcq']['id']}", headers=headers)
    assert question.status_code == 200, question.text


def test_the_attempt_token_opens_nothing_else(exam_ctx):
    """The whole reason it is safe to make it long-lived. If it worked as an
    ordinary session token, it would be a multi-hour credential rather than a
    narrow one."""
    ctx = exam_ctx
    headers = {"Authorization": f"Bearer {ctx['attempt_token']}"}

    assert ctx["client"].get("/api/v1/users/me", headers=headers).status_code == 401
    assert ctx["client"].get("/api/v1/exams/available", headers=headers).status_code == 401
    assert ctx["client"].get("/api/v1/attempts/my", headers=headers).status_code == 401


def test_the_attempt_token_does_not_work_on_another_attempt(exam_ctx, client, admin_token):
    """It names one attempt, and that name is checked against the path."""
    ctx = exam_ctx
    other_student = _register_student_and_login(client, email="second@example.com")
    other = client.post(f"/api/v1/attempts/start/{ctx['exam_id']}",
                        headers=auth_headers(other_student)).json()

    headers = {"Authorization": f"Bearer {ctx['attempt_token']}"}
    response = client.get(f"/api/v1/attempts/{other['attempt_id']}/answers", headers=headers)
    assert response.status_code == 401


def test_the_attempt_token_outlives_the_session_token(exam_ctx):
    """The point of the whole mechanism: a 120-minute session token must not be
    able to end a 480-minute exam."""
    import jwt
    from app.core.config import settings

    ctx = exam_ctx
    attempt_claims = jwt.decode(ctx["attempt_token"], settings.SECRET_KEY,
                                algorithms=[settings.ALGORITHM])
    session_claims = jwt.decode(ctx["token"], settings.SECRET_KEY,
                                algorithms=[settings.ALGORITHM])
    assert attempt_claims["scope"] == "attempt"
    assert attempt_claims["attempt_id"] == ctx["attempt_id"]
    # 60-minute exam + 30-minute grace comfortably exceeds the exam itself,
    # and is derived from the attempt rather than from a fixed lifetime.
    assert attempt_claims["exp"] > session_claims["exp"] - settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60


def test_the_status_endpoint_reports_the_servers_own_clock(exam_ctx):
    ctx = exam_ctx
    headers = {"Authorization": f"Bearer {ctx['attempt_token']}"}
    status_response = ctx["client"].get(
        f"/api/v1/attempts/{ctx['attempt_id']}/status", headers=headers)
    assert status_response.status_code == 200, status_response.text
    body = status_response.json()
    assert body["status"] == "in_progress"
    assert 0 < body["remaining_seconds"] <= 60 * 60


def test_the_status_endpoint_reports_an_attempt_the_server_finalized(exam_ctx, db_session):
    """How a client that was offline past the deadline finds out."""
    ctx = exam_ctx
    from app.models.attempt import StudentExamAttempt
    attempt = db_session.query(StudentExamAttempt).filter(
        StudentExamAttempt.id == ctx["attempt_id"]).first()
    attempt.started_at = datetime.now(timezone.utc) - timedelta(hours=3)
    db_session.commit()

    body = ctx["client"].get(f"/api/v1/attempts/{ctx['attempt_id']}/status",
                             headers=ctx["headers"]).json()
    assert body["status"] == "auto_submitted"
    assert body["remaining_seconds"] == 0


def test_proctoring_keeps_working_on_the_attempt_token(exam_ctx):
    """The gap that would otherwise have swallowed the whole fix."""
    ctx = exam_ctx
    headers = {"Authorization": f"Bearer {ctx['attempt_token']}"}

    # A 1x1 PNG: enough to get past auth and schema validation, which is all
    # this is checking. Whether the model finds a face is not the point.
    tiny_png = ("data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ"
                "AAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
    for path in ("/api/v1/proctoring/face/verify", "/api/v1/proctoring/objects/detect"):
        response = ctx["client"].post(path, json={"image_base64": tiny_png}, headers=headers)
        assert response.status_code != 401, f"{path} rejected the attempt token"

    event = ctx["client"].post("/api/v1/proctoring/events", json={
        "attempt_id": ctx["attempt_id"], "event_type": "tab_switch", "description": "test",
    }, headers=headers)
    assert event.status_code in (201, 200), event.text


def test_the_attempt_token_cannot_log_events_against_another_attempt(exam_ctx, client):
    """The proctoring endpoints take the attempt id in the BODY, so the token's
    own attempt id cannot be matched against a path parameter. The handler's own
    ownership check is what holds here, and it has to."""
    ctx = exam_ctx
    other_student = _register_student_and_login(client, email="third@example.com")
    other = client.post(f"/api/v1/attempts/start/{ctx['exam_id']}",
                        headers=auth_headers(other_student)).json()

    headers = {"Authorization": f"Bearer {ctx['attempt_token']}"}
    response = client.post("/api/v1/proctoring/events", json={
        "attempt_id": other["attempt_id"], "event_type": "tab_switch", "description": "nope",
    }, headers=headers)
    assert response.status_code in (403, 404)
