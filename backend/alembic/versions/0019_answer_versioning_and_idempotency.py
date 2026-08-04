"""optimistic concurrency for autosaved answers

Revision ID: 0019
Revises: 0018
Create Date: 2026-08-04

Fixes silent answer loss.

The answer path was unconditional last-write-wins: attempt_repository's upserts
overwrote whatever was stored, regardless of which write represented the
candidate's newer intent. That is not theoretical. The exam client autosaves on
every change and retries with backoff on network errors (see Exam.jsx's
persistMcqAnswer), so a request stalled on a slow connection routinely lands
AFTER a newer one for the same question -- silently reverting an answer the
candidate had already changed. Nothing surfaced to them, and nothing in the logs
distinguished it from them simply choosing that option.

Three columns on student_answers:

  answer_version    A per-(attempt, question) counter owned by the CLIENT and
                    incremented on every local change. The server refuses any
                    write older than what it holds. Client-owned because the
                    ordering that matters is the order the candidate made the
                    changes in, which only the client observes -- a server-issued
                    version would merely re-derive arrival order, which is the
                    thing already going wrong.

  idempotency_key   The last accepted request's unique id, so a retry of a
                    request that DID land (but whose response was lost) is a
                    no-op rather than a second write. Exactly what the client's
                    network-error backoff produces.

  saved_at          Server receipt time, stamped only when a write is actually
                    APPLIED. Distinct from answered_at, whose onupdate fires on
                    any UPDATE -- this stays a true record of when the answer
                    last changed.

Backfill: answer_version defaults to 0 for every existing row, which is below
any version a client will send, so in-flight attempts keep working and their
first post-upgrade save is accepted normally. Both other columns are nullable.
Nothing here changes how answers are graded or read.
"""
import sqlalchemy as sa
from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # server_default rather than a plain default: existing rows need a value at
    # the moment the column is added, and NOT NULL without one would fail on any
    # table that already has answers in it -- i.e. every real deployment.
    op.add_column(
        "student_answers",
        sa.Column("answer_version", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("student_answers", sa.Column("idempotency_key", sa.String(length=64), nullable=True))
    op.add_column("student_answers", sa.Column("saved_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("student_answers", "saved_at")
    op.drop_column("student_answers", "idempotency_key")
    op.drop_column("student_answers", "answer_version")
