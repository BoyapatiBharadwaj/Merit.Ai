"""
The admin dashboard's drill-down endpoints: examiners, candidates, exams,
live sessions, and violations across every organization.
"""
from datetime import datetime, timedelta, timezone

from app.models.exam import Exam
from app.models.student import Student
from app.models.user import User
from tests.conftest import auth_headers
from tests.test_exam_workflow import _build_published_exam, _create_examiner_and_login, _register_student_and_login


def _student_id(db_session, email: str) -> int:
    return db_session.query(User).filter(User.email == email).first().student_profile.id


def _log_violation(client, student_headers, attempt_id: int, event_type: str = "phone_detected") -> None:
    response = client.post("/api/v1/proctoring/events", json={"attempt_id": attempt_id, "event_type": event_type},
                           headers=student_headers)
    assert response.status_code == 201, response.text


# ---------------------------------------------------------------------------
# Access gating
# ---------------------------------------------------------------------------

def test_admin_routes_reject_non_admins(client, seed_roles, admin_token):
    examiner_token = _create_examiner_and_login(client, admin_token, email="not-admin-examiner@example.com")
    student_token = _register_student_and_login(client, email="not-admin-student@example.com")

    for headers in (auth_headers(examiner_token), auth_headers(student_token)):
        assert client.get("/api/v1/admin/examiners", headers=headers).status_code == 403
        assert client.get("/api/v1/admin/candidates", headers=headers).status_code == 403
        assert client.get("/api/v1/admin/live-sessions", headers=headers).status_code == 403
        assert client.get("/api/v1/admin/violations", headers=headers).status_code == 403


def test_admin_routes_reject_unauthenticated(client, seed_roles):
    assert client.get("/api/v1/admin/examiners").status_code == 401


# ---------------------------------------------------------------------------
# Examiners overview + detail
# ---------------------------------------------------------------------------

def test_examiners_overview_lists_counts_and_supports_filters(client, seed_roles, admin_token, db_session):
    examiner_token = _create_examiner_and_login(client, admin_token, email="overview-examiner@example.com")
    exam_id = _build_published_exam(client, auth_headers(examiner_token))
    _register_student_and_login(client, email="overview-student@example.com")
    student_id = _student_id(db_session, "overview-student@example.com")

    rows = client.get("/api/v1/admin/examiners", headers=auth_headers(admin_token)).json()["items"]
    mine = next(r for r in rows if r["email"] == "overview-examiner@example.com")
    assert mine["active_exams"] == 1
    assert mine["upcoming_exams"] == 0
    assert mine["completed_exams"] == 0
    assert mine["candidate_count"] == 1  # enrolled via _build_published_exam's org roster
    assert mine["is_active"] is True

    by_search = client.get("/api/v1/admin/examiners", params={"search": "overview-examiner"},
                           headers=auth_headers(admin_token)).json()["items"]
    assert [r["email"] for r in by_search] == ["overview-examiner@example.com"]

    # Deactivate via the existing user-management endpoint, then filter by status.
    examiner_user_id = db_session.query(User).filter(User.email == "overview-examiner@example.com").first().id
    client.post(f"/api/v1/users/{examiner_user_id}/deactivate", headers=auth_headers(admin_token))
    disabled = client.get("/api/v1/admin/examiners", params={"status": "disabled"}, headers=auth_headers(admin_token)).json()["items"]
    assert "overview-examiner@example.com" in [r["email"] for r in disabled]
    active_only = client.get("/api/v1/admin/examiners", params={"status": "active"}, headers=auth_headers(admin_token)).json()["items"]
    assert "overview-examiner@example.com" not in [r["email"] for r in active_only]

    assert exam_id and student_id  # sanity: both fixtures actually ran


def test_examiner_detail_and_exam_list(client, seed_roles, admin_token):
    examiner_token = _create_examiner_and_login(client, admin_token, email="detail-examiner@example.com")
    headers = auth_headers(examiner_token)
    exam_id = _build_published_exam(client, headers)

    examiners = client.get("/api/v1/admin/examiners", headers=auth_headers(admin_token)).json()["items"]
    examiner_id = next(r["id"] for r in examiners if r["email"] == "detail-examiner@example.com")

    detail = client.get(f"/api/v1/admin/examiners/{examiner_id}", headers=auth_headers(admin_token))
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["total_exams"] == 1
    assert body["active_exams"] == 1

    exams = client.get(f"/api/v1/admin/examiners/{examiner_id}/exams", headers=auth_headers(admin_token)).json()
    assert len(exams) == 1
    assert exams[0]["id"] == exam_id
    assert exams[0]["status"] == "active"

    filtered_out = client.get(f"/api/v1/admin/examiners/{examiner_id}/exams", params={"status": "completed"},
                              headers=auth_headers(admin_token)).json()
    assert filtered_out == []


