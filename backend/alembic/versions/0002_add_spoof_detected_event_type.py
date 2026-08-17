"""add spoof_detected event type

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-01
"""
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Postgres enums can only gain values, not lose them, without a full type
    # rebuild -- ADD VALUE is safe and cheap for the append-only case here.
    op.execute("ALTER TYPE eventtype ADD VALUE IF NOT EXISTS 'spoof_detected'")


def downgrade() -> None:
    # No-op: Postgres does not support removing a single enum value without
    # recreating the type (and rewriting every row that references it).
    pass
