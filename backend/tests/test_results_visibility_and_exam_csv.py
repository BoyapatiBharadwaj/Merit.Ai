"""Two new per-exam settings an examiner can choose when
creating (or, while still a draft, editing) an exam:
"""
from datetime import datetime, timedelta, timezone

from app.models.exam import Exam
from tests.conftest import auth_headers
from tests.test_exam_workflow import _create_examiner_and_login, _register_student_and_login


def _exam_with_one_question(client, examiner_headers, **exam_fields):
    """Returns (exam_id, question_id, correct_option_id) for a single
    5-mark MCQ exam, published and ready to sit."""
    payload = {
        "title": "Results Visibility Quiz", "duration_minutes": 30,
        "randomize_questions": False, "proctoring_enabled": False,
        **exam_fields,
    }
    exam = client.post("/api/v1/exams", json=payload, headers=examiner_headers).json()
    section = client.post(f"/api/v1/exams/{exam['id']}/sections", json={
        "title": "Section 1", "order_index": 0,
    }, headers=examiner_headers).json()
    question = client.post(f"/api/v1/exams/sections/{section['id']}/questions", json={
        "text": "2 + 2 = ?", "marks": 5, "order_index": 0,
        "options": [{"text": "3", "is_correct": False}, {"text": "4", "is_correct": True}],
    }, headers=examiner_headers).json()
    publish = client.post(f"/api/v1/exams/{exam['id']}/publish", headers=examiner_headers)
    assert publish.status_code == 200, publish.text
    correct_option_id = next(o["id"] for o in question["options"] if o["is_correct"])
    return exam["id"], question["id"], correct_option_id


def _sit_answer_and_submit(client, student_headers, exam_id, question_id, correct_option_id):
    """Starts an attempt, answers the one question CORRECTLY, and submits --
    so scored_marks/percentage are meaningfully non-zero wherever they are
    visible at all."""
    start = client.post(f"/api/v1/attempts/start/{exam_id}", headers=student_headers)
    assert start.status_code == 200, start.text
    attempt_id = start.json()["attempt_id"]
    save = client.put(f"/api/v1/attempts/{attempt_id}/answer", json={
        "question_id": question_id, "selected_option_id": correct_option_id,
    }, headers=student_headers)
    assert save.status_code == 200, save.text
    submit = client.post(f"/api/v1/attempts/{attempt_id}/submit", headers=student_headers)
    assert submit.status_code == 200, submit.text
    return attempt_id, submit.json()


# --- - ---
# show_results -------------------------------------------------------------------------

def test_defaults_preserve_the_original_always_shown_behaviour(client, seed_roles, admin_token):
    """Every exam created before this feature existed must keep behaving
    exactly as it always did: a candidate sees their score the moment they
    submit."""
    examiner_headers = auth_headers(_create_examiner_and_login(client, admin_token, email="rv-default@example.com"))
    exam_id, question_id, correct_option_id = _exam_with_one_question(client, examiner_headers)
    student_token = _register_student_and_login(client, email="rv-default-student@example.com")
    student_headers = auth_headers(student_token)

    attempt_id, submit_body = _sit_answer_and_submit(client, student_headers, exam_id, question_id, correct_option_id)
    assert submit_body["results_released"] is True
    assert submit_body["scored_marks"] == 5

    result = client.get(f"/api/v1/attempts/{attempt_id}/result", headers=student_headers)
    assert result.status_code == 200
    assert result.json()["results_released"] is True
    assert result.json()["percentage"] == 100.0