# ---------------------------------------------------------------------------
# Examiner edit / delete guard
# ---------------------------------------------------------------------------

def test_update_examiner_edits_name_and_organization(client, seed_roles, admin_token):
    examiner_token = _create_examiner_and_login(client, admin_token, email="editme@example.com")
    examiners = client.get("/api/v1/admin/examiners", headers=auth_headers(admin_token)).json()["items"]
    examiner_id = next(r["id"] for r in examiners if r["email"] == "editme@example.com")

    response = client.patch(f"/api/v1/admin/examiners/{examiner_id}",
                            json={"first_name": "Renamed", "organization_name": "New Org Name"},
                            headers=auth_headers(admin_token))
    assert response.status_code == 200, response.text
    assert response.json()["organization_name"] == "New Org Name"
    assert "Renamed" in response.json()["full_name"]
    assert examiner_token  # sanity


def test_delete_examiner_blocked_when_real_attempts_exist_but_allowed_otherwise(client, seed_roles, admin_token):
    # Examiner A: has a real attempt -- delete must be refused.
    token_a = _create_examiner_and_login(client, admin_token, email="delete-blocked@example.com")
    exam_id = _build_published_exam(client, auth_headers(token_a))
    student_token = _register_student_and_login(client, email="attempted@example.com")
    start = client.post(f"/api/v1/attempts/start/{exam_id}", headers=auth_headers(student_token))
    assert start.status_code == 200, start.text

    examiners = client.get("/api/v1/admin/examiners", headers=auth_headers(admin_token)).json()["items"]
    examiner_a_id = next(r["id"] for r in examiners if r["email"] == "delete-blocked@example.com")
    blocked = client.delete(f"/api/v1/admin/examiners/{examiner_a_id}", headers=auth_headers(admin_token))
    assert blocked.status_code == 400
    assert "cannot be deleted" in blocked.json()["detail"].lower()

    # Examiner B: only ever created a draft, no attempts anywhere -- delete succeeds.
    _create_examiner_and_login(client, admin_token, email="delete-allowed@example.com")
    examiners = client.get("/api/v1/admin/examiners", headers=auth_headers(admin_token)).json()["items"]
    examiner_b_id = next(r["id"] for r in examiners if r["email"] == "delete-allowed@example.com")
    allowed = client.delete(f"/api/v1/admin/examiners/{examiner_b_id}", headers=auth_headers(admin_token))
    assert allowed.status_code == 204
    remaining = client.get("/api/v1/admin/examiners", headers=auth_headers(admin_token)).json()["items"]
    assert "delete-allowed@example.com" not in [r["email"] for r in remaining]


# ---------------------------------------------------------------------------
# Exam admin detail + enrolled students
# ---------------------------------------------------------------------------

def test_exam_admin_detail_and_enrolled_students(client, seed_roles, admin_token, db_session):
    examiner_token = _create_examiner_and_login(client, admin_token, email="exam-detail-examiner@example.com")
    headers = auth_headers(examiner_token)
    exam_id = _build_published_exam(client, headers)

    started_token = _register_student_and_login(client, email="started@example.com")
    not_started_token = _register_student_and_login(client, email="not-started@example.com")
    assert not_started_token

    start = client.post(f"/api/v1/attempts/start/{exam_id}", headers=auth_headers(started_token))
    attempt_id = start.json()["attempt_id"]
    # Low-severity on purpose (see proctor_service.SEVERITY_MAP) -- this test
    # checks the "low" tier specifically; risk-tier thresholds themselves are
    # covered by test_a_terminated_attempt_is_forced_to_high_risk_regardless_of_score.
    _log_violation(client, auth_headers(started_token), attempt_id, "copy_paste_attempt")
    submit = client.post(f"/api/v1/attempts/{attempt_id}/submit", headers=auth_headers(started_token))
    assert submit.status_code == 200, submit.text

    detail = client.get(f"/api/v1/admin/exams/{exam_id}", headers=auth_headers(admin_token))
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["enrolled"] == 2
    assert body["attended"] == 1
    assert body["submitted"] == 1
    assert body["absent"] == 1
    assert body["violations"] == 1

    students = client.get(f"/api/v1/admin/exams/{exam_id}/students", headers=auth_headers(admin_token)).json()
    assert len(students) == 2
    started_row = next(r for r in students if r["email"] == "started@example.com")
    not_started_row = next(r for r in students if r["email"] == "not-started@example.com")
    assert started_row["exam_status"] == "Completed"
    assert started_row["violations"] == 1
    assert started_row["risk"] == "low"
    assert not_started_row["exam_status"] == "Not Started"
    assert not_started_row["violations"] == 0
    assert not_started_row["risk"] is None

    only_completed = client.get(f"/api/v1/admin/exams/{exam_id}/students", params={"exam_status": "completed"},
                                headers=auth_headers(admin_token)).json()
    assert [r["email"] for r in only_completed] == ["started@example.com"]


