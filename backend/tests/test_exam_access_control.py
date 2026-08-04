"""
Organization scoping of exams -- the tenancy boundary.

The bug: every published exam was listed to every student, and `start_attempt`
checked only status and timing, so any student could sit any exam in the system
by naming its id. Filtering the list alone would have been cosmetic.

These tests are therefore written in pairs wherever it matters: one asserting
the exam is hidden from the list, and one asserting it *also* cannot be started
by id. The second of each pair is the one that would have caught the real bug.
"""
from tests.conftest import auth_headers
from tests.test_exam_workflow import (
    _build_published_exam, _create_examiner_and_login, _register_student_and_login, enrol_email,
)


def _org_id_for(examiner_email: str) -> int:
    from app.database.session import SessionLocal
    from app.models.examiner import Examiner
    from app.models.user import User

    session = SessionLocal()
    try:
        examiner = (session.query(Examiner).join(User, Examiner.user_id == User.id)
                    .filter(User.email == examiner_email).first())
        return examiner.organization_id
    finally:
        session.close()


def _student_id_for(email: str) -> int:
    from app.database.session import SessionLocal
    from app.models.student import Student
    from app.models.user import User

    session = SessionLocal()
    try:
        return (session.query(Student).join(User, Student.user_id == User.id)
                .filter(User.email == email).first().id)
    finally:
        session.close()


def _two_organizations(client, admin_token):
    """Acme and Globex, each with an examiner and one published exam."""
    acme = auth_headers(_create_examiner_and_login(
        client, admin_token, email="acme-examiner@example.com"))
    globex_token = client.post("/api/v1/auth/examiners", json={
        "first_name": "Glo", "last_name": "Bex", "email": "globex-examiner@example.com",
        "organization_name": "Globex Corp", "password": "Sup3rSecret!",
    }, headers=auth_headers(admin_token))
    assert globex_token.status_code in (200, 201), globex_token.text
    login = client.post("/api/v1/auth/login", json={
        "email": "globex-examiner@example.com", "password": "Sup3rSecret!"})
    globex = auth_headers(login.json()["access_token"])

    return {
        "acme_headers": acme,
        "globex_headers": globex,
        "acme_exam": _build_published_exam(client, acme),
        "globex_exam": _build_published_exam(client, globex),
        "acme_org": _org_id_for("acme-examiner@example.com"),
        "globex_org": _org_id_for("globex-examiner@example.com"),
    }


# ---------------------------------------------------------------------------
# The core boundary
# ---------------------------------------------------------------------------

def test_organizations_are_actually_distinct(client, seed_roles, admin_token):
    """Guards the premise of every other test here."""
    world = _two_organizations(client, admin_token)
    assert world["acme_org"] is not None
    assert world["acme_org"] != world["globex_org"]


def test_student_does_not_see_another_organizations_exam_in_the_list(client, seed_roles, admin_token):
    world = _two_organizations(client, admin_token)
    student = auth_headers(_register_student_and_login(
        client, email="acme-student@example.com", organization_id=world["acme_org"]))

    listed = client.get("/api/v1/exams/available", headers=student).json()

    assert [e["id"] for e in listed] == [world["acme_exam"]]


def test_student_cannot_start_another_organizations_exam_by_id(client, seed_roles, admin_token):
    """THE test. Hiding an exam from the list means nothing if its id still
    works -- this is the hole that actually existed."""
    world = _two_organizations(client, admin_token)
    student = auth_headers(_register_student_and_login(
        client, email="acme-student@example.com", organization_id=world["acme_org"]))

    forbidden = client.post(f"/api/v1/attempts/start/{world['globex_exam']}", headers=student)

    assert forbidden.status_code == 404
    # Same wording as a genuinely missing exam, so ids cannot be enumerated.
    assert forbidden.json()["detail"] == "Exam not available."

    allowed = client.post(f"/api/v1/attempts/start/{world['acme_exam']}", headers=student)
    assert allowed.status_code == 200, allowed.text


