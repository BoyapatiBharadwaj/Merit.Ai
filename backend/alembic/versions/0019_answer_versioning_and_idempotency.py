"""optimistic concurrency for autosaved answers

Revision ID: 0019
Revises: 0018
Create Date: 2026-08-04
"""
import sqlalchemy as sa
from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # server_default rather than a plain default: existing rows need a value at
    # the moment the column is added, and NOT NULL without one would fail on any
    # table that already has answers in it -- i.e. every real deployment.
    op.add_column(
        "student_answers",
        sa.Column("answer_version", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("student_answers", sa.Column("idempotency_key", sa.String(length=64), nullable=True))
    op.add_column("student_answers", sa.Column("saved_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("student_answers", "saved_at")
    op.drop_column("student_answers", "idempotency_key")
    op.drop_column("student_answers", "answer_version")
