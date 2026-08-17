"""activation links instead of mailed passwords

Revision ID: 0026
Revises: 0025
Create Date: 2026-08-05
"""
import sqlalchemy as sa
from alembic import op

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()

    op.add_column(
        "users",
        sa.Column("must_change_password", sa.Boolean(), nullable=False,
                  server_default=sa.false()),
    )

    if bind.dialect.name == "postgresql":
        # SQLite has no native enum -- the column is a VARCHAR with a CHECK that SQLAlchemy
        # renders from the Python enum, so nothing to alter there.
        op.execute("COMMIT")
        op.execute("ALTER TYPE otppurpose ADD VALUE IF NOT EXISTS 'activation'")


def downgrade() -> None:
    op.drop_column("users", "must_change_password")
    # The enum value stays. Postgres cannot drop one without recreating the type
    # and rewriting every row that references it, and an unused value is inert.