def test_a_missing_exam_and_a_forbidden_one_are_indistinguishable(client, seed_roles, admin_token):
    """Otherwise an outsider could enumerate ids and map out which
    organizations are running exams and when."""
    world = _two_organizations(client, admin_token)
    student = auth_headers(_register_student_and_login(
        client, email="acme-student@example.com", organization_id=world["acme_org"]))

    forbidden = client.post(f"/api/v1/attempts/start/{world['globex_exam']}", headers=student)
    missing = client.post("/api/v1/attempts/start/99999", headers=student)

    assert forbidden.status_code == missing.status_code == 404
    assert forbidden.json()["detail"] == missing.json()["detail"]


# ---------------------------------------------------------------------------
# Unenrolled students
# ---------------------------------------------------------------------------

def test_a_student_nobody_enrolled_sees_and_can_start_nothing(client, seed_roles, admin_token):
    """A self-registered stranger. Fail-closed: no organization means access
    to nothing, not access to everything."""
    world = _two_organizations(client, admin_token)
    stranger = auth_headers(_register_student_and_login(
        client, email="stranger@example.com", enrol=False))

    assert client.get("/api/v1/exams/available", headers=stranger).json() == []
    assert client.post(f"/api/v1/attempts/start/{world['acme_exam']}",
                       headers=stranger).status_code == 404


def test_enrolling_an_already_registered_student_grants_access(client, seed_roles, admin_token):
    """Order must not matter: a student who signed up before being enrolled
    should not have to re-register."""
    world = _two_organizations(client, admin_token)
    student = auth_headers(_register_student_and_login(
        client, email="late@example.com", enrol=False))
    assert client.get("/api/v1/exams/available", headers=student).json() == []

    added = client.post("/api/v1/organizations/me/roster",
                        json={"emails": ["late@example.com"]}, headers=world["acme_headers"])
    assert added.status_code == 201, added.text
    assert added.json()["linked"] == ["late@example.com"]

    listed = client.get("/api/v1/exams/available", headers=student).json()
    assert [e["id"] for e in listed] == [world["acme_exam"]]


def test_removing_a_student_from_the_roster_revokes_access(client, seed_roles, admin_token):
    world = _two_organizations(client, admin_token)
    student = auth_headers(_register_student_and_login(
        client, email="leaver@example.com", organization_id=world["acme_org"]))
    assert client.get("/api/v1/exams/available", headers=student).json() != []

    roster = client.get("/api/v1/organizations/me/roster", headers=world["acme_headers"]).json()
    entry = next(m for m in roster if m["email"] == "leaver@example.com")
    removed = client.delete(f"/api/v1/organizations/me/roster/{entry['id']}",
                            headers=world["acme_headers"])
    assert removed.status_code == 204

    assert client.get("/api/v1/exams/available", headers=student).json() == []
    assert client.post(f"/api/v1/attempts/start/{world['acme_exam']}",
                       headers=student).status_code == 404


def test_removing_a_student_keeps_the_exam_they_already_sat(client, seed_roles, admin_token):
    """Revoking future access must not destroy assessment records."""
    world = _two_organizations(client, admin_token)
    student = auth_headers(_register_student_and_login(
        client, email="grad@example.com", organization_id=world["acme_org"]))
    start = client.post(f"/api/v1/attempts/start/{world['acme_exam']}", headers=student)
    attempt_id = start.json()["attempt_id"]
    client.post(f"/api/v1/attempts/{attempt_id}/submit", headers=student)

    roster = client.get("/api/v1/organizations/me/roster", headers=world["acme_headers"]).json()
    entry = next(m for m in roster if m["email"] == "grad@example.com")
    client.delete(f"/api/v1/organizations/me/roster/{entry['id']}", headers=world["acme_headers"])

    analytics = client.get(f"/api/v1/analytics/exams/{world['acme_exam']}",
                           headers=world["acme_headers"]).json()
    assert analytics["total_attempts"] == 1


# ---------------------------------------------------------------------------
# Revocation must reach *inside* a live attempt, not just the front door
# ---------------------------------------------------------------------------

