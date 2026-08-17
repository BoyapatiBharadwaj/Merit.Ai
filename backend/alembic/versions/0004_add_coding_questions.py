"""add coding questions (question_type, language, starter_code, test cases, time limit) and
per-attempt code submissions/results

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-01
"""
import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

questiontype = sa.Enum("mcq", "coding", name="questiontype")


def upgrade() -> None:
    bind = op.get_bind()
    questiontype.create(bind, checkfirst=True)

    op.add_column("questions", sa.Column("question_type", questiontype, nullable=False, server_default="mcq"))
    op.add_column("questions", sa.Column("language", sa.String(length=20), nullable=True))
    op.add_column("questions", sa.Column("starter_code", sa.Text(), nullable=True))
    op.add_column("questions", sa.Column("test_cases_json", sa.Text(), nullable=True))
    op.add_column("questions", sa.Column("time_limit_seconds", sa.Integer(), nullable=True))

    op.add_column("student_answers", sa.Column("code_submission", sa.Text(), nullable=True))
    op.add_column("student_answers", sa.Column("code_test_results_json", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("student_answers", "code_test_results_json")
    op.drop_column("student_answers", "code_submission")

    op.drop_column("questions", "time_limit_seconds")
    op.drop_column("questions", "test_cases_json")
    op.drop_column("questions", "starter_code")
    op.drop_column("questions", "language")
    op.drop_column("questions", "question_type")

    bind = op.get_bind()
    questiontype.drop(bind, checkfirst=True)
