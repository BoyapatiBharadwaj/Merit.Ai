"""add the two enum values that exist in Python but never reached PostgreSQL

Revision ID: 0017
Revises: 0016
Create Date: 2026-08-04
"""
from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None

# (enum type name, value to add)
MISSING_VALUES = [
    ("questiontype", "multi_select"),
    ("eventtype", "screen_share_stopped"),
]


def upgrade() -> None:
    bind = op.get_bind()
    # SQLite (and anything else non-Postgres) has no native enum types.
    if bind.dialect.name != "postgresql":
        return

    with op.get_context().autocommit_block():
        for type_name, value in MISSING_VALUES:
            op.execute(f"ALTER TYPE {type_name} ADD VALUE IF NOT EXISTS '{value}'")


def downgrade() -> None:
    # No-op, matching 0002/0003/0005/0007.
    pass
