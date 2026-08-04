"""
Autosave ordering and idempotency.

The bug these cover: the answer path was unconditional last-write-wins, so a
request stalled on a slow connection could land AFTER a newer one for the same
question and silently revert an answer the candidate had already changed. The
exam client autosaves on every change and retries with backoff, which makes that
sequence routine on a bad connection rather than exotic.

Every test here drives the real HTTP endpoints, because the ordering guarantee
has to hold at the API boundary -- that is where the out-of-order requests
actually arrive.
"""
import pytest

from tests.conftest import auth_headers
from tests.test_exam_workflow import _create_examiner_and_login, _register_student_and_login


@pytest.fixture
def mcq_attempt(client, seed_roles, admin_token):
    """A published single-question MCQ exam with an attempt already started."""
    examiner_token = _create_examiner_and_login(client, admin_token)
    exam = client.post("/api/v1/exams", json={
        "title": "Autosave Ordering", "duration_minutes": 60, "proctoring_enabled": False,
    }, headers=auth_headers(examiner_token)).json()

    section = client.post(f"/api/v1/exams/{exam['id']}/sections", json={"title": "Main"},
                          headers=auth_headers(examiner_token)).json()
    question = client.post(f"/api/v1/exams/sections/{section['id']}/questions", json={
        "text": "Pick one", "marks": 1, "question_type": "mcq",
        "options": [{"text": "A", "is_correct": True}, {"text": "B", "is_correct": False},
                    {"text": "C", "is_correct": False}],
    }, headers=auth_headers(examiner_token)).json()
    client.post(f"/api/v1/exams/{exam['id']}/publish", headers=auth_headers(examiner_token))

    student_token = _register_student_and_login(client)
    attempt = client.post(f"/api/v1/attempts/start/{exam['id']}",
                          headers=auth_headers(student_token)).json()

    option_ids = [o["id"] for o in question["options"]]
    return {
        "client": client, "token": student_token,
        "attempt_id": attempt["attempt_id"], "question_id": question["id"],
        "options": option_ids,
    }


def _save(ctx, option_id, *, version=None, key=None):
    body = {"question_id": ctx["question_id"], "selected_option_id": option_id}
    if version is not None:
        body["answer_version"] = version
    if key is not None:
        body["idempotency_key"] = key
    return ctx["client"].put(f"/api/v1/attempts/{ctx['attempt_id']}/answer",
                             json=body, headers=auth_headers(ctx["token"]))


def _stored(ctx):
    answers = ctx["client"].get(f"/api/v1/attempts/{ctx['attempt_id']}/answers",
                                headers=auth_headers(ctx["token"])).json()
    return answers.get(str(ctx["question_id"]), answers.get(ctx["question_id"]))


# --- the bug --------------------------------------------------------------

def test_a_delayed_save_cannot_overwrite_a_newer_answer(mcq_attempt):
    """The exact failure: candidate answers A, connection stalls, they change to
    B, and the stalled A request finally lands. B must survive."""
    ctx = mcq_attempt
    a, b, _ = ctx["options"]

    _save(ctx, a, version=1, key="req-1")          # first choice
    _save(ctx, b, version=2, key="req-2")          # changed their mind
    late = _save(ctx, a, version=1, key="req-1-retry")  # the stalled original, arriving last

    assert late.status_code == 200
    assert late.json()["applied"] is False
    assert late.json()["outcome"] == "stale"
    assert _stored(ctx) == b, "a delayed request reverted a newer answer"


def test_an_immediate_replay_is_recognised_as_a_duplicate(mcq_attempt):
    """A retry of a request that landed but whose response was lost -- exactly
    what the client's network backoff produces when only the reply is dropped."""
    ctx = mcq_attempt
    a, _, _ = ctx["options"]

    first = _save(ctx, a, version=1, key="dup-key")
    replay = _save(ctx, a, version=1, key="dup-key")

    assert first.json()["applied"] is True
    assert replay.json()["applied"] is False
    assert replay.json()["outcome"] == "duplicate"
    assert _stored(ctx) == a


def test_an_old_replay_after_a_newer_write_is_still_refused(mcq_attempt):
    """Only the most recent accepted key is stored, so a replay of an OLDER
    request is no longer recognisable as a duplicate -- it is caught by the
    version check instead and reported as stale.

    Both guards refusing to apply it is the guarantee that matters; which of the
    two catches it is an implementation detail. This test pins the guarantee and
    deliberately does not pin the label.
    """
    ctx = mcq_attempt
    a, b, _ = ctx["options"]

    _save(ctx, a, version=1, key="dup-key")
    _save(ctx, b, version=2, key="other-key")
    replay = _save(ctx, a, version=1, key="dup-key")

    assert replay.json()["applied"] is False
    assert _stored(ctx) == b


def test_a_newer_save_is_applied_normally(mcq_attempt):
    ctx = mcq_attempt
    a, b, _ = ctx["options"]

    _save(ctx, a, version=1, key="k1")
    newer = _save(ctx, b, version=2, key="k2")

    assert newer.json()["applied"] is True
    assert newer.json()["answer_version"] == 2
    assert _stored(ctx) == b