def test_a_terminated_attempt_is_forced_to_high_risk_regardless_of_score(client, seed_roles, admin_token, db_session):
    """risk_score_and_tier's deliberate override: a lockdown termination is
    itself the strongest signal this app can raise, so it always reads High
    even if very few points were logged."""
    examiner_token = _create_examiner_and_login(client, admin_token, email="terminate-examiner@example.com")
    exam_id = _build_published_exam(client, auth_headers(examiner_token))
    student_token = _register_student_and_login(client, email="terminated-student@example.com")
    start = client.post(f"/api/v1/attempts/start/{exam_id}", headers=auth_headers(student_token))
    attempt_id = start.json()["attempt_id"]

    attempt = db_session.query(Exam).filter(Exam.id == exam_id).first().attempts[0]
    from app.models.enums import AttemptStatus
    attempt.status = AttemptStatus.TERMINATED
    db_session.commit()

    students = client.get(f"/api/v1/admin/exams/{exam_id}/students", headers=auth_headers(admin_token)).json()
    row = next(r for r in students if r["email"] == "terminated-student@example.com")
    assert row["exam_status"] == "Terminated"
    assert row["risk"] == "high"
    assert attempt_id


# ---------------------------------------------------------------------------
# Candidates
# ---------------------------------------------------------------------------

def test_candidates_overview_and_detail(client, seed_roles, admin_token, db_session):
    examiner_token = _create_examiner_and_login(client, admin_token, email="cand-examiner@example.com")
    exam_id = _build_published_exam(client, auth_headers(examiner_token))
    student_token = _register_student_and_login(client, email="candidate-view@example.com")
    start = client.post(f"/api/v1/attempts/start/{exam_id}", headers=auth_headers(student_token))
    attempt_id = start.json()["attempt_id"]
    _log_violation(client, auth_headers(student_token), attempt_id, "tab_switch")
    client.post(f"/api/v1/attempts/{attempt_id}/submit", headers=auth_headers(student_token))

    overview = client.get("/api/v1/admin/candidates", headers=auth_headers(admin_token)).json()["items"]
    row = next(r for r in overview if r["email"] == "candidate-view@example.com")
    assert row["exams_taken"] == 1
    assert row["completed_exams"] == 1
    assert row["total_violations"] == 1

    student_id = _student_id(db_session, "candidate-view@example.com")
    detail = client.get(f"/api/v1/admin/candidates/{student_id}", headers=auth_headers(admin_token))
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["total_exams"] == 1
    assert len(body["history"]) == 1
    assert body["history"][0]["exam_title"]
    assert body["history"][0]["violations"] == 1


# ---------------------------------------------------------------------------
# Live sessions + violations
# ---------------------------------------------------------------------------

def test_live_sessions_lists_only_in_progress_attempts(client, seed_roles, admin_token):
    examiner_token = _create_examiner_and_login(client, admin_token, email="live-examiner@example.com")
    exam_id = _build_published_exam(client, auth_headers(examiner_token))
    in_progress_token = _register_student_and_login(client, email="live-in-progress@example.com")
    finished_token = _register_student_and_login(client, email="live-finished@example.com")

    client.post(f"/api/v1/attempts/start/{exam_id}", headers=auth_headers(in_progress_token))
    finished_start = client.post(f"/api/v1/attempts/start/{exam_id}", headers=auth_headers(finished_token))
    client.post(f"/api/v1/attempts/{finished_start.json()['attempt_id']}/submit", headers=auth_headers(finished_token))

    live = client.get("/api/v1/admin/live-sessions", headers=auth_headers(admin_token)).json()
    assert len(live) == 1
    assert live[0]["exam_title"]
    assert live[0]["elapsed_minutes"] is not None
    assert live[0]["student_id"] is not None