def test_revoked_access_stops_an_already_started_attempt(client, seed_roles, admin_token):
    """Gating only start_attempt left ~12 endpoints open.

    A student removed mid-exam could no longer start anything, but the attempt
    they already held kept serving question text and accepting answers and
    submissions. Owning an attempt is not the same as being entitled to it.
    """
    world = _two_organizations(client, admin_token)
    student = auth_headers(_register_student_and_login(
        client, email="midexam@example.com", organization_id=world["acme_org"]))
    start = client.post(f"/api/v1/attempts/start/{world['acme_exam']}", headers=student)
    attempt_id = start.json()["attempt_id"]
    question_id = start.json()["question_ids_in_order"][0]
    # Baseline: everything works while enrolled.
    assert client.get(f"/api/v1/attempts/{attempt_id}/question/{question_id}",
                      headers=student).status_code == 200

    roster = client.get("/api/v1/organizations/me/roster", headers=world["acme_headers"]).json()
    entry = next(m for m in roster if m["email"] == "midexam@example.com")
    client.delete(f"/api/v1/organizations/me/roster/{entry['id']}", headers=world["acme_headers"])

    for path, method in [
        (f"/api/v1/attempts/{attempt_id}/question/{question_id}", "get"),
        (f"/api/v1/attempts/{attempt_id}/answers", "get"),
    ]:
        response = getattr(client, method)(path, headers=student)
        assert response.status_code == 404, f"{path} still reachable after revocation"

    assert client.put(f"/api/v1/attempts/{attempt_id}/answer",
                      json={"question_id": question_id, "selected_option_id": None},
                      headers=student).status_code == 404
    assert client.post(f"/api/v1/attempts/{attempt_id}/submit",
                       headers=student).status_code == 404


def test_being_excluded_from_a_restricted_exam_stops_a_live_attempt(client, seed_roles, admin_token):
    world = _two_organizations(client, admin_token)
    excluded = auth_headers(_register_student_and_login(
        client, email="excluded@example.com", organization_id=world["acme_org"]))
    _register_student_and_login(client, email="invited@example.com",
                                organization_id=world["acme_org"])
    start = client.post(f"/api/v1/attempts/start/{world['acme_exam']}", headers=excluded)
    attempt_id = start.json()["attempt_id"]

    client.post(f"/api/v1/organizations/exams/{world['acme_exam']}/participants",
               json={"emails": ["invited@example.com"]},
               headers=world["acme_headers"])

    assert client.get(f"/api/v1/attempts/{attempt_id}/answers",
                      headers=excluded).status_code == 404


def test_starting_an_exam_never_reveals_whether_it_exists(client, seed_roles, admin_token):
    """The identity gate used to run before the access check, so an unverified
    student got 403 "verify your ID" for a real proctoring-enabled exam and
    404 for a fake id -- an oracle for enumerating exam ids across tenants."""
    examiner = auth_headers(_create_examiner_and_login(client, admin_token))
    real_exam = _build_published_exam(client, examiner, proctoring_enabled=True)
    unverified = auth_headers(_register_student_and_login(
        client, email="unverified@example.com", enrol=False))

    real = client.post(f"/api/v1/attempts/start/{real_exam}", headers=unverified)
    fake = client.post("/api/v1/attempts/start/99999", headers=unverified)

    assert real.status_code == fake.status_code == 404
    assert real.json()["detail"] == fake.json()["detail"]


# ---------------------------------------------------------------------------
# Other tenant-scoped surfaces
# ---------------------------------------------------------------------------

def test_examiners_only_see_their_own_organizations_students_in_the_directory(client, seed_roles, admin_token):
    """/users/students was an unscoped SELECT *, handing any examiner every
    other institution's names, emails and roll numbers."""
    world = _two_organizations(client, admin_token)
    _register_student_and_login(client, email="acme-student@example.com",
                                organization_id=world["acme_org"])
    _register_student_and_login(client, email="globex-student@example.com",
                                organization_id=world["globex_org"])

    acme = client.get("/api/v1/users/students", headers=world["acme_headers"]).json()
    globex = client.get("/api/v1/users/students", headers=world["globex_headers"]).json()
    everyone = client.get("/api/v1/users/students", headers=auth_headers(admin_token)).json()

    assert [s["email"] for s in acme] == ["acme-student@example.com"]
    assert [s["email"] for s in globex] == ["globex-student@example.com"]
    assert len(everyone) == 2, "admins still see the whole platform"