def test_show_results_false_withholds_the_score_from_the_candidate_forever(client, seed_roles, admin_token):
    examiner_headers = auth_headers(_create_examiner_and_login(client, admin_token, email="rv-hidden@example.com"))
    exam_id, question_id, correct_option_id = _exam_with_one_question(client, examiner_headers, show_results=False)
    student_token = _register_student_and_login(client, email="rv-hidden-student@example.com")
    student_headers = auth_headers(student_token)

    attempt_id, submit_body = _sit_answer_and_submit(client, student_headers, exam_id, question_id, correct_option_id)
    assert submit_body["results_released"] is False
    assert submit_body["scored_marks"] is None
    assert submit_body["percentage"] is None

    result = client.get(f"/api/v1/attempts/{attempt_id}/result", headers=student_headers)
    assert result.json()["results_released"] is False
    assert result.json()["scored_marks"] is None

    report = client.get(f"/api/v1/attempts/{attempt_id}/report", headers=student_headers)
    assert report.status_code == 200, report.text
    body = report.json()
    assert body["results_released"] is False
    assert body["scored_marks"] is None
    assert body["percentage"] is None
    assert body["passed"] is None
    # No per-question breakdown leaked either -- outcome/marks_awarded ARE
    # "the results" this setting exists to hide.
    assert body["questions"] == []

    # A student cannot even pull the PDF while results are withheld.
    pdf = client.get(f"/api/v1/attempts/{attempt_id}/result/pdf", headers=student_headers)
    assert pdf.status_code == 403

    # Staff are never affected: the exam's owning examiner sees everything.
    staff_result = client.get(f"/api/v1/attempts/{attempt_id}/result", headers=examiner_headers)
    assert staff_result.json()["results_released"] is True
    assert staff_result.json()["scored_marks"] == 5
    staff_report = client.get(f"/api/v1/attempts/{attempt_id}/report", headers=examiner_headers)
    assert staff_report.json()["scored_marks"] == 5
    assert len(staff_report.json()["questions"]) == 1


def test_after_end_time_mode_withholds_until_the_exams_end_time_passes(client, seed_roles, admin_token, db_session):
    examiner_headers = auth_headers(_create_examiner_and_login(client, admin_token, email="rv-endtime@example.com"))
    future_end = datetime.now(timezone.utc) + timedelta(hours=2)
    exam_id, question_id, correct_option_id = _exam_with_one_question(
        client, examiner_headers,
        results_release_mode="after_end_time",
        end_time=future_end.isoformat(),
    )
    student_token = _register_student_and_login(client, email="rv-endtime-student@example.com")
    student_headers = auth_headers(student_token)

    attempt_id, submit_body = _sit_answer_and_submit(client, student_headers, exam_id, question_id, correct_option_id)
    # The candidate submitted well before the exam's own end_time, so their
    # result stays hidden even though THEY are finished.
    assert submit_body["results_released"] is False
    assert submit_body["scored_marks"] is None

    # Move the exam's end_time into the past (simulating time passing) and
    # confirm the same attempt's result is now visible.
    exam = db_session.query(Exam).filter(Exam.id == exam_id).first()
    exam.end_time = datetime.now(timezone.utc) - timedelta(minutes=1)
    db_session.commit()

    result = client.get(f"/api/v1/attempts/{attempt_id}/result", headers=student_headers)
    assert result.json()["results_released"] is True
    assert result.json()["scored_marks"] == 5


def test_after_end_time_mode_with_no_end_time_set_stays_withheld(client, seed_roles, admin_token):
    """An examiner who picks 'after end time' but never actually sets one
    gets the safe default (withheld), not an exception or a silent
    'immediate' fallback."""
    examiner_headers = auth_headers(_create_examiner_and_login(client, admin_token, email="rv-noend@example.com"))
    exam_id, question_id, correct_option_id = _exam_with_one_question(
        client, examiner_headers, results_release_mode="after_end_time")
    student_token = _register_student_and_login(client, email="rv-noend-student@example.com")

    _, submit_body = _sit_answer_and_submit(client, auth_headers(student_token), exam_id, question_id, correct_option_id)
    assert submit_body["results_released"] is False


