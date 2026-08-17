"""The examiner module's own defects, each pinned by what it cost."""
from datetime import datetime, timedelta, timezone

import pytest

from tests.conftest import auth_headers
from tests.test_exam_workflow import _create_examiner_and_login, _register_student_and_login


@pytest.fixture
def examiner(client, seed_roles, admin_token):
    return auth_headers(_create_examiner_and_login(client, admin_token))


def _exam_with_questions(client, examiner, *, count=2, publish=True, title="Examiner"):
    exam = client.post("/api/v1/exams", json={
        "title": title, "duration_minutes": 60, "proctoring_enabled": False,
    }, headers=examiner).json()
    section = client.post(f"/api/v1/exams/{exam['id']}/sections", json={"title": "Main"},
                          headers=examiner).json()
    for index in range(count):
        client.post(f"/api/v1/exams/sections/{section['id']}/questions", json={
            "text": f"Q{index}", "marks": 5, "question_type": "mcq",
            "options": [{"text": "right", "is_correct": True}, {"text": "wrong", "is_correct": False}],
        }, headers=examiner)
    if publish:
        client.post(f"/api/v1/exams/{exam['id']}/publish", headers=examiner)
    return exam["id"], section["id"]


# --- the reset that destroyed the evidence ------------------------------------

def test_a_reset_keeps_the_previous_attempt_and_its_answers(client, examiner, seed_roles):
    """The whole point. Before, this data did not survive the reset at all."""
    exam_id, _ = _exam_with_questions(client, examiner)
    student = auth_headers(_register_student_and_login(client, email="reset-me@example.com"))
    start = client.post(f"/api/v1/attempts/start/{exam_id}", headers=student).json()
    attempt_id = start["attempt_id"]

    question_id = start["question_ids_in_order"][0]
    question = client.get(f"/api/v1/attempts/{attempt_id}/question/{question_id}",
                          headers=student).json()
    client.put(f"/api/v1/attempts/{attempt_id}/answer", json={
        "question_id": question_id, "selected_option_id": question["options"][0]["id"],
        "answer_version": 1,
    }, headers=student)
    client.post(f"/api/v1/attempts/{attempt_id}/submit", headers=student)

    students = client.get("/api/v1/users/students", headers=examiner)
    student_id = client.get("/api/v1/users/me", headers=student).json()["id"]
    profile = client.get(f"/api/v1/attempts/exam/{exam_id}", headers=examiner).json()
    target_student_id = profile["items"][0]["student_id"]

    reset = client.post(f"/api/v1/attempts/exam/{exam_id}/student/{target_student_id}/reset",
                        json={"reason": "Power cut in the hall"}, headers=examiner)
    assert reset.status_code == 200, reset.text

    archived = client.get(f"/api/v1/attempts/exam/{exam_id}/archived", headers=examiner)
    assert archived.status_code == 200, archived.text
    rows = archived.json()
    assert len(rows) == 1, "the previous attempt was destroyed rather than archived"
    assert rows[0]["attempt_id"] == attempt_id
    assert rows[0]["scored_marks"] is not None, "the result went with it"

    # And the full staff report for it is still readable.
    report = client.get(f"/api/v1/attempts/{attempt_id}/staff-report", headers=examiner)
    assert report.status_code == 200, "the archived attempt's report is gone"


def test_a_reset_lets_the_student_start_again(client, examiner, seed_roles):
    """Archiving must not break the thing the reset was granted FOR."""
    exam_id, _ = _exam_with_questions(client, examiner)
    student = auth_headers(_register_student_and_login(client, email="retake@example.com"))
    client.post(f"/api/v1/attempts/start/{exam_id}", headers=student)
    client.post(f"/api/v1/attempts/{client.get('/api/v1/attempts/my', headers=student).json()[0]['attempt_id']}/submit",
                headers=student)

    student_id = client.get(f"/api/v1/attempts/exam/{exam_id}", headers=examiner).json()["items"][0]["student_id"]
    client.post(f"/api/v1/attempts/exam/{exam_id}/student/{student_id}/reset",
                json={"reason": "Disconnection"}, headers=examiner)

    retake = client.post(f"/api/v1/attempts/start/{exam_id}", headers=student)
    assert retake.status_code == 200, retake.text


def test_an_archived_attempt_does_not_count_twice(client, examiner, seed_roles):
    """It must disappear from the live list and the candidate's own results, or
    a reset candidate appears twice and their abandoned score is counted."""
    exam_id, _ = _exam_with_questions(client, examiner)
    student = auth_headers(_register_student_and_login(client, email="once@example.com"))
    client.post(f"/api/v1/attempts/start/{exam_id}", headers=student)
    student_id = client.get(f"/api/v1/attempts/exam/{exam_id}", headers=examiner).json()["items"][0]["student_id"]

    client.post(f"/api/v1/attempts/exam/{exam_id}/student/{student_id}/reset",
                json={"reason": "Crash"}, headers=examiner)

    assert client.get(f"/api/v1/attempts/exam/{exam_id}", headers=examiner).json()["items"] == []
    assert client.get("/api/v1/attempts/my", headers=student).json() == []


