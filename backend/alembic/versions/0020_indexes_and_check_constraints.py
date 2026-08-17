"""composite indexes for the dashboard queries, and value constraints

Revision ID: 0020
Revises: 0019
Create Date: 2026-08-04
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
    # `unique=True, index=True` on the model, so the unique constraint's
    # own index already serves results_for_attempts' IN () lookup.
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
    # SQLite cannot ALTER TABLE ADD CONSTRAINT -- it needs a full table
    # rebuild, which alembic's batch mode can do but which is a lot of
    # risk for a dialect this application never runs in production.
    if bind.dialect.name != "postgresql":
        return

    # NOT VALID, deliberately. A plain ADD CONSTRAINT CHECK scans every existing row and fails
    # the whole migration if even one violates it.
    for name, table, condition in CHECKS:
        op.execute(f'ALTER TABLE {table} ADD CONSTRAINT {name} CHECK ({condition}) NOT VALID')


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        for name, table, _ in reversed(CHECKS):
            op.drop_constraint(name, table, type_="check")

    for name, table, _ in reversed(INDEXES):
        op.drop_index(name, table_name=table)
