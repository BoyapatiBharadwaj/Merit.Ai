"""archive superseded attempts instead of deleting them

Revision ID: 0023
Revises: 0022
Create Date: 2026-08-05

Granting a retake used to DELETE the candidate's previous attempt, because
student_exam_attempts had a plain unique (student_id, exam_id) constraint and
the new attempt needed the slot. The cascade took the answers, the result, both
comment fields, every proctoring event and every violation screenshot with it,
leaving an AttemptReset row holding a reason string and an integer pointing at
an attempt that no longer existed.

A reset is granted after something went wrong -- a crash, a disconnection, a
proctoring interruption, an accusation. That is exactly when somebody may later
need to see what happened, so the evidence was being destroyed by the action
most likely to precede a request to examine it.

What changes here:

  * `archived_at` and `archived_by_reset_id` on student_exam_attempts.
  * The unique constraint becomes a PARTIAL unique index scoped to
    `archived_at IS NULL`. The rule is "one LIVE attempt per student per exam",
    not "one attempt ever", and that distinction is the whole fix.

The constraint swap is the delicate part. It is dropped and recreated as an
index rather than altered, because PostgreSQL implements a UNIQUE constraint as
a constraint-backed index that cannot be given a WHERE clause in place. Existing
rows all have archived_at NULL, so every one of them is inside the partial
index's predicate and the new index enforces exactly what the old constraint did
for present data -- no row can violate it at creation time.
"""
import sqlalchemy as sa
from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None

# The constraint's name differs by how it was created: models declare
# uq_student_exam, but a database built by an older create_all may carry
# PostgreSQL's generated name instead. Both are tried.
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
        # SQLite cannot drop a constraint in place, and the suite builds its
        # schema with create_all rather than through this chain, so the index is
        # created and the old constraint left alone. A SQLite deployment is not
        # a supported production target for this application.
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
        # Archived rows would violate the plain constraint being restored --
        # they are the duplicates it forbids. Removing them is the only way back,
        # and is exactly what the old behaviour did to them anyway.
        op.execute("DELETE FROM student_exam_attempts WHERE archived_at IS NOT NULL")
        op.drop_constraint("fk_attempt_archived_by_reset", "student_exam_attempts",
                           type_="foreignkey")
        op.create_unique_constraint("uq_student_exam", "student_exam_attempts",
                                    ["student_id", "exam_id"])

    op.drop_column("student_exam_attempts", "archived_by_reset_id")
    op.drop_column("student_exam_attempts", "archived_at")
