"""add advanced proctoring event types (object detection, pose, gaze, monitor)

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-01
"""
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

NEW_VALUES = [
    "phone_detected",
    "book_detected",
    "multiple_persons_detected",
    "looking_away",
    "gaze_deviation",
    "external_monitor_detected",
]


def upgrade() -> None:
    for value in NEW_VALUES:
        # Each ADD VALUE must run outside the (implicit) enclosing transaction in
        # older Postgres; Alembic's autocommit block handles that for us here.
        op.execute(f"ALTER TYPE eventtype ADD VALUE IF NOT EXISTS '{value}'")


def downgrade() -> None:
    # No-op: Postgres does not support removing a single enum value without
    # recreating the type (and rewriting every row that references it).
    pass