def test_answers_can_still_be_cleared(mcq_attempt):
    """Clearing is a normal newer write, not a special case -- it must not be
    mistaken for 'nothing changed' and skipped."""
    ctx = mcq_attempt
    a, _, _ = ctx["options"]

    _save(ctx, a, version=1, key="k1")
    cleared = _save(ctx, None, version=2, key="k2")

    assert cleared.json()["applied"] is True
    assert _stored(ctx) is None


# --- backward compatibility -----------------------------------------------

def test_a_client_that_sends_no_version_still_works(mcq_attempt):
    """A candidate already mid-exam on a cached bundle must not start losing
    answers because the server was upgraded underneath them."""
    ctx = mcq_attempt
    a, b, _ = ctx["options"]

    assert _save(ctx, a).json()["applied"] is True
    assert _save(ctx, b).json()["applied"] is True
    assert _stored(ctx) == b


def test_a_versioned_client_is_not_blocked_by_pre_existing_unversioned_answers(mcq_attempt):
    """Migration 0019 backfills answer_version to 0, which must be below any
    version a client sends -- otherwise every first save after the upgrade
    would be rejected as stale."""
    ctx = mcq_attempt
    a, b, _ = ctx["options"]

    _save(ctx, a)                                    # unversioned -> stored at version 0
    first_versioned = _save(ctx, b, version=1, key="k1")

    assert first_versioned.json()["applied"] is True
    assert _stored(ctx) == b


# --- the same guarantee for the other two answer types ---------------------

def test_multi_select_rejects_a_stale_save(client, seed_roles, admin_token):
    examiner_token = _create_examiner_and_login(client, admin_token)
    exam = client.post("/api/v1/exams", json={
        "title": "Multi Ordering", "duration_minutes": 60, "proctoring_enabled": False,
    }, headers=auth_headers(examiner_token)).json()
    section = client.post(f"/api/v1/exams/{exam['id']}/sections", json={"title": "S"},
                          headers=auth_headers(examiner_token)).json()
    question = client.post(f"/api/v1/exams/sections/{section['id']}/questions", json={
        "text": "Pick several", "marks": 2, "question_type": "multi_select",
        "options": [{"text": "A", "is_correct": True}, {"text": "B", "is_correct": True},
                    {"text": "C", "is_correct": False}],
    }, headers=auth_headers(examiner_token)).json()
    client.post(f"/api/v1/exams/{exam['id']}/publish", headers=auth_headers(examiner_token))

    student_token = _register_student_and_login(client)
    attempt = client.post(f"/api/v1/attempts/start/{exam['id']}",
                          headers=auth_headers(student_token)).json()
    ids = [o["id"] for o in question["options"]]

    def save(selection, version, key):
        return client.put(f"/api/v1/attempts/{attempt['attempt_id']}/multi-answer", json={
            "question_id": question["id"], "selected_option_ids": selection,
            "answer_version": version, "idempotency_key": key,
        }, headers=auth_headers(student_token))

    save([ids[0]], 1, "m1")
    save([ids[0], ids[1]], 2, "m2")
    late = save([ids[0]], 1, "m1-retry")

    assert late.json()["applied"] is False
    assert sorted(late.json()["selected_option_ids"]) == sorted([ids[0], ids[1]])


def test_code_answer_rejects_a_stale_save(client, seed_roles, admin_token):
    """Highest-stakes of the three: a late retry here would restore an older
    snapshot of the candidate's whole source file."""
    examiner_token = _create_examiner_and_login(client, admin_token)
    exam = client.post("/api/v1/exams", json={
        "title": "Code Ordering", "duration_minutes": 60, "proctoring_enabled": False,
    }, headers=auth_headers(examiner_token)).json()
    section = client.post(f"/api/v1/exams/{exam['id']}/sections", json={"title": "S"},
                          headers=auth_headers(examiner_token)).json()
    question = client.post(f"/api/v1/exams/sections/{section['id']}/questions", json={
        "text": "Write a function", "marks": 5, "question_type": "coding",
        "language": "python", "starter_code": "", "options": [],
        "test_cases": [{"stdin": "", "expected_output": "hi", "is_sample": True}],
    }, headers=auth_headers(examiner_token)).json()
    client.post(f"/api/v1/exams/{exam['id']}/publish", headers=auth_headers(examiner_token))

    student_token = _register_student_and_login(client)
    attempt = client.post(f"/api/v1/attempts/start/{exam['id']}",
                          headers=auth_headers(student_token)).json()

    def save(code, version, key):
        return client.put(f"/api/v1/attempts/{attempt['attempt_id']}/code-answer", json={
            "question_id": question["id"], "source_code": code,
            "answer_version": version, "idempotency_key": key,
        }, headers=auth_headers(student_token))

    save("print('draft')", 1, "c1")
    save("print('final')", 2, "c2")
    late = save("print('draft')", 1, "c1-retry")
    assert late.json()["applied"] is False

    view = client.get(f"/api/v1/attempts/{attempt['attempt_id']}/question/{question['id']}",
                      headers=auth_headers(student_token)).json()
    assert view["source_code"] == "print('final')"
