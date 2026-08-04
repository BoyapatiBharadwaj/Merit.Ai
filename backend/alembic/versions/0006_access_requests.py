"""examiner access requests

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-01

Adds the public "request examiner access" queue. Examiners cannot
self-register, so this table is the supported path from an interested
institution to an admin-created examiner account.

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

# Owns the CREATE TYPE / DROP TYPE for the enum, called explicitly below.
access_request_status_enum = sa.Enum("pending", "approved", "rejected", name="accessrequeststatus")

# The same Postgres type, but referenced with create_type=False so that
# op.create_table() only *uses* it and does not try to define it again.
#
# Why this matters: a plain sa.Enum attached to a column makes SQLAlchemy fire
# its _on_table_create hook during create_table, which re-issues CREATE TYPE --
# and Alembic's create_table does not pass checkfirst, so that second statement
# is unconditional. Combined with the explicit create() above it raises
# DuplicateObject on every fresh database. Because Alembic runs the whole
# upgrade inside a single transaction on Postgres, that failure rolled back
# migrations 0001-0005 as well, leaving the database completely empty while the
# log still showed all six revisions "Running upgrade".
#
# Migration 0001 avoids the clash by never pre-creating its enums (create_table
# defines them); 0004 avoids it because add_column does not fire the hook. This
# migration is the only one that did both, which is why it was the only one that
# failed.
access_request_status_column = postgresql.ENUM(
    "pending", "approved", "rejected", name="accessrequeststatus", create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    access_request_status_enum.create(bind, checkfirst=True)

    op.create_table(
        "access_requests",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column("first_name", sa.String(75), nullable=False),
        sa.Column("last_name", sa.String(75), nullable=False),
        sa.Column("email", sa.String(150), nullable=False, index=True),
        sa.Column("organization_name", sa.String(150), nullable=False),
        sa.Column("purpose", sa.Text(), nullable=False),
        sa.Column("status", access_request_status_column, nullable=False, server_default="pending", index=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        # SET NULL rather than CASCADE: deleting an admin must not erase the
        # record that a request was reviewed.
        sa.Column("reviewed_by_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("review_note", sa.String(255), nullable=True),
        sa.Column("created_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), index=True),
    )


def downgrade() -> None:
    op.drop_table("access_requests")
    access_request_status_enum.drop(op.get_bind(), checkfirst=True)