def test_an_examiner_cannot_fetch_another_organizations_face_photo(client, seed_roles, admin_token):
    """Biometrics are the most sensitive data here; every examiner counting as
    'staff' for every student meant any of them could walk student ids."""
    world = _two_organizations(client, admin_token)
    _register_student_and_login(client, email="acme-student@example.com",
                                organization_id=world["acme_org"])
    student_id = _student_id_for("acme-student@example.com")

    outsider = client.get(f"/api/v1/proctoring/face/photo/{student_id}",
                          headers=world["globex_headers"])
    owner = client.get(f"/api/v1/proctoring/face/photo/{student_id}",
                       headers=world["acme_headers"])

    assert outsider.status_code == 403
    # The owning org gets past authorization; 404 only because no photo was
    # ever registered in this test.
    assert owner.status_code == 404


# ---------------------------------------------------------------------------
# Roster mechanics
# ---------------------------------------------------------------------------

def test_organization_names_are_matched_literally_not_as_wildcards(client, seed_roles, admin_token):
    """get_or_create used ilike(), so % and _ in the caller's string were
    wildcards -- and the string arrives from the *unauthenticated* access
    request form. Submitting "%" matched the first existing organization and
    bonded the new examiner into someone else's tenant."""
    _create_examiner_and_login(client, admin_token, email="acme@example.com")

    for probe in ("%", "Acme_Institute", "Acme%"):
        client.post("/api/v1/auth/examiners", json={
            "first_name": "Probe", "last_name": "User",
            "email": f"probe-{abs(hash(probe)) % 10000}@example.com",
            "organization_name": probe, "password": "Sup3rSecret!",
        }, headers=auth_headers(admin_token))

    names = {o["name"] for o in client.get("/api/v1/organizations",
                                           headers=auth_headers(admin_token)).json()}

    # Each probe must have created its own distinct tenant, never joined Acme.
    assert {"Acme Institute", "%", "Acme_Institute", "Acme%"} <= names


def test_enrolling_someone_who_belongs_to_another_organization_is_reported(client, seed_roles, admin_token):
    """The roster row is created but grants nothing, because link_student
    keeps the earliest membership. Reporting it as "linked" told the examiner
    the opposite of the truth."""
    world = _two_organizations(client, admin_token)
    _register_student_and_login(client, email="taken@example.com",
                                organization_id=world["acme_org"])

    result = client.post("/api/v1/organizations/me/roster",
                         json={"emails": ["taken@example.com"]},
                         headers=world["globex_headers"]).json()

    assert result["linked"] == []
    assert result["in_another_organization"] == ["taken@example.com"]

def test_roster_enrolment_is_idempotent_and_case_insensitive(client, seed_roles, admin_token):
    """The realistic input is last term's spreadsheet column, pasted again."""
    world = _two_organizations(client, admin_token)

    first = client.post("/api/v1/organizations/me/roster",
                        json={"emails": ["Cohort@Example.com"]},
                        headers=world["acme_headers"]).json()
    assert first["added"] == ["cohort@example.com"]

    again = client.post("/api/v1/organizations/me/roster",
                        json={"emails": ["cohort@example.com", "COHORT@EXAMPLE.COM"]},
                        headers=world["acme_headers"]).json()
    assert again["added"] == []
    assert again["already_present"] == ["cohort@example.com", "cohort@example.com"]


