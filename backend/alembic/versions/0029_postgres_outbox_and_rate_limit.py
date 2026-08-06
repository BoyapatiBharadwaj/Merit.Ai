"""email outbox, job outbox, and postgres-backed rate limit counters

Revision ID: 0029
Revises: 0028
Create Date: 2026-08-06

Three tables, one theme: replace the pieces of this application that used to
depend on Redis with the database every part of it already requires.

  email_outbox        Durable delivery tracking for transactional email. A
                       row is written before the first send attempt, so a
                       notification that fails (or a process that crashes
                       mid-send) leaves something retryable rather than
                       nothing at all. See app/models/email_outbox.py.

  job_outbox           Replaces the Redis/RQ background job queue. See
                       app/models/job_outbox.py.

  rate_limit_counters  Replaces the Redis-backed shared rate-limit counter.
                       See app/models/rate_limit_counter.py.

None of these back-fill anything -- they are all new, empty tables with no
existing data to migrate.
"""
import sqlalchemy as sa
from alembic import op

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "email_outbox",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("to_address", sa.String(length=255), nullable=False),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column("text_body", sa.Text(), nullable=False),
        sa.Column("html_body", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("last_error", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_email_outbox_to_address", "email_outbox", ["to_address"])
    op.create_index("ix_email_outbox_status", "email_outbox", ["status"])
    op.create_index("ix_email_outbox_next_retry_at", "email_outbox", ["next_retry_at"])

    op.create_table(
        "job_outbox",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("job_type", sa.String(length=100), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("description", sa.String(length=200), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("last_error", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("run_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_job_outbox_job_type", "job_outbox", ["job_type"])
    op.create_index("ix_job_outbox_status", "job_outbox", ["status"])
    op.create_index("ix_job_outbox_run_at", "job_outbox", ["run_at"])

    op.create_table(
        "rate_limit_counters",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("bucket", sa.String(length=64), nullable=False),
        sa.Column("identity", sa.String(length=128), nullable=False),
        sa.Column("window_start", sa.Integer(), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("bucket", "identity", "window_start", name="uq_rate_limit_counter"),
    )
    op.create_index("ix_rate_limit_counters_expires_at", "rate_limit_counters", ["expires_at"])


def downgrade() -> None:
    op.drop_table("rate_limit_counters")
    op.drop_table("job_outbox")
    op.drop_table("email_outbox")