def test_violations_overview_and_admin_decision_update(client, seed_roles, admin_token):
    examiner_token = _create_examiner_and_login(client, admin_token, email="violations-examiner@example.com")
    exam_id = _build_published_exam(client, auth_headers(examiner_token))
    student_token = _register_student_and_login(client, email="violations-student@example.com")
    start = client.post(f"/api/v1/attempts/start/{exam_id}", headers=auth_headers(student_token))
    attempt_id = start.json()["attempt_id"]
    _log_violation(client, auth_headers(student_token), attempt_id, "phone_detected")

    all_violations = client.get("/api/v1/admin/violations", headers=auth_headers(admin_token)).json()["items"]
    assert len(all_violations) == 1
    event = all_violations[0]
    assert event["admin_decision"] == "pending"
    assert event["severity"] == "high"  # phone_detected is mapped High in proctor_service.SEVERITY_MAP

    # A DIFFERENT examiner (not this exam's owner) cannot set the decision --
    # violations now live only inside the owning examiner's own per-attempt
    # review, and this is the ownership boundary that protects it.
    other_examiner_token = _create_examiner_and_login(client, admin_token, email="other-violations-examiner@example.com")
    forbidden = client.patch(f"/api/v1/proctoring/events/{event['id']}/decision", json={"decision": "confirmed"},
                             headers=auth_headers(other_examiner_token))
    assert forbidden.status_code == 403

    # The exam's OWNING examiner can, though -- this is exactly the action
    # they take from their per-student attempt review (see
    # GET /attempts/{id}/staff-report and PATCH .../decision no longer being
    # admin-only).
    updated = client.patch(f"/api/v1/proctoring/events/{event['id']}/decision", json={"decision": "confirmed"},
                           headers=auth_headers(examiner_token))
    assert updated.status_code == 200, updated.text
    assert updated.json()["admin_decision"] == "confirmed"

    # And so can an admin, on any exam.
    admin_updated = client.patch(f"/api/v1/proctoring/events/{event['id']}/decision", json={"decision": "pending"},
                                 headers=auth_headers(admin_token))
    assert admin_updated.status_code == 200, admin_updated.text
    assert admin_updated.json()["admin_decision"] == "pending"

    updated = client.patch(f"/api/v1/proctoring/events/{event['id']}/decision", json={"decision": "confirmed"},
                           headers=auth_headers(admin_token))
    assert updated.status_code == 200, updated.text

    only_confirmed = client.get("/api/v1/admin/violations", params={"decision": "confirmed"},
                                headers=auth_headers(admin_token)).json()["items"]
    assert len(only_confirmed) == 1
    only_dismissed = client.get("/api/v1/admin/violations", params={"decision": "dismissed"},
                                headers=auth_headers(admin_token)).json()["items"]
    assert only_dismissed == []

    bad_decision = client.patch(f"/api/v1/proctoring/events/{event['id']}/decision", json={"decision": "not-a-real-value"},
                                headers=auth_headers(admin_token))
    assert bad_decision.status_code == 422


# ---------------------------------------------------------------------------
# Staff report + comments
# ---------------------------------------------------------------------------

def test_staff_report_visible_to_admin_and_owning_examiner_not_others(client, seed_roles, admin_token):
    examiner_token = _create_examiner_and_login(client, admin_token, email="report-examiner@example.com")
    other_examiner_token = _create_examiner_and_login(client, admin_token, email="report-other-examiner@example.com")
    exam_id = _build_published_exam(client, auth_headers(examiner_token))
    student_token = _register_student_and_login(client, email="report-student@example.com")
    start = client.post(f"/api/v1/attempts/start/{exam_id}", headers=auth_headers(student_token))
    attempt_id = start.json()["attempt_id"]
    _log_violation(client, auth_headers(student_token), attempt_id, "tab_switch")

    admin_view = client.get(f"/api/v1/attempts/{attempt_id}/staff-report", headers=auth_headers(admin_token))
    assert admin_view.status_code == 200, admin_view.text
    body = admin_view.json()
    assert body["risk_tier"] in ("low", "medium", "high")
    assert body["proctoring_summary"]
    assert len(body["violation_timeline"]) == 1
    assert "face_registered" in body

    owner_view = client.get(f"/api/v1/attempts/{attempt_id}/staff-report", headers=auth_headers(examiner_token))
    assert owner_view.status_code == 200

    other_examiner_view = client.get(f"/api/v1/attempts/{attempt_id}/staff-report", headers=auth_headers(other_examiner_token))
    assert other_examiner_view.status_code == 404

    student_view = client.get(f"/api/v1/attempts/{attempt_id}/staff-report", headers=auth_headers(student_token))
    assert student_view.status_code == 404