def test_malformed_roster_entries_are_reported_not_silently_dropped(client, seed_roles, admin_token):
    """A pasted spreadsheet column often carries a header row or a stray name.

    Skipping those quietly is how a typo'd address becomes a student who never
    gets access and an examiner with no idea why -- discovered on exam day.
    """
    world = _two_organizations(client, admin_token)

    result = client.post("/api/v1/organizations/me/roster",
                         json={"emails": "Email Address\nreal@x.com\nnot-an-email\nfoo@bar"},
                         headers=world["acme_headers"]).json()

    assert result["added"] == ["real@x.com"]
    # "Email" and "Address" split on whitespace; neither is an address, and
    # neither is the domain-less "foo@bar".
    assert set(result["invalid"]) == {"Email", "Address", "not-an-email", "foo@bar"}


def test_roster_accepts_a_pasted_block_of_addresses(client, seed_roles, admin_token):
    world = _two_organizations(client, admin_token)

    result = client.post("/api/v1/organizations/me/roster",
                         json={"emails": "a@x.com, b@x.com;c@x.com\nd@x.com"},
                         headers=world["acme_headers"]).json()

    assert result["added"] == ["a@x.com", "b@x.com", "c@x.com", "d@x.com"]


def test_an_examiner_only_ever_sees_their_own_roster(client, seed_roles, admin_token):
    world = _two_organizations(client, admin_token)
    client.post("/api/v1/organizations/me/roster", json={"emails": ["acme-only@example.com"]},
                headers=world["acme_headers"])

    globex_roster = client.get("/api/v1/organizations/me/roster",
                               headers=world["globex_headers"]).json()

    assert [m["email"] for m in globex_roster] == []


def test_students_only_appear_in_their_own_organizations_student_list(client, seed_roles, admin_token):
    world = _two_organizations(client, admin_token)
    _register_student_and_login(client, email="acme-student@example.com",
                                organization_id=world["acme_org"])

    acme_students = client.get("/api/v1/organizations/me/students",
                               headers=world["acme_headers"]).json()
    globex_students = client.get("/api/v1/organizations/me/students",
                                 headers=world["globex_headers"]).json()

    assert [s["email"] for s in acme_students] == ["acme-student@example.com"]
    assert globex_students == []


# ---------------------------------------------------------------------------
# Per-exam restriction
# ---------------------------------------------------------------------------

def test_an_exam_with_no_participant_list_is_open_to_the_whole_organization(client, seed_roles, admin_token):
    world = _two_organizations(client, admin_token)
    student = auth_headers(_register_student_and_login(
        client, email="anyone@example.com", organization_id=world["acme_org"]))

    access = client.get(f"/api/v1/organizations/exams/{world['acme_exam']}/access",
                        headers=world["acme_headers"]).json()

    assert access["restricted"] is False
    assert access["participants"] == []
    assert [e["id"] for e in client.get("/api/v1/exams/available", headers=student).json()] \
        == [world["acme_exam"]]


def test_restricting_an_exam_hides_it_from_everyone_else_in_the_organization(client, seed_roles, admin_token):
    world = _two_organizations(client, admin_token)
    invited = auth_headers(_register_student_and_login(
        client, email="invited@example.com", organization_id=world["acme_org"]))
    excluded = auth_headers(_register_student_and_login(
        client, email="excluded@example.com", organization_id=world["acme_org"]))

    updated = client.post(f"/api/v1/organizations/exams/{world['acme_exam']}/participants",
                          json={"emails": ["invited@example.com"]},
                          headers=world["acme_headers"])
    assert updated.status_code == 201, updated.text
    assert updated.status_code == 201, updated.text

    assert [e["id"] for e in client.get("/api/v1/exams/available", headers=invited).json()] \
        == [world["acme_exam"]]
    assert client.get("/api/v1/exams/available", headers=excluded).json() == []


def test_an_excluded_student_cannot_start_a_restricted_exam_by_id(client, seed_roles, admin_token):
    """Same pairing as the cross-organization case: the list is not the gate."""
    world = _two_organizations(client, admin_token)
    _register_student_and_login(client, email="invited@example.com",
                                organization_id=world["acme_org"])
    excluded = auth_headers(_register_student_and_login(
        client, email="excluded@example.com", organization_id=world["acme_org"]))
    client.post(f"/api/v1/organizations/exams/{world['acme_exam']}/participants",
               json={"emails": ["invited@example.com"]},
               headers=world["acme_headers"])

    blocked = client.post(f"/api/v1/attempts/start/{world['acme_exam']}", headers=excluded)

    assert blocked.status_code == 404


