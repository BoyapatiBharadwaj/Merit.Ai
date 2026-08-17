"""Analytics/reporting endpoints: exam-level and platform-level aggregation."""
from tests.conftest import auth_headers
from tests.test_exam_workflow import _build_published_exam, _create_examiner_and_login, _register_student_and_login


def _run_and_score_full_marks(client, exam_id, student_headers):
    start = client.post(f"/api/v1/attempts/start/{exam_id}", headers=student_headers)
    assert start.status_code == 200, start.text
    attempt = start.json()
    question_id = attempt["question_ids_in_order"][0]
    question = client.get(f"/api/v1/attempts/{attempt['attempt_id']}/question/{question_id}", headers=student_headers).json()
    correct_option = next(o["id"] for o in question["options"] if o["text"] == "4")
    client.put(f"/api/v1/attempts/{attempt['attempt_id']}/answer", json={
        "question_id": question_id, "selected_option_id": correct_option,
    }, headers=student_headers)
    submit = client.post(f"/api/v1/attempts/{attempt['attempt_id']}/submit", headers=student_headers)
    assert submit.status_code == 200, submit.text
    return attempt["attempt_id"]


def test_exam_analytics_reflects_a_completed_attempt(client, seed_roles, admin_token):
    examiner_headers = auth_headers(_create_examiner_and_login(client, admin_token))
    exam_id = _build_published_exam(client, examiner_headers)
    student_headers = auth_headers(_register_student_and_login(client))
    _run_and_score_full_marks(client, exam_id, student_headers)

    response = client.get(f"/api/v1/analytics/exams/{exam_id}", headers=examiner_headers)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["total_attempts"] == 1
    assert data["completed_results"] == 1
    assert data["average_percentage"] == 100.0
    assert data["min_percentage"] == 100.0
    assert data["max_percentage"] == 100.0
    # Band labels changed with the bucketing fix: they used to be
    # (0,20), (21,40)... matched inclusively at both ends, which
    # left every decimal percentage between bands counted nowhere.
    top_bucket = next(bucket for bucket in data["score_distribution"] if bucket["range"] == "80-100%")
    assert top_bucket["count"] == 1
    assert sum(bucket["count"] for bucket in data["score_distribution"]) == 1


def test_exam_analytics_hidden_from_non_owning_examiner(client, seed_roles, admin_token):
    owner_headers = auth_headers(_create_examiner_and_login(client, admin_token, email="owner2@example.com"))
    other_headers = auth_headers(_create_examiner_and_login(client, admin_token, email="other2@example.com"))
    exam_id = _build_published_exam(client, owner_headers)

    response = client.get(f"/api/v1/analytics/exams/{exam_id}", headers=other_headers)
    assert response.status_code == 404


def test_platform_analytics_is_admin_only(client, seed_roles, admin_token):
    examiner_headers = auth_headers(_create_examiner_and_login(client, admin_token))
    forbidden = client.get("/api/v1/analytics/platform", headers=examiner_headers)
    assert forbidden.status_code == 403

    response = client.get("/api/v1/analytics/platform", headers=auth_headers(admin_token))
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["total_examiners"] >= 1


def test_platform_analytics_counts_exams_and_violations(client, seed_roles, admin_token):
    examiner_headers = auth_headers(_create_examiner_and_login(client, admin_token))
    exam_id = _build_published_exam(client, examiner_headers)
    student_headers = auth_headers(_register_student_and_login(client))
    attempt_id = _run_and_score_full_marks(client, exam_id, student_headers)

    log = client.post("/api/v1/proctoring/events", json={
        "attempt_id": attempt_id, "event_type": "tab_switch", "description": "test",
    }, headers=student_headers)
    assert log.status_code == 201, log.text

    response = client.get("/api/v1/analytics/platform", headers=auth_headers(admin_token))
    data = response.json()
    assert data["total_attempts"] >= 1
    assert data["total_violations"] >= 1
    assert data["violations_by_type"].get("tab_switch", 0) >= 1
    assert data["exams_by_status"].get("published", 0) >= 1


def test_noise_detected_loud_is_a_valid_event_type_logged_as_high_severity(client, seed_roles, admin_token):
    """The second, louder noise tier (proctoring.js) needs its own
    EventType value (added in migration 0007) to actually be accepted
    by this endpoint, and must come back tagged "high" severity.
    """
    examiner_headers = auth_headers(_create_examiner_and_login(client, admin_token))
    exam_id = _build_published_exam(client, examiner_headers)
    student_headers = auth_headers(_register_student_and_login(client))
    attempt_id = _run_and_score_full_marks(client, exam_id, student_headers)

    log = client.post("/api/v1/proctoring/events", json={
        "attempt_id": attempt_id, "event_type": "noise_detected_loud", "description": "test",
    }, headers=student_headers)
    assert log.status_code == 201, log.text
    assert log.json()["severity"] == "high"

    events = client.get(f"/api/v1/proctoring/events/attempt/{attempt_id}", headers=student_headers).json()
    match = next(e for e in events if e["id"] == log.json()["id"])
    assert match["event_type"] == "noise_detected_loud"
    assert match["severity"] == "high"


def test_violation_event_accepts_a_screenshot_in_the_request_body(client, seed_roles, admin_token):
    """screenshot_base64 used to be bound as a query-string parameter (a FastAPI footgun."""
    examiner_headers = auth_headers(_create_examiner_and_login(client, admin_token))
    exam_id = _build_published_exam(client, examiner_headers)
    student_headers = auth_headers(_register_student_and_login(client))
    attempt_id = _run_and_score_full_marks(client, exam_id, student_headers)

    # Smallest possible valid JPEG-ish payload -- content doesn't matter to
    # this test, only that it's valid base64 the service can decode and write.
    tiny_image_b64 = "/9j/4AAQSkZJRgABAQEAYABgAAD/2wBDAAMCAgICAgMCAgIDAwMDBAYEBAQEBAgGBgUGCQgKCgkICQkKDA8MCgsOCwkJDRENDg8QEBEQCgwSExIQEw8QEBD/2wBDAQMDAwQDBAgEBAgQCwkLEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBD/wAARCAABAAEDASIAAhEBAxEB/8QAFQABAQAAAAAAAAAAAAAAAAAAAAj/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/8QAFQEBAQAAAAAAAAAAAAAAAAAAAAX/xAAUEQEAAAAAAAAAAAAAAAAAAAAA/9oADAMBAAIRAxEAPwCdABmX/9k="

    log = client.post("/api/v1/proctoring/events", json={
        "attempt_id": attempt_id, "event_type": "tab_switch", "description": "test",
        "screenshot_base64": tiny_image_b64,
    }, headers=student_headers)
    assert log.status_code == 201, log.text
    event_id = log.json()["id"]

    # The screenshot is attached by a BackgroundTask; TestClient runs those
    # synchronously as part of the request/response cycle, so it's already
    # written and linked by the time this GET runs.
    events = client.get(f"/api/v1/proctoring/events/attempt/{attempt_id}", headers=student_headers).json()
    match = next(e for e in events if e["id"] == event_id)
    assert match["screenshot_path"] is not None
    assert match["screenshot_path"].endswith(".jpg")
