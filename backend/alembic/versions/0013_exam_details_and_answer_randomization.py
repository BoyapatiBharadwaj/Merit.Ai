"""exam instructions, randomize_options, and per-attempt option order

Revision ID: 0013
Revises: 0012
Create Date: 2026-08-03
"""
import sqlalchemy as sa
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("exams", sa.Column("instructions", sa.Text(), nullable=True))
    op.add_column("exams", sa.Column(
        "randomize_options", sa.Boolean(), nullable=False, server_default=sa.true(),
    ))
    op.add_column("student_exam_attempts", sa.Column("option_order_json", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("student_exam_attempts", "option_order_json")
    op.drop_column("exams", "randomize_options")
    op.drop_column("exams", "instructions")
