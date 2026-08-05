"""per-exam requirements, and holding results until release

Revision ID: 0025
Revises: 0024
Create Date: 2026-08-05

Two independent problems, both solved by letting the examiner say what they
mean instead of inferring it from one boolean.

`require_camera` / `require_microphone` / `require_screen_share` /
`require_fullscreen`: `proctoring_enabled` gated the AI signals, but the exam
page demanded camera, microphone, screen sharing AND fullscreen from every
candidate regardless -- and then told them "this exam is not proctored". An
ordinary unproctored quiz still required someone to hand over their webcam and
share their screen for no purpose anyone could name.

`release_results_at` / `show_answers_on_release`: the candidate report returned
correct options, correct-answer text and explanations the instant an attempt was
submitted. The first person to finish held the complete answer key while
everyone else was still writing, and could simply send it to them.

Every column is NULLABLE (or defaulted to today's behaviour) and NOT
backfilled with anything opinionated:

  * The four require_* columns stay NULL, which Exam.requires() reads as
    "follow proctoring_enabled" -- so every exam already created behaves
    exactly as it did, and an examiner only sets them when they want something
    different.
  * release_results_at stays NULL, which means "released immediately" -- again
    today's behaviour. Retroactively withholding results from candidates who
    have already been shown theirs would be a worse surprise than the leak.
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
