"""per-exam requirements, and holding results until release

Revision ID: 0025
Revises: 0024
Create Date: 2026-08-05
"""
import sqlalchemy as sa
from alembic import op

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None

_REQUIREMENTS = ("require_camera", "require_microphone", "require_screen_share", "require_fullscreen")


def upgrade() -> None:
    for column in _REQUIREMENTS:
        op.add_column("exams", sa.Column(column, sa.Boolean(), nullable=True))

    op.add_column("exams", sa.Column("release_results_at", sa.DateTime(timezone=True), nullable=True))
    # server_default so the column can be NOT NULL without a separate backfill
    # pass over a populated table.
    op.add_column("exams", sa.Column("show_answers_on_release", sa.Boolean(),
                                     nullable=False, server_default=sa.true()))


def downgrade() -> None:
    op.drop_column("exams", "show_answers_on_release")
    op.drop_column("exams", "release_results_at")
    for column in reversed(_REQUIREMENTS):
        op.drop_column("exams", column)