def test_attempt_comment_is_role_scoped_to_separate_fields(client, seed_roles, admin_token):
    examiner_token = _create_examiner_and_login(client, admin_token, email="comment-examiner@example.com")
    other_examiner_token = _create_examiner_and_login(client, admin_token, email="comment-other-examiner@example.com")
    exam_id = _build_published_exam(client, auth_headers(examiner_token))
    student_token = _register_student_and_login(client, email="comment-student@example.com")
    start = client.post(f"/api/v1/attempts/start/{exam_id}", headers=auth_headers(student_token))
    attempt_id = start.json()["attempt_id"]

    examiner_comment = client.patch(f"/api/v1/attempts/{attempt_id}/comment", json={"comment": "Saw them glance away once."},
                                    headers=auth_headers(examiner_token))
    assert examiner_comment.status_code == 200, examiner_comment.text
    assert examiner_comment.json()["examiner_comment"] == "Saw them glance away once."
    assert examiner_comment.json()["admin_comment"] is None

    admin_comment = client.patch(f"/api/v1/attempts/{attempt_id}/comment", json={"comment": "Reviewed, no action needed."},
                                 headers=auth_headers(admin_token))
    assert admin_comment.status_code == 200, admin_comment.text
    assert admin_comment.json()["admin_comment"] == "Reviewed, no action needed."
    # The examiner's earlier note must still be there -- one role's write
    # never clobbers the other's column.
    assert admin_comment.json()["examiner_comment"] == "Saw them glance away once."

    forbidden = client.patch(f"/api/v1/attempts/{attempt_id}/comment", json={"comment": "Not my exam."},
                             headers=auth_headers(other_examiner_token))
    assert forbidden.status_code == 403

    student_forbidden = client.patch(f"/api/v1/attempts/{attempt_id}/comment", json={"comment": "trying anyway"},
                                     headers=auth_headers(student_token))
    assert student_forbidden.status_code == 403


# ---------------------------------------------------------------------------
# Dashboard summary + platform-wide exams list
# ---------------------------------------------------------------------------

def test_dashboard_summary_counts_match_the_drilldown_endpoints(client, seed_roles, admin_token):
    examiner_token = _create_examiner_and_login(client, admin_token, email="summary-examiner@example.com")
    exam_id = _build_published_exam(client, auth_headers(examiner_token))
    student_token = _register_student_and_login(client, email="summary-student@example.com")
    start = client.post(f"/api/v1/attempts/start/{exam_id}", headers=auth_headers(student_token))
    attempt_id = start.json()["attempt_id"]
    _log_violation(client, auth_headers(student_token), attempt_id, "tab_switch")

    summary = client.get("/api/v1/admin/summary", headers=auth_headers(admin_token))
    assert summary.status_code == 200, summary.text
    body = summary.json()

    # Compared against each list's `total`, not the length of a page.
    # These endpoints are paginated now, so len(items) is the page size and
    # would only agree with the summary by coincidence -- while still looking
    # like a real cross-check.
    examiners = client.get("/api/v1/admin/examiners", headers=auth_headers(admin_token)).json()
    candidates = client.get("/api/v1/admin/candidates", headers=auth_headers(admin_token)).json()
    live = client.get("/api/v1/admin/live-sessions", headers=auth_headers(admin_token)).json()
    violations = client.get("/api/v1/admin/violations", headers=auth_headers(admin_token)).json()

    assert body["total_examiners"] == examiners["total"]
    assert body["total_candidates"] == candidates["total"]
    assert body["live_sessions"] == len(live) == 1  # attempt above is still in progress
    assert body["violations_logged"] == violations["total"]
    assert body["active_exams"] >= 1  # the exam built above is published + active