def test_an_exam_can_be_restricted_to_someone_who_has_not_registered_yet(client, seed_roles, admin_token):
    """The whole point of keying the allow-list on email.

    An examiner sets an exam up before the cohort signs up. With the old
    student_id-keyed list this was impossible -- you had to enrol everyone
    org-wide, wait for them all to register, and only then restrict.
    """
    world = _two_organizations(client, admin_token)

    added = client.post(f"/api/v1/organizations/exams/{world['acme_exam']}/participants",
                        json={"emails": ["future@example.com"]},
                        headers=world["acme_headers"])
    assert added.status_code == 201, added.text

    access = client.get(f"/api/v1/organizations/exams/{world['acme_exam']}/access",
                        headers=world["acme_headers"]).json()
    assert access["restricted"] is True
    assert access["participants"][0]["student_id"] is None, "pending invite, no account yet"

    # Now they sign up (and are enrolled in the org, as they must be).
    invitee = auth_headers(_register_student_and_login(
        client, email="future@example.com", organization_id=world["acme_org"]))

    # Access works immediately -- it matches on email, so it never depended on
    # a resolution step having run.
    assert [e["id"] for e in client.get("/api/v1/exams/available",
                                        headers=invitee).json()] == [world["acme_exam"]]
    assert client.post(f"/api/v1/attempts/start/{world['acme_exam']}",
                       headers=invitee).status_code == 200

    # ...and the examiner's list now shows them as registered.
    access = client.get(f"/api/v1/organizations/exams/{world['acme_exam']}/access",
                        headers=world["acme_headers"]).json()
    assert access["participants"][0]["student_id"] is not None


def test_adding_exam_participants_is_additive_and_idempotent(client, seed_roles, admin_token):
    world = _two_organizations(client, admin_token)

    client.post(f"/api/v1/organizations/exams/{world['acme_exam']}/participants",
                json={"emails": "a@x.com, b@x.com"}, headers=world["acme_headers"])
    second = client.post(f"/api/v1/organizations/exams/{world['acme_exam']}/participants",
                         json={"emails": "B@X.com\nc@x.com"},
                         headers=world["acme_headers"]).json()

    assert second["added"] == ["c@x.com"]
    assert second["already_present"] == ["b@x.com"]

    access = client.get(f"/api/v1/organizations/exams/{world['acme_exam']}/access",
                        headers=world["acme_headers"]).json()
    assert {p["email"] for p in access["participants"]} == {"a@x.com", "b@x.com", "c@x.com"}


def test_a_single_participant_can_be_removed_without_reopening_the_exam(client, seed_roles, admin_token):
    world = _two_organizations(client, admin_token)
    client.post(f"/api/v1/organizations/exams/{world['acme_exam']}/participants",
                json={"emails": "keep@x.com, drop@x.com"}, headers=world["acme_headers"])
    access = client.get(f"/api/v1/organizations/exams/{world['acme_exam']}/access",
                        headers=world["acme_headers"]).json()
    drop = next(p for p in access["participants"] if p["email"] == "drop@x.com")

    removed = client.delete(
        f"/api/v1/organizations/exams/{world['acme_exam']}/participants/{drop['id']}",
        headers=world["acme_headers"])

    assert removed.status_code == 204
    after = client.get(f"/api/v1/organizations/exams/{world['acme_exam']}/access",
                       headers=world["acme_headers"]).json()
    assert [p["email"] for p in after["participants"]] == ["keep@x.com"]
    assert after["restricted"] is True, "one removal must not reopen the exam to everyone"


