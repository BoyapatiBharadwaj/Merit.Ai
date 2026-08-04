"""per-exam allow-list keyed on email instead of student id

Revision ID: 0009
Revises: 0008
Create Date: 2026-08-02

exam_participants originally referenced students.id, which meant an exam could
only be restricted to people who had already registered. That is backwards for
the case it exists to serve: an examiner sets up an exam *before* the cohort
signs up. Keying on email lets them paste a list straight away, and the row
resolves to a real account whenever that person registers.

student_id survives as a nullable cache for display ("Registered" vs
"Invited"). It is not what authorises access -- see the ExamParticipant
docstring and can_student_access_exam, which match on id OR email.
"""
import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("exam_participants", sa.Column("email", sa.String(length=150), nullable=True))
    op.add_column("exam_participants", sa.Column("linked_at", sa.DateTime(timezone=True), nullable=True))

    # Backfill email from the student each existing row points at, so any
    # allow-list configured before this migration keeps working unchanged.
    op.execute(sa.text(
        "UPDATE exam_participants SET email = ("
        "  SELECT LOWER(u.email) FROM students s JOIN users u ON u.id = s.user_id"
        "  WHERE s.id = exam_participants.student_id"
        "), linked_at = CURRENT_TIMESTAMP"
    ))
    # A row whose student vanished has nothing to key on and can never match
    # anyone; leaving it would silently keep the exam in allow-list mode
    # while granting access to no one.
    op.execute(sa.text("DELETE FROM exam_participants WHERE email IS NULL"))

    with op.batch_alter_table("exam_participants") as batch:
        batch.alter_column("email", existing_type=sa.String(length=150), nullable=False)
        # student_id becomes nullable: a pending invite has no account yet.
        batch.alter_column("student_id", existing_type=sa.Integer(), nullable=True)
        batch.drop_constraint("uq_exam_participant", type_="unique")
        batch.create_unique_constraint("uq_exam_participant_email", ["exam_id", "email"])

    op.create_index("ix_exam_participants_email", "exam_participants", ["email"])


def downgrade() -> None:
    # Rows that never resolved to an account cannot be represented by the old
    # student_id-only schema, so they are dropped rather than silently
    # becoming NULL-keyed junk.
    op.execute(sa.text("DELETE FROM exam_participants WHERE student_id IS NULL"))
    op.drop_index("ix_exam_participants_email", table_name="exam_participants")
    with op.batch_alter_table("exam_participants") as batch:
        batch.drop_constraint("uq_exam_participant_email", type_="unique")
        batch.create_unique_constraint("uq_exam_participant", ["exam_id", "student_id"])
        batch.alter_column("student_id", existing_type=sa.Integer(), nullable=False)
    op.drop_column("exam_participants", "linked_at")
    op.drop_column("exam_participants", "email")
