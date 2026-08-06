"""drop job_outbox and rate_limit_counters (superseded by Redis)

Revision ID: 0030
Revises: 0029
Create Date: 2026-08-06

Redis is back in this stack (see app/core/redis_client.py), scoped to the
things that are genuinely ephemeral, shared, cross-worker state: rate
limiting, one-time-passcode state, distributed locks, and the background job
queue. Two of the three Postgres tables 0029 introduced as Redis substitutes
are now themselves superseded:

  job_outbox           The job queue is RQ over Redis now (app/core/queues.py,
                       app/worker/). A job's own durable outcome, where one is
                       required (email delivery), is recorded in
                       `email_outbox` -- there was never a requirement that
                       PDF-report or bulk-email jobs have a permanent Postgres
                       record, only that permanent data never live ONLY in
                       Redis, and neither of those produces data that isn't
                       already durable elsewhere (a generated report file; an
                       email whose own outcome is tracked in email_outbox).

  rate_limit_counters  Rate limiting is Redis-backed now (app/core/rate_limit.py),
                       using Redis's own key TTLs for expiry instead of a
                       sweep over a Postgres table.

`email_outbox` is untouched: it remains the permanent delivery record
(status, attempts, error, sent_at) this rewrite explicitly requires
PostgreSQL to keep, independent of whatever queues or retries the delivery
mechanism uses underneath it.
"""
import sqlalchemy as sa
from alembic import op

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_index("ix_rate_limit_counters_expires_at", table_name="rate_limit_counters")
    op.drop_table("rate_limit_counters")

    op.drop_index("ix_job_outbox_run_at", table_name="job_outbox")
    op.drop_index("ix_job_outbox_status", table_name="job_outbox")
    op.drop_index("ix_job_outbox_job_type", table_name="job_outbox")
    op.drop_table("job_outbox")


def downgrade() -> None:
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
