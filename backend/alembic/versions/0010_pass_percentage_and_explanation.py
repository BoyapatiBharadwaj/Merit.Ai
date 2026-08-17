"""exam pass_percentage and question explanation

Revision ID: 0010
Revises: 0009
Create Date: 2026-08-03
"""
import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("exams", sa.Column("pass_percentage", sa.Integer(), nullable=False, server_default="40"))
    with op.batch_alter_table("exams") as batch:
        batch.alter_column("pass_percentage", server_default=None)

    op.add_column("questions", sa.Column("explanation", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("questions", "explanation")
    op.drop_column("exams", "pass_percentage")
