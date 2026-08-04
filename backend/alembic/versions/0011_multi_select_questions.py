"""multi-select question type

Revision ID: 0011
Revises: 0010
Create Date: 2026-08-03

Adds student_answers.selected_option_ids_json (a JSON list of option ids)
for the new "multi_select" question type -- select_option_id stays
single-valued and untouched for MCQ; this is purely additive and nullable,
so every existing row and existing question is unaffected.
"""
import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("student_answers", sa.Column("selected_option_ids_json", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("student_answers", "selected_option_ids_json")
