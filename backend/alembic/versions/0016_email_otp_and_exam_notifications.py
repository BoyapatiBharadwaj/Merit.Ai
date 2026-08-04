"""one-time passcodes and exam notification email

Revision ID: 0016
Revises: 0015
Create Date: 2026-08-04

Backs the outbound-email features (see app/services/email_service.py):

  * otp_codes -- one-time passcodes for signup verification and password
    reset. Codes are stored as HMAC-SHA256 digests, never in plaintext, so
    the column is a fixed 64-character hex string rather than the 6 digits a
    user actually types (see otp_service._digest).
  * exams.notify_email -- the address an examiner nominates to be told when
    the exam is published and again shortly before it starts.
  * exams.reminder_sent_at -- stamped once by reminder_service, and the only
    thing making the pre-exam reminder exactly-once rather than once per poll.

Both exam columns are nullable with no server default, so every existing exam
row is valid the moment this lands: a NULL notify_email simply means "nobody
asked to be notified", which is exactly the behaviour before this migration.

A native `otppurpose` enum is created for the purpose column, matching how
every other enum in this schema is stored. Postgres enums cannot be extended
inside a transaction, so any future purpose needs its own
`ALTER TYPE ... ADD VALUE` migration -- see 0003 for the established pattern.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None

OTP_PURPOSE_VALUES = ("signup", "password_reset")


def upgrade() -> None:
    bind = op.get_bind()

    if bind.dialect.name == "postgresql":
        # Create the type explicitly, then reference it with create_type=False.
        #
        # Without that flag this migration fails on PostgreSQL with
        # `DuplicateObject: type "otppurpose" already exists`: op.create_table
        # runs SQLAlchemy's full DDL visitor, which emits CREATE TYPE for every
        # Enum column it sees -- and unlike the explicit .create() above it does
        # not pass checkfirst, so the type gets created twice.
        #
        # This is exactly the class of bug the SQLite test suite cannot see
        # (tests build the schema with create_all, never by running migrations),
        # and it was caught by actually running the chain against a real
        # PostgreSQL instance -- see tests/test_migrations_postgres.py.
        #
        # Note this differs from 0004/0015, which create their types the same
        # way but attach them with op.add_column; that path emits a plain
        # ALTER TABLE ADD COLUMN and does not re-create the type.
        sa.Enum(*OTP_PURPOSE_VALUES, name="otppurpose").create(bind, checkfirst=True)
        otp_purpose_enum = postgresql.ENUM(*OTP_PURPOSE_VALUES, name="otppurpose", create_type=False)
    else:
        # SQLite and friends have no native enum types -- this renders as a
        # VARCHAR with a CHECK constraint, created inline with the table.
        otp_purpose_enum = sa.Enum(*OTP_PURPOSE_VALUES, name="otppurpose")

    op.create_table(
        "otp_codes",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column("email", sa.String(length=150), nullable=False),
        sa.Column("purpose", otp_purpose_enum, nullable=False),
        # 64 hex characters -- HMAC-SHA256. Never the code itself.
        sa.Column("code_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_otp_codes_email", "otp_codes", ["email"])
    op.create_index("ix_otp_codes_created_at", "otp_codes", ["created_at"])
    # Composite, matching the only shape otp_repository ever queries: the newest
    # row for one (email, purpose). Without it every verify is a full scan of a
    # table that grows monotonically until purge_expired sweeps it.
    op.create_index("ix_otp_codes_email_purpose_created", "otp_codes", ["email", "purpose", "created_at"])

    op.add_column("exams", sa.Column("notify_email", sa.String(length=150), nullable=True))
    op.add_column("exams", sa.Column("reminder_sent_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("exams", "reminder_sent_at")
    op.drop_column("exams", "notify_email")

    op.drop_index("ix_otp_codes_email_purpose_created", table_name="otp_codes")
    op.drop_index("ix_otp_codes_created_at", table_name="otp_codes")
    op.drop_index("ix_otp_codes_email", table_name="otp_codes")
    op.drop_table("otp_codes")

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        sa.Enum(name="otppurpose").drop(bind, checkfirst=True)
