"""record when admins were last notified about an access request

Revision ID: 0027
Revises: 0026
Create Date: 2026-08-05
"""
import sqlalchemy as sa
from alembic import op

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "access_requests",
        sa.Column("last_notified_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("access_requests", "last_notified_at")
