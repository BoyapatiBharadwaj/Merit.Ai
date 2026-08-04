"""exam instructions, randomize_options, and per-attempt option order

Revision ID: 0013
Revises: 0012
Create Date: 2026-08-03

Three additive, backward-compatible columns for this feature set:
  * exams.instructions -- optional longer-form candidate guidance, distinct
    from the existing `description` (a short card blurb).
  * exams.randomize_options -- independent of randomize_questions; shuffles
    each MCQ/multi_select question's own option order per attempt.
  * student_exam_attempts.option_order_json -- where that per-attempt
    shuffle is actually stored (see attempt_service._build_option_order),
    mirroring the existing question_order column's role for questions.

randomize_options defaults to true (server_default), matching
randomize_questions' existing default, so every already-published exam
starts behaving the same way a newly created one would rather than silently
opting out.
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