def test_release_results_at_still_only_gates_the_answer_key_not_the_score(client, seed_roles, admin_token, db_session):
    """The original release_results_at/show_answers_on_release pair is
    unaffected by this feature: it is still answer-key-only, independent of
    whether the score itself (results_released) is shown."""
    examiner_headers = auth_headers(_create_examiner_and_login(client, admin_token, email="rv-key@example.com"))
    exam_id, question_id, correct_option_id = _exam_with_one_question(client, examiner_headers)
    exam = db_session.query(Exam).filter(Exam.id == exam_id).first()
    exam.release_results_at = datetime.now(timezone.utc) + timedelta(hours=2)
    db_session.commit()

    student_token = _register_student_and_login(client, email="rv-key-student@example.com")
    student_headers = auth_headers(student_token)
    attempt_id, _ = _sit_answer_and_submit(client, student_headers, exam_id, question_id, correct_option_id)

    report = client.get(f"/api/v1/attempts/{attempt_id}/report", headers=student_headers).json()
    # Score/outcome visible immediately...
    assert report["results_released"] is True
    assert report["scored_marks"] == 5
    assert report["questions"][0]["outcome"] == "correct"
    # ...but the answer key is still withheld until release_results_at.
    assert report["answers_released"] is False
    assert report["questions"][0]["correct_answer"] is None


# --- - ---
# Exam CSV export -------------------------------------------------------------------------

def test_exam_csv_export_reflects_every_attempt_so_far(client, seed_roles, admin_token):
    examiner_headers = auth_headers(_create_examiner_and_login(client, admin_token, email="csv-examiner@example.com"))
    exam_id, question_id, correct_option_id = _exam_with_one_question(client, examiner_headers)

    alice_token = _register_student_and_login(client, email="csv-alice@example.com")
    _sit_answer_and_submit(client, auth_headers(alice_token), exam_id, question_id, correct_option_id)

    export1 = client.get(f"/api/v1/attempts/exam/{exam_id}/export", headers=examiner_headers)
    assert export1.status_code == 200, export1.text
    assert export1.headers["content-type"].startswith("text/csv")
    lines1 = export1.text.strip().splitlines()
    assert len(lines1) == 2  # header + Alice

    # A second candidate submits -- the export must include them too, on the
    # very next request, with no separate "publish"/"add a row" step.
    bob_token = _register_student_and_login(client, email="csv-bob@example.com")
    _sit_answer_and_submit(client, auth_headers(bob_token), exam_id, question_id, correct_option_id)

    export2 = client.get(f"/api/v1/attempts/exam/{exam_id}/export", headers=examiner_headers)
    lines2 = export2.text.strip().splitlines()
    assert len(lines2) == 3  # header + two candidates

    header = lines2[0].split(",")
    assert "Violations" in header
    assert "Start Time" in header
    assert "Result" in header
    assert all("Passed" in line for line in lines2[1:])


def test_exam_csv_export_is_scoped_to_the_owning_examiner_or_admin(client, seed_roles, admin_token):
    owner_token = _create_examiner_and_login(client, admin_token, email="csv-owner@example.com")
    other_token = _create_examiner_and_login(client, admin_token, email="csv-other@example.com")
    exam_id, question_id, correct_option_id = _exam_with_one_question(client, auth_headers(owner_token))
    student_token = _register_student_and_login(client, email="csv-scoped-student@example.com")
    _sit_answer_and_submit(client, auth_headers(student_token), exam_id, question_id, correct_option_id)

    forbidden = client.get(f"/api/v1/attempts/exam/{exam_id}/export", headers=auth_headers(other_token))
    assert forbidden.status_code == 404

    forbidden_student = client.get(f"/api/v1/attempts/exam/{exam_id}/export", headers=auth_headers(student_token))
    assert forbidden_student.status_code == 404

    ok_owner = client.get(f"/api/v1/attempts/exam/{exam_id}/export", headers=auth_headers(owner_token))
    assert ok_owner.status_code == 200

    ok_admin = client.get(f"/api/v1/attempts/exam/{exam_id}/export", headers=auth_headers(admin_token))
    assert ok_admin.status_code == 200
