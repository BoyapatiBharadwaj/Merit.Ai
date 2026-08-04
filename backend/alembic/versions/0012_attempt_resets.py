"""attempt reset audit trail

Revision ID: 0012
Revises: 0011
Create Date: 2026-08-03

Adds the attempt_resets table -- the audit trail for an examiner resetting a
student's exam attempt (see attempt_service.reset_student_attempt). Resetting
deletes the original StudentExamAttempt row (the student_id/exam_id unique
constraint means a fresh retake can't coexist with the old row), so the
who/when/why/what-it-looked-like-before facts worth keeping are captured here
as plain historical columns rather than live foreign keys into a row that is
about to stop existing.
"""
import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "attempt_resets",
        sa.Column("id", sa.Integer(), primary_key=True),
        # No index=True here (unlike the AttemptReset model, which uses it so
        # Base.metadata.create_all in tests gets the same indexes) -- passing
        # index=True on a Column inside op.create_table has SQLAlchemy create
        # the index as part of table creation, which would collide with the
        # explicit op.create_index calls below ("relation already exists").
        sa.Column("exam_id", sa.Integer(), sa.ForeignKey("exams.id", ondelete="CASCADE"), nullable=False),
        sa.Column("student_id", sa.Integer(), sa.ForeignKey("students.id", ondelete="CASCADE"), nullable=False),
        sa.Column("examiner_id", sa.Integer(), sa.ForeignKey("examiners.id", ondelete="SET NULL"), nullable=True),
        sa.Column("previous_attempt_id", sa.Integer(), nullable=False),
        sa.Column("previous_status", sa.String(length=30), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_attempt_resets_exam_id", "attempt_resets", ["exam_id"])
    op.create_index("ix_attempt_resets_student_id", "attempt_resets", ["student_id"])


def downgrade() -> None:
    op.drop_index("ix_attempt_resets_student_id", table_name="attempt_resets")
    op.drop_index("ix_attempt_resets_exam_id", table_name="attempt_resets")
    op.drop_table("attempt_resets")