def test_clearing_the_participant_list_reopens_the_exam(client, seed_roles, admin_token):
    world = _two_organizations(client, admin_token)
    excluded = auth_headers(_register_student_and_login(
        client, email="excluded@example.com", organization_id=world["acme_org"]))
    _register_student_and_login(client, email="invited@example.com",
                                organization_id=world["acme_org"])
    client.post(f"/api/v1/organizations/exams/{world['acme_exam']}/participants",
               json={"emails": ["invited@example.com"]},
               headers=world["acme_headers"])
    assert client.get("/api/v1/exams/available", headers=excluded).json() == []

    reopened = client.delete(f"/api/v1/organizations/exams/{world['acme_exam']}/participants",
                             headers=world["acme_headers"])

    assert reopened.status_code == 204
    assert client.get("/api/v1/exams/available", headers=excluded).json() != []


def test_adding_someone_outside_the_organization_is_flagged_but_still_grants_this_exam(client, seed_roles, admin_token):
    """A per-exam invite is deliberately NOT scoped to the invitee's own
    organization -- see can_student_access_exam's docstring. The address is
    accepted and reported back as `not_in_organization` (shown in the UI as a
    "Not enrolled" badge) purely as information for the examiner, not as a
    rejection: being on the list is enough to get into THIS exam. What it does
    not do is enrol them generally -- they gain nothing else."""
    world = _two_organizations(client, admin_token)
    outsider = auth_headers(_register_student_and_login(
        client, email="globex-student@example.com", organization_id=world["globex_org"]))

    result = client.post(f"/api/v1/organizations/exams/{world['acme_exam']}/participants",
                         json={"emails": ["globex-student@example.com"]},
                         headers=world["acme_headers"])

    assert result.status_code == 201
    assert result.json()["not_in_organization"] == ["globex-student@example.com"]

    # The invite alone is enough to start Acme's exam...
    assert client.post(f"/api/v1/attempts/start/{world['acme_exam']}",
                       headers=outsider).status_code == 200
    # ...and it shows up in their list alongside their own org's exam.
    ids = {e["id"] for e in client.get("/api/v1/exams/available", headers=outsider).json()}
    assert ids == {world["acme_exam"], world["globex_exam"]}

    # But the invite is not a roster enrolment: they still don't appear as an
    # Acme student, and were never added to Acme's general roster.
    acme_roster = client.get("/api/v1/organizations/me/roster", headers=world["acme_headers"]).json()
    assert "globex-student@example.com" not in {m["email"] for m in acme_roster}


def test_a_student_with_no_organization_can_still_take_an_exam_they_are_invited_to(client, seed_roles, admin_token):
    """The extreme version of the above: a student nobody has ever enrolled
    anywhere still gets into a specific exam once an examiner names their
    email on that exam's own list -- the whole point of a per-exam invite
    being independent of org membership rather than gated behind it."""
    world = _two_organizations(client, admin_token)
    stranger = auth_headers(_register_student_and_login(
        client, email="nowhere@example.com", enrol=False))
    assert client.get("/api/v1/exams/available", headers=stranger).json() == []

    client.post(f"/api/v1/organizations/exams/{world['acme_exam']}/participants",
               json={"emails": ["nowhere@example.com"]}, headers=world["acme_headers"])

    assert [e["id"] for e in client.get("/api/v1/exams/available", headers=stranger).json()] \
        == [world["acme_exam"]]
    assert client.post(f"/api/v1/attempts/start/{world['acme_exam']}",
                       headers=stranger).status_code == 200


def test_an_examiner_cannot_change_another_examiners_exam_access(client, seed_roles, admin_token):
    world = _two_organizations(client, admin_token)

    blocked = client.post(f"/api/v1/organizations/exams/{world['acme_exam']}/participants",
                          json={"emails": ["someone@example.com"]},
                          headers=world["globex_headers"])

    assert blocked.status_code == 404


# ---------------------------------------------------------------------------
# Exam creation
# ---------------------------------------------------------------------------

