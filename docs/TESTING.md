# Testing

## Backend

```bash
cd backend
pip install -r requirements-dev.txt
pytest
```

642 tests across 41 modules. The default run needs no Postgres, no Redis, no GPU,
no network, and no model downloads — it uses in-memory SQLite and `fakeredis`,
and every AI call is mocked.

```bash
pytest -q -m "not postgres"        # what CI runs on every push
pytest -m postgres                 # migration and row-lock tests
pytest tests/test_exam_workflow.py -v
pytest -k "autosave or recovery"
```

### What is covered

| Area | Modules |
|---|---|
| Auth and hardening | `test_auth`, `test_auth_hardening`, `test_rate_limit`, `test_production_gates`, `test_production_hardening` |
| Exam lifecycle | `test_exam_workflow`, `test_exam_access_control`, `test_exam_schedule_and_reset`, `test_exam_status_categorization`, `test_exam_recovery` |
| Questions | `test_coding_questions`, `test_multi_select_questions`, `test_question_edit_delete`, `test_section_delete_and_password_policy` |
| Attempts | `test_attempt_finalize`, `test_autosave_ordering`, `test_autosave_concurrency_postgres` |
| Proctoring | `test_proctor_events_batch`, `test_proctoring_kill_switches`, `test_anti_spoof`, `test_object_detection`, `test_ai_helpers` |
| Identity | `test_biometric_lifecycle`, `test_identity_and_lockdown`, `test_face_photo_access`, `test_id_card_photo_access` |
| Admin and examiner | `test_admin_dashboard`, `test_candidate_admin`, `test_examiner_module`, `test_examiner_password_and_exam_delete`, `test_access_requests`, `test_activity_and_account_admin` |
| Email and jobs | `test_email_and_otp`, `test_email_outbox_and_provisioning`, `test_shared_state_and_workers` |
| Data and reporting | `test_db_transactions`, `test_migrations_postgres`, `test_analytics`, `test_pdf_reports`, `test_results_visibility_and_exam_csv` |
| Sandbox | `test_code_runner_hardening` |

The AI tests assert the property that matters: the AI-worker path and the
in-process path produce the *same* embedding format, so a profile registered
against one verifies against the other.

### Fixtures

`tests/conftest.py` sets `DATABASE_URL`, `SECRET_KEY`, `CORS_ORIGINS`, and
`UPLOAD_DIR` before any `app.*` module is imported, because `Settings` is
resolved and cached at first import. It also:

- drops bcrypt's work factor to the minimum, since the production cost is
  deliberately punishing and would dominate the run
- disables `REQUIRE_EMAIL_VERIFICATION` and `REQUIRE_CONSENT_ON_SIGNUP`, so the
  suite is the "cannot send email" deployment those settings exist for, and the
  ~30 setup registrations stay about what each test is actually testing
- swaps the Redis client for `fakeredis` and resets rate-limit counters between
  tests

### The `postgres` marker

Some behaviour cannot be tested on SQLite: `SELECT ... FOR UPDATE` row locks are
silently dropped by SQLAlchemy's SQLite compiler, and enum handling differs. Those
tests carry `@pytest.mark.postgres` and skip automatically unless
`TEST_POSTGRES_URL` is set or the optional `pgserver` package is installed — so a
plain `pytest` still passes on a machine with no database.

```bash
TEST_POSTGRES_URL=postgresql://postgres:postgres@localhost:5432/postgres pytest -m postgres
```

## Frontend

```bash
cd frontend
npm run lint         # ESLint over src/
npm test             # node --test over src/**/*.test.mjs
npm run check        # import-resolution check
npm run build        # production build; fails on a broken import
```

## Continuous integration

`.github/workflows/backend-tests.yml` runs on every push to `main` and on every
pull request:

| Job | What it does |
|---|---|
| **Test suite (SQLite)** | Python 3.11, `pip install -r requirements-dev.txt`, `pytest -q -m "not postgres"` |
| **Alembic chain (PostgreSQL)** | Same install against a `postgres:16` service container, then `pytest -q -m postgres` |

Postgres is pinned to the major version the application is developed against,
since enum behaviour differs across majors.

## Writing a test

Use the existing fixtures rather than constructing clients by hand — they handle
schema setup, teardown, and rate-limit state between tests.

```python
def test_candidate_cannot_start_another_organizations_exam(client, examiner_token, student_token):
    exam_id = create_published_exam(client, examiner_token)
    response = client.post(
        f"/api/v1/attempts/start/{exam_id}",
        headers={"Authorization": f"Bearer {student_token}"},
    )
    assert response.status_code == 403
```

Assert on behaviour visible through the API rather than on internal call
sequences, so a refactor that preserves behaviour does not break the suite.