# --- exam lifecycle -----------------------------------------------------------

def test_an_examiner_can_close_and_reopen_an_exam(client, examiner, seed_roles):
    """CLOSED existed in the schema with no way to reach it, so a published exam
    stayed on every candidate's list forever."""
    exam_id, _ = _exam_with_questions(client, examiner)

    closed = client.post(f"/api/v1/exams/{exam_id}/close", headers=examiner)
    assert closed.status_code == 200, closed.text
    assert closed.json()["status"] == "closed"

    student = auth_headers(_register_student_and_login(client, email="late@example.com"))
    assert client.post(f"/api/v1/attempts/start/{exam_id}", headers=student).status_code == 404

    reopened = client.post(f"/api/v1/exams/{exam_id}/reopen", headers=examiner)
    assert reopened.status_code == 200
    assert reopened.json()["status"] == "published"
    assert client.post(f"/api/v1/attempts/start/{exam_id}", headers=student).status_code == 200


def test_closing_refuses_while_a_candidate_is_still_writing(client, examiner, seed_roles):
    """Closing out from under a live candidate is the participant bug in another
    costume. It takes a deliberate second action, not a surprise."""
    exam_id, _ = _exam_with_questions(client, examiner)
    student = auth_headers(_register_student_and_login(client, email="writing2@example.com"))
    client.post(f"/api/v1/attempts/start/{exam_id}", headers=student)

    refused = client.post(f"/api/v1/exams/{exam_id}/close", headers=examiner)
    assert refused.status_code == 409
    assert "still sitting" in refused.json()["detail"]

    forced = client.post(f"/api/v1/exams/{exam_id}/close?force=true", headers=examiner)
    assert forced.status_code == 200


def test_a_closed_exam_cannot_be_returned_to_draft(client, examiner, seed_roles):
    """Reopening goes to PUBLISHED only. Draft would unlock questions that
    graded results already reference."""
    exam_id, _ = _exam_with_questions(client, examiner)
    client.post(f"/api/v1/exams/{exam_id}/close", headers=examiner)
    assert client.post(f"/api/v1/exams/{exam_id}/reopen", headers=examiner).json()["status"] == "published"


def test_a_draft_cannot_be_closed(client, examiner, seed_roles):
    exam_id, _ = _exam_with_questions(client, examiner, publish=False)
    assert client.post(f"/api/v1/exams/{exam_id}/close", headers=examiner).status_code == 400


def test_another_examiner_cannot_close_your_exam(client, examiner, seed_roles, admin_token):
    exam_id, _ = _exam_with_questions(client, examiner)
    other = auth_headers(_create_examiner_and_login(client, admin_token, email="other-ex@example.com"))
    assert client.post(f"/api/v1/exams/{exam_id}/close", headers=other).status_code == 404


# --- ordering -----------------------------------------------------------------

def test_sections_get_increasing_order_indexes(client, examiner, seed_roles):
    """Every section was created with order_index 0, and Exam.sections orders by
    that column -- so the order was whatever the database returned, and could
    differ between two loads of the same page."""
    exam = client.post("/api/v1/exams", json={
        "title": "Ordered", "duration_minutes": 30, "proctoring_enabled": False,
    }, headers=examiner).json()
    titles = ["First", "Second", "Third"]
    for title in titles:
        client.post(f"/api/v1/exams/{exam['id']}/sections", json={"title": title}, headers=examiner)

    detail = client.get(f"/api/v1/exams/{exam['id']}", headers=examiner).json()
    assert [s["title"] for s in detail["sections"]] == titles
    assert [s["order_index"] for s in detail["sections"]] == [0, 1, 2]


def test_questions_get_increasing_order_indexes(client, examiner, seed_roles):
    exam_id, section_id = _exam_with_questions(client, examiner, count=3, publish=False)
    detail = client.get(f"/api/v1/exams/{exam_id}", headers=examiner).json()
    orders = [q["order_index"] for q in detail["sections"][0]["questions"]]
    assert orders == sorted(orders) and len(set(orders)) == 3, f"questions share an order: {orders}"


def test_sections_can_be_reordered(client, examiner, seed_roles):
    exam = client.post("/api/v1/exams", json={
        "title": "Reorder", "duration_minutes": 30, "proctoring_enabled": False,
    }, headers=examiner).json()
    ids = [client.post(f"/api/v1/exams/{exam['id']}/sections", json={"title": t},
                       headers=examiner).json()["id"] for t in ("A", "B", "C")]

    reordered = client.put(f"/api/v1/exams/{exam['id']}/sections/reorder",
                           json={"section_ids": [ids[2], ids[0], ids[1]]}, headers=examiner)
    assert reordered.status_code == 200, reordered.text
    assert [s["title"] for s in reordered.json()["sections"]] == ["C", "A", "B"]


