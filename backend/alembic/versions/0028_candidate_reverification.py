"""let an administrator require a candidate to verify their identity again

Revision ID: 0028
Revises: 0027
Create Date: 2026-08-05
"""
import sqlalchemy as sa
from alembic import op

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("students", sa.Column("reverification_required_at",
                                        sa.DateTime(timezone=True), nullable=True))
    op.add_column("students", sa.Column("reverification_reason", sa.String(length=500), nullable=True))
    op.add_column("students", sa.Column("reverification_requested_by_id", sa.Integer(), nullable=True))
    # ondelete=SET NULL: an administrator's account being deleted must not take
    # the outstanding request with it, nor block the deletion.
    op.create_foreign_key(
        "fk_students_reverification_requested_by", "students", "users",
        ["reverification_requested_by_id"], ["id"], ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_students_reverification_requested_by", "students", type_="foreignkey")
    op.drop_column("students", "reverification_requested_by_id")
    op.drop_column("students", "reverification_reason")
    op.drop_column("students", "reverification_required_at")
