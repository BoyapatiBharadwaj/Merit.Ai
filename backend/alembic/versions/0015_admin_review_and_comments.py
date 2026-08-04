"""admin violation review decisions and attempt comments

Revision ID: 0015
Revises: 0014
Create Date: 2026-08-03

Backs the admin drill-down dashboard's candidate exam report:

  * proctor_events.admin_decision -- an admin's review verdict
    (pending/confirmed/dismissed) on one logged violation, defaulting to
    "pending" so every already-logged violation starts out needing a look
    rather than silently reading as handled.
  * student_exam_attempts.examiner_comment / admin_comment -- free-text notes
    on one candidate's attempt, one column per role so neither can silently
    overwrite the other's note.

All three are purely additive and never touch scoring or attempt status.
"""
import sqlalchemy as sa
from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None

ADMIN_DECISION_VALUES = ("pending", "confirmed", "dismissed")


def upgrade() -> None:
    bind = op.get_bind()
    admin_decision_enum = sa.Enum(*ADMIN_DECISION_VALUES, name="admindecision")
    admin_decision_enum.create(bind, checkfirst=True)

    op.add_column(
        "proctor_events",
        sa.Column("admin_decision", admin_decision_enum, nullable=False, server_default="pending"),
    )
    op.add_column("student_exam_attempts", sa.Column("examiner_comment", sa.Text(), nullable=True))
    op.add_column("student_exam_attempts", sa.Column("admin_comment", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("student_exam_attempts", "admin_comment")
    op.drop_column("student_exam_attempts", "examiner_comment")
    op.drop_column("proctor_events", "admin_decision")

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        sa.Enum(name="admindecision").drop(bind, checkfirst=True)
