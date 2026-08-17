"""archive superseded attempts instead of deleting them

Revision ID: 0023
Revises: 0022
Create Date: 2026-08-05
"""
import sqlalchemy as sa
from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None

# The constraint's name differs by how it was created.
_LEGACY_CONSTRAINT_NAMES = ("uq_student_exam", "student_exam_attempts_student_id_exam_id_key")


def upgrade() -> None:
    op.add_column("student_exam_attempts",
                  sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("student_exam_attempts",
                  sa.Column("archived_by_reset_id", sa.Integer(), nullable=True))

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.create_foreign_key(
            "fk_attempt_archived_by_reset", "student_exam_attempts", "attempt_resets",
            ["archived_by_reset_id"], ["id"], ondelete="SET NULL",
        )
        # ON DELETE SET NULL, not CASCADE: deleting a reset record must never
        # take the attempt it superseded with it.
        for name in _LEGACY_CONSTRAINT_NAMES:
            op.execute(
                f'ALTER TABLE student_exam_attempts DROP CONSTRAINT IF EXISTS "{name}"'
            )
        op.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_active_student_exam "
            "ON student_exam_attempts (student_id, exam_id) WHERE archived_at IS NULL"
        )
    else:
        # SQLite cannot drop a constraint in place, and the suite builds
        # its schema with create_all rather than through this chain, so
        # the index is created and the old constraint left alone.
        op.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_active_student_exam "
            "ON student_exam_attempts (student_id, exam_id) WHERE archived_at IS NULL"
        )

    op.create_index("ix_student_exam_attempts_archived_at", "student_exam_attempts", ["archived_at"])


def downgrade() -> None:
    op.drop_index("ix_student_exam_attempts_archived_at", table_name="student_exam_attempts")
    op.execute("DROP INDEX IF EXISTS uq_active_student_exam")

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        # Archived rows would violate the plain constraint
        # being restored -- they are the duplicates it forbids.
        op.execute("DELETE FROM student_exam_attempts WHERE archived_at IS NOT NULL")
        op.drop_constraint("fk_attempt_archived_by_reset", "student_exam_attempts",
                           type_="foreignkey")
        op.create_unique_constraint("uq_student_exam", "student_exam_attempts",
                                    ["student_id", "exam_id"])

    op.drop_column("student_exam_attempts", "archived_by_reset_id")
    op.drop_column("student_exam_attempts", "archived_at")