def test_exams_inherit_their_examiners_organization(client, seed_roles, admin_token):
    world = _two_organizations(client, admin_token)

    access = client.get(f"/api/v1/organizations/exams/{world['acme_exam']}/access",
                        headers=world["acme_headers"]).json()

    assert access["organization_id"] == world["acme_org"]
    assert access["organization_name"] == "Acme Institute"


def test_two_examiners_typing_the_same_organization_name_share_one_tenant(client, seed_roles, admin_token):
    """How a department with several examiners shares one student roster --
    and the reason organization names are matched case-insensitively."""
    first = auth_headers(_create_examiner_and_login(
        client, admin_token, email="first@example.com"))
    client.post("/api/v1/auth/examiners", json={
        "first_name": "Second", "last_name": "Examiner", "email": "second@example.com",
        "organization_name": "  acme institute ", "password": "Sup3rSecret!",
    }, headers=auth_headers(admin_token))
    login = client.post("/api/v1/auth/login", json={
        "email": "second@example.com", "password": "Sup3rSecret!"})
    second = auth_headers(login.json()["access_token"])

    exam_id = _build_published_exam(client, first)
    student = auth_headers(_register_student_and_login(client, email="shared@example.com"))

    # The second examiner sees the same roster...
    assert [m["email"] for m in client.get("/api/v1/organizations/me/roster",
                                           headers=second).json()] == ["shared@example.com"]
    # ...and the student sees the first examiner's exam.
    assert [e["id"] for e in client.get("/api/v1/exams/available",
                                        headers=student).json()] == [exam_id]


def test_the_api_will_not_create_an_examiner_without_an_organization(client, seed_roles, admin_token):
    """First line of defence: organization_name is required at the schema."""
    rejected = client.post("/api/v1/auth/examiners", json={
        "first_name": "No", "last_name": "Org", "email": "noorg@example.com",
        "organization_name": None, "password": "Sup3rSecret!",
    }, headers=auth_headers(admin_token))

    assert rejected.status_code == 422


def test_an_examiner_without_an_organization_cannot_create_an_invisible_exam(client, seed_roles, admin_token):
    """Second line of defence, for data the API cannot produce but a database
    can still hold -- a row predating migration 0008, or one an admin edited.

    A NULL organization_id makes an exam inaccessible to every student, and
    the examiner would get no hint as to why. Failing loudly at creation is
    the only outcome that is debuggable.
    """
    headers = auth_headers(_create_examiner_and_login(
        client, admin_token, email="orphan@example.com"))

    # Strip the organization behind the API's back.
    from app.database.session import SessionLocal
    from app.models.examiner import Examiner
    from app.models.user import User

    session = SessionLocal()
    try:
        examiner = (session.query(Examiner).join(User, Examiner.user_id == User.id)
                    .filter(User.email == "orphan@example.com").first())
        examiner.organization_id = None
        session.commit()
    finally:
        session.close()

    rejected = client.post("/api/v1/exams", json={
        "title": "Orphan Exam", "duration_minutes": 30,
    }, headers=headers)

    assert rejected.status_code == 400
    assert "organization" in rejected.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Role boundaries on the new endpoints
# ---------------------------------------------------------------------------

def test_students_cannot_read_or_edit_a_roster(client, seed_roles, admin_token):
    world = _two_organizations(client, admin_token)
    student = auth_headers(_register_student_and_login(
        client, email="nosy@example.com", organization_id=world["acme_org"]))

    assert client.get("/api/v1/organizations/me/roster", headers=student).status_code == 403
    assert client.post("/api/v1/organizations/me/roster", json={"emails": ["x@y.com"]},
                       headers=student).status_code == 403
    assert client.get("/api/v1/organizations/me/students", headers=student).status_code == 403


def test_only_admins_can_list_every_organization(client, seed_roles, admin_token):
    world = _two_organizations(client, admin_token)

    assert client.get("/api/v1/organizations", headers=world["acme_headers"]).status_code == 403
    listed = client.get("/api/v1/organizations", headers=auth_headers(admin_token))
    assert listed.status_code == 200
    assert {o["name"] for o in listed.json()} == {"Acme Institute", "Globex Corp"}
