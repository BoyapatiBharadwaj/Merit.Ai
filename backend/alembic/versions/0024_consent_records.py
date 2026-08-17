"""record what a candidate consented to, and when

Revision ID: 0024
Revises: 0023
Create Date: 2026-08-05
"""
import sqlalchemy as sa
from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("terms_accepted_at", sa.DateTime(timezone=True), nullable=True))
    # The version string, not just a boolean: terms change, and
    # "they agreed" without "to what" cannot answer a dispute.
    op.add_column("users", sa.Column("terms_version", sa.String(length=20), nullable=True))
    op.add_column("users", sa.Column("proctoring_consent_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "proctoring_consent_at")
    op.drop_column("users", "terms_version")
    op.drop_column("users", "terms_accepted_at")
