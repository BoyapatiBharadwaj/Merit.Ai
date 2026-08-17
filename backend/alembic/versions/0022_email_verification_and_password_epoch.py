"""email verification state and a password-change epoch

Revision ID: 0022
Revises: 0021
Create Date: 2026-08-05
"""
import sqlalchemy as sa
from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("password_changed_at", sa.DateTime(timezone=True), nullable=True))

    # See the module docstring: created_at, not now(), so deploying this does
    # not invalidate every token in flight.
    op.execute("UPDATE users SET password_changed_at = created_at WHERE password_changed_at IS NULL")

    # Partial-ish index for "show me the accounts that never verified", which is the query an
    # administrator actually runs against this column.
    op.create_index("ix_users_email_verified_at", "users", ["email_verified_at"])


def downgrade() -> None:
    op.drop_index("ix_users_email_verified_at", table_name="users")
    op.drop_column("users", "password_changed_at")
    op.drop_column("users", "email_verified_at")