def test_platform_wide_exams_list_filters_by_status_and_search(client, seed_roles, admin_token):
    examiner_token = _create_examiner_and_login(client, admin_token, email="platform-exams-examiner@example.com")
    exam_id = _build_published_exam(client, auth_headers(examiner_token))

    all_exams = client.get("/api/v1/admin/exams", headers=auth_headers(admin_token)).json()
    mine = next(r for r in all_exams if r["id"] == exam_id)
    assert mine["examiner_name"]
    assert mine["examiner_id"]
    assert mine["status"] == "active"

    active_only = client.get("/api/v1/admin/exams", params={"status": "active"}, headers=auth_headers(admin_token)).json()
    assert exam_id in [r["id"] for r in active_only]
    completed_only = client.get("/api/v1/admin/exams", params={"status": "completed"}, headers=auth_headers(admin_token)).json()
    assert exam_id not in [r["id"] for r in completed_only]

    # _build_published_exam always titles the exam "Intro Quiz" and
    # _create_examiner_and_login always names the examiner "Test Examiner"
    # regardless of the email passed in -- search on those fixed, known
    # strings rather than the per-test-unique email fragment.
    by_title = client.get("/api/v1/admin/exams", params={"search": "intro quiz"},
                          headers=auth_headers(admin_token)).json()
    assert exam_id in [r["id"] for r in by_title]
    by_examiner_name = client.get("/api/v1/admin/exams", params={"search": "test examiner"},
                                  headers=auth_headers(admin_token)).json()
    assert exam_id in [r["id"] for r in by_examiner_name]
    no_match = client.get("/api/v1/admin/exams", params={"search": "no-such-exam-xyz"},
                          headers=auth_headers(admin_token)).json()
    assert exam_id not in [r["id"] for r in no_match]


# --- pagination and the N+1s --------------------------------------------------

def test_admin_lists_come_back_one_page_at_a_time(client, seed_roles, admin_token):
    """These returned whole tables and the browser sliced them, so viewing ten
    rows cost the transfer and parse of every candidate the institution had."""
    for index in range(7):
        _register_student_and_login(client, email=f"page{index}@example.com")

    first = client.get("/api/v1/admin/candidates?page=1&page_size=3",
                       headers=auth_headers(admin_token))
    assert first.status_code == 200, first.text
    body = first.json()
    assert len(body["items"]) == 3
    assert body["total"] == 7
    assert body["total_pages"] == 3

    # Every candidate appears exactly once across the pages.
    seen = []
    for page in (1, 2, 3):
        seen += [row["id"] for row in
                 client.get(f"/api/v1/admin/candidates?page={page}&page_size=3",
                            headers=auth_headers(admin_token)).json()["items"]]
    assert len(seen) == len(set(seen)) == 7


def test_the_client_cannot_request_an_unbounded_admin_page(client, seed_roles, admin_token):
    for path in ("candidates", "examiners", "violations"):
        assert client.get(f"/api/v1/admin/{path}?page_size=100000",
                          headers=auth_headers(admin_token)).status_code == 422


def test_an_admin_search_wildcard_is_escaped(client, seed_roles, admin_token):
    """`%` and `_` are LIKE wildcards, so an unescaped search box lets any input
    become a pattern -- "%" matched every row."""
    _register_student_and_login(client, email="literal-admin@example.com")
    wildcard = client.get("/api/v1/admin/candidates?search=%25",
                          headers=auth_headers(admin_token)).json()
    assert wildcard["total"] == 0, "a bare % matched every candidate"


def test_the_examiner_list_does_not_query_per_row(client, seed_roles, admin_token, db_session):
    """examiners_overview called candidate_ids_for_examiner inside its loop --
    two queries per examiner, so a page of a hundred staff meant two hundred
    round trips to render one table, degrading linearly with exactly the thing
    an admin dashboard exists to show more of."""
    from sqlalchemy import event

    for index in range(5):
        _create_examiner_and_login(client, admin_token, email=f"many{index}@example.com")

    statements = []
    engine = db_session.get_bind()

    def _count(conn, cursor, statement, *args):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", _count)
    try:
        response = client.get("/api/v1/admin/examiners?page_size=100",
                              headers=auth_headers(admin_token))
    finally:
        event.remove(engine, "before_cursor_execute", _count)

    assert response.status_code == 200, response.text
    assert response.json()["total"] == 5
    # Comfortably under "two per examiner plus overhead". The point is that the
    # count does not scale with the number of rows returned.
    assert len(statements) < 20, f"{len(statements)} statements for 5 examiners -- still N+1"
