"""split names, organization for examiners, identity verification, lockdown events

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-01
"""
from alembic import op
import sqlalchemy as sa

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

NEW_EVENT_VALUES = ["screenshot_attempt", "lockdown_terminated"]


def upgrade() -> None:
    # --- 1. first/last name -------------------------------------------------
    op.add_column("users", sa.Column("first_name", sa.String(75), nullable=False, server_default=""))
    op.add_column("users", sa.Column("last_name", sa.String(75), nullable=False, server_default=""))

    # Backfill: everything before the first space is
    # the first name, the remainder is the last name.
    op.execute(
        """
        UPDATE users
        SET first_name = CASE
                WHEN position(' ' in trim(full_name)) > 0
                    THEN split_part(trim(full_name), ' ', 1)
                ELSE trim(full_name)
            END,
            last_name = CASE
                WHEN position(' ' in trim(full_name)) > 0
                    THEN substr(trim(full_name), position(' ' in trim(full_name)) + 1)
                ELSE ''
            END
        """
    )

    # --- 2. examiner organization ------------------------------------------
    op.alter_column("examiners", "department", new_column_name="organization_name",
                    existing_type=sa.String(100), type_=sa.String(150))

    # --- 3. student identity verification ----------------------------------
    op.add_column("students", sa.Column("id_verified", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("students", sa.Column("id_verified_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("students", sa.Column("id_verified_name", sa.String(150), nullable=True))
    op.add_column("students", sa.Column("identity_locked", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("students", sa.Column("identity_locked_at", sa.DateTime(timezone=True), nullable=True))

    # --- 4. new event types -------------------------------------------------
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        for value in NEW_EVENT_VALUES:
            op.execute(f"ALTER TYPE eventtype ADD VALUE IF NOT EXISTS '{value}'")


def downgrade() -> None:
    op.drop_column("students", "identity_locked_at")
    op.drop_column("students", "identity_locked")
    op.drop_column("students", "id_verified_name")
    op.drop_column("students", "id_verified_at")
    op.drop_column("students", "id_verified")

    op.alter_column("examiners", "organization_name", new_column_name="department",
                    existing_type=sa.String(150), type_=sa.String(100))

    op.drop_column("users", "last_name")
    op.drop_column("users", "first_name")
    # Enum values are intentionally not removed -- Postgres cannot drop a
    # single value without recreating the type and rewriting every row.
