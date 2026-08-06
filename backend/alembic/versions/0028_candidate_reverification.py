"""let an administrator require a candidate to verify their identity again

Revision ID: 0028
Revises: 0027
Create Date: 2026-08-05

Until now the only way to make a candidate re-prove who they are was to erase
their biometrics, which is the wrong instrument for the job. Erasure is a
privacy action -- somebody asked for their data to be removed. Re-verification
is an integrity action -- somebody looked at the enrolled face or ID card and
doubted it. Those pull in opposite directions on the same data: the moment you
doubt the evidence is exactly the moment you must keep it, because it is what a
review will need to look at.

So this adds a flag rather than a delete. While
`reverification_required_at` is set, identity_service.verification_state
reports exam_ready = False and the exam gate refuses entry with a message that
names this specific situation (telling a candidate whose face and ID are both
on file that their "verification is incomplete" is simply false, and sends them
to a Profile page showing two green ticks). The stored photo and embedding stay
readable throughout, and are replaced only when the candidate submits new ones.

`reverification_reason` is shown to the candidate verbatim, because a demand
with no stated reason is indistinguishable from a malfunction at the receiving
end.

All three columns are nullable with no backfill: NULL means "nothing has been
asked of this candidate", which is true of every existing row.
"""
import sqlalchemy as sa
from alembic import op

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("students", sa.Column("reverification_required_at",
                                        sa.DateTime(timezone=True), nullable=True))
    op.add_column("students", sa.Column("reverification_reason", sa.String(length=500), nullable=True))
    op.add_column("students", sa.Column("reverification_requested_by_id", sa.Integer(), nullable=True))
    # ondelete=SET NULL: an administrator's account being deleted must not take
    # the outstanding request with it, nor block the deletion.
    op.create_foreign_key(
        "fk_students_reverification_requested_by", "students", "users",
        ["reverification_requested_by_id"], ["id"], ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_students_reverification_requested_by", "students", type_="foreignkey")
    op.drop_column("students", "reverification_requested_by_id")
    op.drop_column("students", "reverification_reason")
    op.drop_column("students", "reverification_required_at")