def test_reordering_must_name_every_section(client, examiner, seed_roles):
    """A partial list would silently leave sections at stale indexes."""
    exam = client.post("/api/v1/exams", json={
        "title": "Partial", "duration_minutes": 30, "proctoring_enabled": False,
    }, headers=examiner).json()
    ids = [client.post(f"/api/v1/exams/{exam['id']}/sections", json={"title": t},
                       headers=examiner).json()["id"] for t in ("A", "B")]

    assert client.put(f"/api/v1/exams/{exam['id']}/sections/reorder",
                      json={"section_ids": [ids[0]]}, headers=examiner).status_code == 400


# --- transactional question creation ------------------------------------------

def test_a_question_and_its_options_are_written_together(client, examiner, seed_roles,
                                                        db_session, monkeypatch):
    """A failure part-way through adding options must leave NO question."""
    from app.models.question import Question
    from app.repositories import exam_repository

    exam_id, section_id = _exam_with_questions(client, examiner, count=0, publish=False)

    calls = {"n": 0}
    real_option = exam_repository.Option

    class ExplodingOption(real_option):
        def __init__(self, *args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 2:  # the question and the first option are already in
                raise RuntimeError("database went away mid-insert")
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(exam_repository, "Option", ExplodingOption)
    with pytest.raises(RuntimeError):
        exam_repository.add_question(
            db_session, section_id, "Doomed", 5, 0, question_type="mcq",
            options=[{"text": "a", "is_correct": True}, {"text": "b", "is_correct": False}],
        )
    monkeypatch.setattr(exam_repository, "Option", real_option)

    survivors = db_session.query(Question).filter(Question.section_id == section_id).all()
    assert survivors == [], "a half-built question survived the failure"


# --- bulk import --------------------------------------------------------------

def test_a_bulk_import_is_all_or_nothing(client, examiner, seed_roles):
    """One request per question meant a failure at 40 of 60 left the first 39
    written, with nothing to say where it stopped."""
    exam_id, section_id = _exam_with_questions(client, examiner, count=0, publish=False)

    response = client.post(f"/api/v1/exams/sections/{section_id}/questions/bulk", json={
        "questions": [
            {"text": "Good one", "marks": 1,
             "options": [{"text": "a", "is_correct": True}, {"text": "b", "is_correct": False}]},
            {"text": "Two correct on an MCQ", "marks": 1,
             "options": [{"text": "a", "is_correct": True}, {"text": "b", "is_correct": True}]},
        ],
    }, headers=examiner)
    assert response.status_code == 400, response.text

    detail = client.get(f"/api/v1/exams/{exam_id}", headers=examiner).json()
    assert detail["sections"][0]["questions"] == [], "a rejected import still wrote the valid rows"


def test_a_bulk_import_reports_every_problem_at_once(client, examiner, seed_roles):
    """Fixing a sixty-question import one error per attempt is worse than the
    partial writes it replaced."""
    _, section_id = _exam_with_questions(client, examiner, count=0, publish=False)

    response = client.post(f"/api/v1/exams/sections/{section_id}/questions/bulk", json={
        "questions": [
            {"text": "No correct answer", "marks": 1,
             "options": [{"text": "a", "is_correct": False}, {"text": "b", "is_correct": False}]},
            {"text": "Two correct", "marks": 1,
             "options": [{"text": "a", "is_correct": True}, {"text": "b", "is_correct": True}]},
        ],
    }, headers=examiner)
    problems = response.json()["detail"]["problems"]
    assert len(problems) == 2
    assert problems[0].startswith("Question 1:") and problems[1].startswith("Question 2:")


def test_a_valid_bulk_import_writes_everything_in_order(client, examiner, seed_roles):
    exam_id, section_id = _exam_with_questions(client, examiner, count=0, publish=False)

    response = client.post(f"/api/v1/exams/sections/{section_id}/questions/bulk", json={
        "questions": [
            {"text": f"Q{i}", "marks": 2,
             "options": [{"text": "a", "is_correct": True}, {"text": "b", "is_correct": False}]}
            for i in range(5)
        ],
    }, headers=examiner)
    assert response.status_code == 201, response.text
    assert response.json()["imported"] == 5

    questions = client.get(f"/api/v1/exams/{exam_id}", headers=examiner).json()["sections"][0]["questions"]
    assert [q["text"] for q in questions] == [f"Q{i}" for i in range(5)]


def test_bulk_import_supports_multiple_correct_answers(client, examiner, seed_roles):
    _, section_id = _exam_with_questions(client, examiner, count=0, publish=False)
    response = client.post(f"/api/v1/exams/sections/{section_id}/questions/bulk", json={
        "questions": [{
            "text": "Pick two", "marks": 4, "question_type": "multi_select",
            "options": [{"text": "a", "is_correct": True}, {"text": "b", "is_correct": True},
                        {"text": "c", "is_correct": False}],
        }],
    }, headers=examiner)
    assert response.status_code == 201, response.text


def test_bulk_import_is_draft_only(client, examiner, seed_roles):
    _, section_id = _exam_with_questions(client, examiner, count=1, publish=True)
    response = client.post(f"/api/v1/exams/sections/{section_id}/questions/bulk", json={
        "questions": [{"text": "Late", "marks": 1,
                       "options": [{"text": "a", "is_correct": True}, {"text": "b", "is_correct": False}]}],
    }, headers=examiner)
    assert response.status_code == 400


# --- live monitoring ----------------------------------------------------------

def test_the_active_endpoint_returns_only_live_attempts(client, examiner, seed_roles):
    """Live monitoring downloaded EVERY attempt every ten seconds and filtered in
    the browser, so watching one candidate cost the whole sitting history."""
    exam_id, _ = _exam_with_questions(client, examiner)
    writing = auth_headers(_register_student_and_login(client, email="live1@example.com"))
    finished = auth_headers(_register_student_and_login(client, email="live2@example.com"))
    client.post(f"/api/v1/attempts/start/{exam_id}", headers=writing)
    client.post(f"/api/v1/attempts/start/{exam_id}", headers=finished)
    done_id = client.get("/api/v1/attempts/my", headers=finished).json()[0]["attempt_id"]
    client.post(f"/api/v1/attempts/{done_id}/submit", headers=finished)

    active = client.get(f"/api/v1/attempts/exam/{exam_id}/active", headers=examiner)
    assert active.status_code == 200, active.text
    assert len(active.json()) == 1
    row = active.json()[0]
    assert row["student_name"]
    assert row["remaining_seconds"] > 0
    assert "violation_count" in row


def test_another_examiners_exam_is_not_visible(client, examiner, seed_roles, admin_token):
    exam_id, _ = _exam_with_questions(client, examiner)
    other = auth_headers(_create_examiner_and_login(client, admin_token, email="nosy@example.com"))
    assert client.get(f"/api/v1/attempts/exam/{exam_id}/active", headers=other).status_code == 404
    assert client.get(f"/api/v1/attempts/exam/{exam_id}/archived", headers=other).status_code == 404


# --- the 0% pass mark ---------------------------------------------------------

def test_a_zero_percent_pass_mark_is_kept(client, examiner, seed_roles):
    """`parseInt(value, 10) || 40` in the exam form turned a valid 0 into 40,
    because 0 is falsy. The server has always accepted 0; the form silently
    changed the examiner's answer on the way."""
    exam = client.post("/api/v1/exams", json={
        "title": "Everyone passes", "duration_minutes": 30,
        "proctoring_enabled": False, "pass_percentage": 0,
    }, headers=examiner)
    assert exam.status_code == 201, exam.text
    assert exam.json()["pass_percentage"] == 0


# --- analytics buckets --------------------------------------------------------

@pytest.mark.parametrize("percentage", [0, 20.5, 40.5, 60.5, 80.5, 99.99, 100])
def test_every_percentage_lands_in_exactly_one_band(percentage):
    """The bands were (0,20), (21,40), (41,60), (61,80), (81,100) matched with `lo <= pct <=
    hi`, so every value in the gaps matched nothing and vanished from the chart.
    """
    from app.services.analytics_service import _bucket_scores

    buckets = _bucket_scores([percentage])
    assert sum(b["count"] for b in buckets) == 1, f"{percentage}% fell into no band"


def test_the_bands_account_for_every_candidate():
    from app.services.analytics_service import _bucket_scores

    scores = [0, 5.5, 20, 20.5, 33.33, 40, 40.5, 55, 60, 60.5, 79.99, 80, 80.5, 95, 100]
    buckets = _bucket_scores(scores)
    assert sum(b["count"] for b in buckets) == len(scores)


# --- pagination ---------------------------------------------------------------

def test_attempts_come_back_one_page_at_a_time(client, examiner, seed_roles):
    """The whole list used to be returned and sliced in the browser, so viewing
    25 rows cost the transfer and parse of every attempt the exam had ever had --
    repeatedly, for every examiner watching a live sitting."""
    exam_id, _ = _exam_with_questions(client, examiner)
    for index in range(7):
        student = auth_headers(_register_student_and_login(client, email=f"p{index}@example.com"))
        client.post(f"/api/v1/attempts/start/{exam_id}", headers=student)

    first = client.get(f"/api/v1/attempts/exam/{exam_id}?page=1&page_size=3", headers=examiner)
    assert first.status_code == 200, first.text
    body = first.json()
    assert len(body["items"]) == 3
    assert body["total"] == 7
    assert body["total_pages"] == 3
    assert body["page"] == 1

    last = client.get(f"/api/v1/attempts/exam/{exam_id}?page=3&page_size=3", headers=examiner).json()
    assert len(last["items"]) == 1

    # Every candidate appears exactly once across the pages -- no row is dropped
    # at a boundary or repeated.
    seen = []
    for page in (1, 2, 3):
        seen += [row["attempt_id"] for row in
                 client.get(f"/api/v1/attempts/exam/{exam_id}?page={page}&page_size=3",
                            headers=examiner).json()["items"]]
    assert len(seen) == len(set(seen)) == 7


def test_the_client_cannot_ask_for_an_unbounded_page(client, examiner, seed_roles):
    """Without a ceiling, `?page_size=100000` reproduces the exact unbounded
    response the pagination exists to prevent, on request."""
    exam_id, _ = _exam_with_questions(client, examiner)
    assert client.get(f"/api/v1/attempts/exam/{exam_id}?page_size=100000",
                      headers=examiner).status_code == 422
    assert client.get(f"/api/v1/attempts/exam/{exam_id}?page=0",
                      headers=examiner).status_code == 422


def test_attempts_can_be_searched_by_candidate(client, examiner, seed_roles):
    exam_id, _ = _exam_with_questions(client, examiner)
    for email in ("ayesha@example.com", "brendan@example.com"):
        student = auth_headers(_register_student_and_login(client, email=email))
        client.post(f"/api/v1/attempts/start/{exam_id}", headers=student)

    found = client.get(f"/api/v1/attempts/exam/{exam_id}?search=ayesha", headers=examiner).json()
    assert found["total"] == 1


def test_a_search_wildcard_is_escaped_not_interpreted(client, examiner, seed_roles):
    """`%` and `_` are LIKE wildcards, so an unescaped search box lets any input
    become a pattern -- "%" would match every candidate, and a wildcard-heavy
    string is a cheap way to make the database work hard."""
    exam_id, _ = _exam_with_questions(client, examiner)
    student = auth_headers(_register_student_and_login(client, email="literal@example.com"))
    client.post(f"/api/v1/attempts/start/{exam_id}", headers=student)

    wildcard = client.get(f"/api/v1/attempts/exam/{exam_id}?search=%25", headers=examiner).json()
    assert wildcard["total"] == 0, "a bare % matched every candidate"


def test_violations_are_paginated_and_carry_review_context(client, examiner, seed_roles):
    """A hall of 500 candidates producing a dozen events each is 6,000 rows sent
    to render 25 -- and the rows carried no candidate name, description or
    evidence flag, so reviewing one meant leaving the page."""
    exam_id, _ = _exam_with_questions(client, examiner)
    student = auth_headers(_register_student_and_login(client, email="flagged@example.com"))
    attempt_id = client.post(f"/api/v1/attempts/start/{exam_id}", headers=student).json()["attempt_id"]
    for index in range(4):
        client.post("/api/v1/proctoring/events", json={
            "attempt_id": attempt_id, "event_type": "tab_switch",
            "description": f"Switched away ({index})",
        }, headers=student)

    page = client.get(f"/api/v1/proctoring/events/exam/{exam_id}?page=1&page_size=2",
                      headers=examiner)
    assert page.status_code == 200, page.text
    body = page.json()
    assert len(body["items"]) == 2
    assert body["total"] == 4
    row = body["items"][0]
    assert row["student_name"], "the reviewer cannot tell whose violation this is"
    assert row["description"]
    assert "has_screenshot" in row


# --- adjudicated risk ---------------------------------------------------------

class _FakeViolation:
    def __init__(self, severity, decision=None):
        self.severity = severity
        self.admin_decision = decision


def test_a_dismissed_violation_stops_counting_toward_risk():
    """Dismissing changed admin_decision and nothing else."""
    from app.models.enums import Severity
    from app.services.admin_service import adjudicated_risk

    violations = [_FakeViolation(Severity.HIGH, "dismissed"),
                  _FakeViolation(Severity.HIGH, "false_positive"),
                  _FakeViolation(Severity.HIGH, "dismissed")]

    risk = adjudicated_risk(violations)
    assert risk["automated_score"] == 15
    assert risk["automated_tier"] == "high"
    assert risk["adjudicated_score"] == 0
    assert risk["adjudicated_tier"] == "low"
    assert risk["dismissed_count"] == 3


def test_the_automated_score_stays_visible():
    """Replacing one number with the other would hide the reviewer's work or the
    original signal. "Automated 15, adjudicated 5" is the honest summary."""
    from app.models.enums import Severity
    from app.services.admin_service import adjudicated_risk

    risk = adjudicated_risk([_FakeViolation(Severity.HIGH, "dismissed"),
                             _FakeViolation(Severity.HIGH, "confirmed")])
    assert risk["automated_score"] == 10
    assert risk["adjudicated_score"] == 5
    assert risk["confirmed_count"] == 1


def test_unreviewed_violations_still_count(): 
    """A flag nobody has looked at is not a cleared flag."""
    from app.models.enums import Severity
    from app.services.admin_service import adjudicated_risk

    risk = adjudicated_risk([_FakeViolation(Severity.HIGH), _FakeViolation(Severity.MEDIUM)])
    assert risk["adjudicated_score"] == 8
    assert risk["pending_review_count"] == 2


def test_a_terminated_attempt_stays_high_however_the_flags_were_judged():
    """The termination is its own signal, not the sum of the events."""
    from app.models.enums import AttemptStatus, Severity
    from app.services.admin_service import adjudicated_risk

    risk = adjudicated_risk([_FakeViolation(Severity.LOW, "dismissed")],
                            AttemptStatus.TERMINATED.value)
    assert risk["adjudicated_tier"] == "high"


def test_moving_an_examiner_moves_their_tenancy(client, seed_roles, admin_token, db_session):
    """update_examiner wrote organization_name -- a display string -- while organization_id
    decides which students they see and which roster they draw from.
    """
    from app.models.examiner import Examiner

    examiner_token = _create_examiner_and_login(client, admin_token)
    examiners = client.get("/api/v1/admin/examiners", headers=auth_headers(admin_token)).json()["items"]
    target = examiners[0]

    response = client.patch(f"/api/v1/admin/examiners/{target['id']}",
                            json={"organization_name": "Riverside Polytechnic"},
                            headers=auth_headers(admin_token))
    assert response.status_code == 200, response.text

    row = db_session.query(Examiner).filter(Examiner.id == target["id"]).first()
    db_session.refresh(row)
    assert row.organization_name == "Riverside Polytechnic"
    assert row.organization_id is not None, "the display name moved but the tenancy did not"

    from app.models.organization import Organization
    organization = db_session.query(Organization).filter(
        Organization.id == row.organization_id).first()
    assert organization.name == "Riverside Polytechnic", "the two disagree"


# --- per-exam proctoring policy -----------------------------------------------

def test_an_exam_can_switch_off_camera_and_screen_share(client, examiner, seed_roles):
    """proctoring_enabled gated the AI signals, but the exam page demanded
    camera, microphone, screen sharing AND fullscreen from every candidate
    regardless -- then told them the exam was not proctored."""
    exam = client.post("/api/v1/exams", json={
        "title": "Open book", "duration_minutes": 30, "proctoring_enabled": False,
        "require_camera": False, "require_microphone": False,
        "require_screen_share": False, "require_fullscreen": True,
    }, headers=examiner)
    assert exam.status_code == 201, exam.text

    section = client.post(f"/api/v1/exams/{exam.json()['id']}/sections",
                          json={"title": "S"}, headers=examiner).json()
    client.post(f"/api/v1/exams/sections/{section['id']}/questions", json={
        "text": "Q", "marks": 1, "question_type": "mcq",
        "options": [{"text": "a", "is_correct": True}, {"text": "b", "is_correct": False}],
    }, headers=examiner)
    client.post(f"/api/v1/exams/{exam.json()['id']}/publish", headers=examiner)

    student = auth_headers(_register_student_and_login(client, email="openbook@example.com"))
    available = client.get("/api/v1/exams/available", headers=student).json()
    entry = next(e for e in available if e["id"] == exam.json()["id"])
    assert entry["requires"] == {
        "camera": False, "microphone": False, "screen_share": False, "fullscreen": True,
    }


def test_requirements_default_to_the_old_behaviour(client, examiner, seed_roles):
    """Every exam already created has these NULL, and must keep behaving exactly
    as it did -- following proctoring_enabled."""
    exam_id, _ = _exam_with_questions(client, examiner, title="Legacy")
    student = auth_headers(_register_student_and_login(client, email="legacy@example.com"))
    entry = next(e for e in client.get("/api/v1/exams/available", headers=student).json()
                 if e["id"] == exam_id)
    # proctoring_enabled=False in the fixture, so nothing is demanded.
    assert entry["requires"]["camera"] is False

    proctored = client.post("/api/v1/exams", json={
        "title": "Proctored", "duration_minutes": 30, "proctoring_enabled": True,
    }, headers=examiner).json()
    from app.models.exam import Exam
    assert Exam(proctoring_enabled=True).requires("camera") is True


# --- results release ----------------------------------------------------------

def test_a_candidate_does_not_get_the_answer_key_before_release(client, examiner, seed_roles,
                                                                db_session):
    """The report returned correct options, correct-answer text and explanations
    the instant an attempt was submitted -- so the first candidate to finish held
    the complete key while everyone else was still writing."""
    from datetime import datetime, timedelta, timezone
    from app.models.exam import Exam

    exam_id, _ = _exam_with_questions(client, examiner, count=1)
    exam = db_session.query(Exam).filter(Exam.id == exam_id).first()
    exam.release_results_at = datetime.now(timezone.utc) + timedelta(hours=2)
    db_session.commit()

    student = auth_headers(_register_student_and_login(client, email="early@example.com"))
    attempt_id = client.post(f"/api/v1/attempts/start/{exam_id}", headers=student).json()["attempt_id"]
    client.post(f"/api/v1/attempts/{attempt_id}/submit", headers=student)

    report = client.get(f"/api/v1/attempts/{attempt_id}/report", headers=student)
    assert report.status_code == 200, report.text
    body = report.json()
    assert body["answers_released"] is False
    for question in body["questions"]:
        assert question["correct_answer"] is None, "the answer key leaked before release"
        assert question["correct_option_ids"] is None
        assert question["explanation"] is None
    # Their own mark is not withheld -- only WHICH answer was right.
    assert "scored_marks" in body
    assert body["questions"][0]["outcome"] in ("correct", "incorrect", "unattempted")


def test_staff_always_see_the_answer_key(client, examiner, seed_roles, db_session):
    """An examiner reviewing a paper needs it, and withholding it from them
    would make the release setting a marking obstacle rather than an anti-leak
    measure."""
    from datetime import datetime, timedelta, timezone
    from app.models.exam import Exam

    exam_id, _ = _exam_with_questions(client, examiner, count=1)
    exam = db_session.query(Exam).filter(Exam.id == exam_id).first()
    exam.release_results_at = datetime.now(timezone.utc) + timedelta(hours=2)
    db_session.commit()

    student = auth_headers(_register_student_and_login(client, email="marked@example.com"))
    attempt_id = client.post(f"/api/v1/attempts/start/{exam_id}", headers=student).json()["attempt_id"]
    client.post(f"/api/v1/attempts/{attempt_id}/submit", headers=student)

    staff = client.get(f"/api/v1/attempts/{attempt_id}/report", headers=examiner).json()
    assert staff["answers_released"] is True
    assert staff["questions"][0]["correct_answer"] is not None


def test_results_are_released_once_the_time_passes(client, examiner, seed_roles, db_session):
    from datetime import datetime, timedelta, timezone
    from app.models.exam import Exam

    exam_id, _ = _exam_with_questions(client, examiner, count=1)
    student = auth_headers(_register_student_and_login(client, email="later@example.com"))
    attempt_id = client.post(f"/api/v1/attempts/start/{exam_id}", headers=student).json()["attempt_id"]
    client.post(f"/api/v1/attempts/{attempt_id}/submit", headers=student)

    exam = db_session.query(Exam).filter(Exam.id == exam_id).first()
    exam.release_results_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    db_session.commit()

    body = client.get(f"/api/v1/attempts/{attempt_id}/report", headers=student).json()
    assert body["answers_released"] is True
    assert body["questions"][0]["correct_answer"] is not None


def test_no_release_time_means_immediately(client, examiner, seed_roles):
    """The existing behaviour for every exam already created -- switching this on
    retroactively would surprise candidates who have already seen theirs."""
    exam_id, _ = _exam_with_questions(client, examiner, count=1)
    student = auth_headers(_register_student_and_login(client, email="instant@example.com"))
    attempt_id = client.post(f"/api/v1/attempts/start/{exam_id}", headers=student).json()["attempt_id"]
    client.post(f"/api/v1/attempts/{attempt_id}/submit", headers=student)

    body = client.get(f"/api/v1/attempts/{attempt_id}/report", headers=student).json()
    assert body["answers_released"] is True


# --- review queue, organizations, exports -------------------------------------

def _flag(client, student_headers, attempt_id, event_type="tab_switch", description="x"):
    return client.post("/api/v1/proctoring/events", json={
        "attempt_id": attempt_id, "event_type": event_type, "description": description,
    }, headers=student_headers)


def test_the_review_queue_puts_the_worst_and_oldest_first(client, examiner, seed_roles, admin_token,
                                                          db_session):
    """The violations table is newest-first, which buries an unreviewed
    high-severity flag under a week of routine tab switches -- and an undecided
    flag counts against the candidate until someone clears it."""
    from app.models.proctor_event import ProctorEvent
    from app.models.enums import Severity

    exam_id, _ = _exam_with_questions(client, examiner)
    student = auth_headers(_register_student_and_login(client, email="queued@example.com"))
    attempt_id = client.post(f"/api/v1/attempts/start/{exam_id}", headers=student).json()["attempt_id"]
    for _ in range(3):
        _flag(client, student, attempt_id)

    # Make one of them high severity, and older than the rest.
    events = db_session.query(ProctorEvent).order_by(ProctorEvent.id).all()
    events[2].severity = Severity.HIGH
    events[2].created_at = datetime.now(timezone.utc) - timedelta(days=3)
    db_session.commit()

    queue = client.get("/api/v1/admin/review-queue", headers=auth_headers(admin_token))
    assert queue.status_code == 200, queue.text
    items = queue.json()["items"]
    assert items[0]["id"] == events[2].id, "the high-severity, oldest flag was not first"
    assert items[0]["waiting_hours"] > 60
    assert items[0]["student_name"]


def test_a_decided_violation_leaves_the_queue(client, examiner, seed_roles, admin_token):
    exam_id, _ = _exam_with_questions(client, examiner)
    student = auth_headers(_register_student_and_login(client, email="decided@example.com"))
    attempt_id = client.post(f"/api/v1/attempts/start/{exam_id}", headers=student).json()["attempt_id"]
    event_id = _flag(client, student, attempt_id).json()["id"]

    assert client.get("/api/v1/admin/review-queue",
                      headers=auth_headers(admin_token)).json()["total"] == 1

    client.patch(f"/api/v1/proctoring/events/{event_id}/decision",
                 json={"decision": "dismissed"}, headers=auth_headers(admin_token))

    assert client.get("/api/v1/admin/review-queue",
                      headers=auth_headers(admin_token)).json()["total"] == 0


def test_the_organizations_overview_counts_what_is_in_each(client, examiner, seed_roles, admin_token):
    """Organizations decide which candidates an examiner sees and which exams a
    student may sit, and there was no page showing which existed."""
    _exam_with_questions(client, examiner)
    rows = client.get("/api/v1/admin/organizations/overview", headers=auth_headers(admin_token))
    assert rows.status_code == 200, rows.text
    assert rows.json(), "no organizations reported despite an examiner owning one"
    org = rows.json()[0]
    assert org["examiner_count"] >= 1
    assert org["exam_count"] >= 1
    assert "pending_invites" in org


def test_an_export_returns_csv_and_is_recorded(client, seed_roles, admin_token):
    """An export is the moment data leaves the platform. Without a record,
    "who took a copy of the candidate list?" has no answer."""
    _register_student_and_login(client, email="exported@example.com")

    response = client.get("/api/v1/admin/export/candidates", headers=auth_headers(admin_token))
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/csv")
    assert "attachment" in response.headers["content-disposition"]

    body = response.text
    assert body.splitlines()[0].startswith("id,full_name,email")
    assert "exported@example.com" in body

    feed = client.get("/api/v1/admin/activity", headers=auth_headers(admin_token)).json()
    assert any(entry["type"] == "data_exported" for entry in feed), "the export left no record"


def test_an_export_never_contains_biometric_material(client, examiner, seed_roles, admin_token):
    """A CSV is exactly the artefact that escapes the individually-authorised
    endpoints those images are served through."""
    exam_id, _ = _exam_with_questions(client, examiner)
    student = auth_headers(_register_student_and_login(client, email="biometric@example.com"))
    attempt_id = client.post(f"/api/v1/attempts/start/{exam_id}", headers=student).json()["attempt_id"]
    _flag(client, student, attempt_id)

    for kind in ("candidates", "examiners", "violations"):
        body = client.get(f"/api/v1/admin/export/{kind}", headers=auth_headers(admin_token)).text.lower()
        for forbidden in ("image_path", "screenshot_path", "base64", "embedding", "face_"):
            assert forbidden not in body, f"{kind} export leaked {forbidden}"


def test_an_export_respects_the_current_filter(client, seed_roles, admin_token):
    _register_student_and_login(client, email="keep@example.com")
    _register_student_and_login(client, email="drop@example.com")

    body = client.get("/api/v1/admin/export/candidates?search=keep",
                      headers=auth_headers(admin_token)).text
    assert "keep@example.com" in body
    assert "drop@example.com" not in body, "the export ignored the filter on screen"


def test_only_an_admin_can_export(client, examiner, seed_roles):
    assert client.get("/api/v1/admin/export/candidates", headers=examiner).status_code == 403


def test_an_unknown_export_is_refused(client, seed_roles, admin_token):
    assert client.get("/api/v1/admin/export/passwords",
                      headers=auth_headers(admin_token)).status_code == 400


def test_the_settings_endpoint_never_returns_a_secret(client, seed_roles, admin_token):
    """A settings page is exactly where a signing key or an SMTP password would
    end up travelling through an API response into a browser, and from there
    into a screenshot."""
    response = client.get("/api/v1/admin/settings", headers=auth_headers(admin_token))
    assert response.status_code == 200, response.text

    from app.core.config import settings as app_settings
    blob = response.text.lower()

    # Secret VALUES, not any key whose name happens to contain "password" --
    # `password_min_length` is a policy number and belongs here.
    for value in (app_settings.SECRET_KEY, app_settings.DATABASE_URL, app_settings.SMTP_PASSWORD):
        if value:
            assert value.lower() not in blob, "the settings endpoint leaked a secret value"

    # And the field names that would only ever carry one.
    for name in ("secret_key", "smtp_password", "database_url", "redis_url", "smtp_user"):
        assert name not in blob, f"the settings endpoint exposed {name!r}"
    # But it does answer the question an administrator actually has.
    assert "configured" in response.json()["email"]
    assert response.json()["proctoring"]["strike_limit"] == app_settings.LOCKDOWN_STRIKE_LIMIT


def test_only_an_admin_can_read_the_settings(client, examiner, seed_roles):
    assert client.get("/api/v1/admin/settings", headers=examiner).status_code == 403
