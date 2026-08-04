"""POST /proctoring/events/batch -- the batched counterpart to /events used by
frontend/src/lib/eventLogger.js, which queues violations from lockdown.js and
proctoring.js and flushes them together instead of one request per event.
"""
from tests.conftest import auth_headers
from tests.test_exam_workflow import _build_published_exam, _create_examiner_and_login, _register_student_and_login


def _start_attempt(client, admin_token, email="student@example.com"):
    examiner_headers = auth_headers(_create_examiner_and_login(client, admin_token))
    exam_id = _build_published_exam(client, examiner_headers)
    student_token = _register_student_and_login(client, email=email)
    started = client.post(f"/api/v1/attempts/start/{exam_id}", headers=auth_headers(student_token))
    assert started.status_code == 200, started.text
    return student_token, started.json()["attempt_id"]


def test_batch_creates_every_event_in_one_request(client, seed_roles, admin_token):
    student_token, attempt_id = _start_attempt(client, admin_token)
    headers = auth_headers(student_token)

    response = client.post("/api/v1/proctoring/events/batch", json={
        "events": [
            {"attempt_id": attempt_id, "event_type": "right_click_attempt", "description": "a"},
            {"attempt_id": attempt_id, "event_type": "copy_paste_attempt", "description": "b"},
            {"attempt_id": attempt_id, "event_type": "looking_away", "description": "c"},
        ],
    }, headers=headers)
    assert response.status_code == 201, response.text
    assert response.json() == {"created": 3, "received": 3}

    events = client.get(f"/api/v1/proctoring/events/attempt/{attempt_id}", headers=headers).json()
    types = {e["event_type"] for e in events}
    assert {"right_click_attempt", "copy_paste_attempt", "looking_away"} <= types


def test_batch_skips_events_for_an_attempt_that_is_not_the_caller_s(client, seed_roles, admin_token):
    """A tampered/buggy client batching in an attempt_id it doesn't own must
    not fail the whole request (real events sitting alongside it should still
    land) and must not create anything for the attempt it doesn't own."""
    owner_token, owner_attempt = _start_attempt(client, admin_token, email="owner@example.com")
    _, other_attempt = _start_attempt(client, admin_token, email="intruder@example.com")

    response = client.post("/api/v1/proctoring/events/batch", json={
        "events": [
            {"attempt_id": owner_attempt, "event_type": "tab_switch", "description": "mine"},
            {"attempt_id": other_attempt, "event_type": "tab_switch", "description": "not mine"},
        ],
    }, headers=auth_headers(owner_token))
    assert response.status_code == 201, response.text
    assert response.json() == {"created": 1, "received": 2}

    mine = client.get(f"/api/v1/proctoring/events/attempt/{owner_attempt}", headers=auth_headers(owner_token)).json()
    assert any(e["description"] == "mine" for e in mine)


def test_batch_rejects_an_empty_events_list(client, seed_roles, admin_token):
    student_token, _ = _start_attempt(client, admin_token)
    response = client.post(
        "/api/v1/proctoring/events/batch", json={"events": []}, headers=auth_headers(student_token),
    )
    assert response.status_code == 422


def test_batch_rejects_a_batch_over_the_size_cap(client, seed_roles, admin_token):
    student_token, attempt_id = _start_attempt(client, admin_token)
    events = [{"attempt_id": attempt_id, "event_type": "right_click_attempt"} for _ in range(51)]
    response = client.post(
        "/api/v1/proctoring/events/batch", json={"events": events}, headers=auth_headers(student_token),
    )
    assert response.status_code == 422
