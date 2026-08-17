"""add noise_detected_loud event type

Revision ID: 0007
Revises: 0006
Create Date: 2026-08-02
"""
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

NEW_VALUES = ["noise_detected_loud"]


def upgrade() -> None:
    for value in NEW_VALUES:
        # Each ADD VALUE must run outside the (implicit) enclosing transaction in
        # older Postgres; Alembic's autocommit block handles that for us here.
        op.execute(f"ALTER TYPE eventtype ADD VALUE IF NOT EXISTS '{value}'")


def downgrade() -> None:
    # No-op: Postgres does not support removing a single enum value without
    # recreating the type (and rewriting every row that references it).
    pass
