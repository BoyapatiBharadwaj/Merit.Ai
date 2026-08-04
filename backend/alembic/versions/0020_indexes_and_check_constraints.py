"""composite indexes for the dashboard queries, and value constraints

Revision ID: 0020
Revises: 0019
Create Date: 2026-08-04

Two unrelated-but-both-overdue database improvements.

--- Composite indexes ---

The schema has plenty of single-column indexes, but PostgreSQL can only use the
LEADING columns of an index, so `index=True` on `exam_id` does nothing for a
query that filters on exam_id AND status AND orders by started_at. Every index
below matches a query this application actually issues; none is speculative.

  proctor_events(attempt_id, created_at)
      The candidate proctoring timeline, and now
      proctor_repository.events_for_attempts. proctor_events is the
      fastest-growing table in the schema, so this is the highest-value entry.

  proctor_events(severity, created_at)
      The admin violations queue, which filters by severity and shows newest
      first.

  student_exam_attempts(exam_id, status)
      "who is currently in progress on this exam" -- the live-sessions view and
      every completed/in-progress count.

  exams(examiner_id, status) / exams(organization_id, status)
      The examiner's own exam list, and the student-facing available-exams
      query, which is organization-scoped by can_student_access_exam.

  access_requests(status, created_at)
      The admin's pending-requests queue.

--- CHECK constraints ---

The API validates all of these at the schema layer, which is the right place for
a good error message -- but the database currently accepts anything. Validation
in one layer only holds as long as every writer goes through that layer, and a
migration, a management script or a future endpoint will eventually not. These
are the invariants where a violation would silently corrupt reporting rather
than fail loudly: a negative duration, a pass mark above 100, a percentage
outside 0-100.

Deliberately NOT added: `end_time > start_time`. Both columns are nullable and
the existing rules around open-ended windows are richer than a CHECK can
express (see schemas/exam.py::_check_schedule_window), so a constraint here
would reject rows the application considers valid.
"""
import sqlalchemy as sa
from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None

INDEXES = [
    ("ix_proctor_events_attempt_created", "proctor_events", ["attempt_id", "created_at"]),
    ("ix_proctor_events_severity_created", "proctor_events", ["severity", "created_at"]),
    ("ix_attempts_exam_status", "student_exam_attempts", ["exam_id", "status"]),
    ("ix_exams_examiner_status", "exams", ["examiner_id", "status"]),
    ("ix_exams_organization_status", "exams", ["organization_id", "status"]),
    # No index on exam_results(attempt_id): the column is already
    # `unique=True, index=True` on the model, so the unique constraint's own
    # index already serves results_for_attempts' IN () lookup. Adding another
    # would cost write throughput and disk for nothing.
    ("ix_access_requests_status_created", "access_requests", ["status", "created_at"]),
]

CHECKS = [
    ("ck_exams_duration_positive", "exams", "duration_minutes > 0"),
    ("ck_exams_pass_percentage_range", "exams", "pass_percentage BETWEEN 0 AND 100"),
    ("ck_questions_marks_positive", "questions", "marks > 0"),
    ("ck_exam_results_percentage_range", "exam_results", "percentage BETWEEN 0 AND 100"),
]


def upgrade() -> None:
    for name, table, columns in INDEXES:
        op.create_index(name, table, columns)

    bind = op.get_bind()
    # SQLite cannot ALTER TABLE ADD CONSTRAINT -- it needs a full table rebuild,
    # which alembic's batch mode can do but which is a lot of risk for a
    # dialect this application never runs in production. The tests build their
    # schema from the models rather than by running migrations anyway, so
    # skipping here costs nothing.
    if bind.dialect.name != "postgresql":
        return

    # NOT VALID, deliberately.
    #
    # A plain ADD CONSTRAINT CHECK scans every existing row and fails the whole
    # migration if even one violates it. On an empty database that is fine; on a
    # database with real history it means a single old exam with a bad value
    # aborts the migration -- and because entrypoint.sh runs with `set -e`, that
    # takes the entire container down at boot with a connection-refused for
    # every request. A schema improvement should never be able to do that.
    #
    # NOT VALID enforces the constraint on every INSERT and UPDATE from now on
    # (which is the actual goal -- stopping new bad data) while leaving existing
    # rows unexamined. Once you have confirmed the historical data is clean:
    #
    #   ALTER TABLE exams VALIDATE CONSTRAINT ck_exams_duration_positive;
    #
    # ...which takes only a SHARE UPDATE EXCLUSIVE lock, so it does not block
    # reads or writes the way the naive form would have.
    #
    # Find any offending rows first with, e.g.:
    #   SELECT id, duration_minutes FROM exams WHERE NOT (duration_minutes > 0);
    for name, table, condition in CHECKS:
        op.execute(f'ALTER TABLE {table} ADD CONSTRAINT {name} CHECK ({condition}) NOT VALID')


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        for name, table, _ in reversed(CHECKS):
            op.drop_constraint(name, table, type_="check")

    for name, table, _ in reversed(INDEXES):
        op.drop_index(name, table_name=table)
