"""one-time passcodes and exam notification email

Revision ID: 0016
Revises: 0015
Create Date: 2026-08-04
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
    # Composite, matching the only shape otp_repository ever
    # queries: the newest row for one (email, purpose).
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
