"""let an examiner control whether, and when, candidates see their results

Revision ID: 0031
Revises: 0030
Create Date: 2026-08-06
"""
import sqlalchemy as sa
from alembic import op

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None

_RESULTS_RELEASE_MODE = sa.Enum("immediate", "after_end_time", name="resultsreleasemode")


def upgrade() -> None:
    _RESULTS_RELEASE_MODE.create(op.get_bind(), checkfirst=True)
    op.add_column("exams", sa.Column("show_results", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column("exams", sa.Column(
        "results_release_mode", _RESULTS_RELEASE_MODE, nullable=False, server_default="immediate",
    ))
    # The server_default did the backfill for existing rows; drop it so the
    # column's steady-state definition matches the model (every future INSERT
    # goes through the ORM, which always supplies a value).
    op.alter_column("exams", "show_results", server_default=None)
    op.alter_column("exams", "results_release_mode", server_default=None)


def downgrade() -> None:
    op.drop_column("exams", "results_release_mode")
    op.drop_column("exams", "show_results")
    _RESULTS_RELEASE_MODE.drop(op.get_bind(), checkfirst=True)
