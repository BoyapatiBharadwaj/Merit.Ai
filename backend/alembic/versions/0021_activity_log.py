"""per-account activity trail

Revision ID: 0021
Revises: 0020
Create Date: 2026-08-04
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None

ACTIVITY_TYPES = (
    "signed_up", "logged_in", "login_failed",
    "face_registered", "id_verified", "id_verification_failed",
    "identity_locked", "identity_unlocked", "biometrics_erased",
    "password_changed", "password_reset_by_admin", "password_reset_by_email",
    "account_created_by_admin", "account_disabled", "account_enabled",
    "account_deleted", "profile_updated",
)


def upgrade() -> None:
    bind = op.get_bind()

    if bind.dialect.name == "postgresql":
        # create_type=False on the column, with the type created explicitly first.
        sa.Enum(*ACTIVITY_TYPES, name="activitytype").create(bind, checkfirst=True)
        activity_enum = postgresql.ENUM(*ACTIVITY_TYPES, name="activitytype", create_type=False)
    else:
        activity_enum = sa.Enum(*ACTIVITY_TYPES, name="activitytype")

    op.create_table(
        "activity_logs",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column("subject_user_id", sa.Integer(),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("actor_user_id", sa.Integer(),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("activity_type", activity_enum, nullable=False),
        sa.Column("description", sa.String(length=255), nullable=True),
        sa.Column("subject_email", sa.String(length=150), nullable=True),
        sa.Column("actor_email", sa.String(length=150), nullable=True),
        sa.Column("ip_address", sa.String(length=45), nullable=True),
        sa.Column("request_id", sa.String(length=32), nullable=True),
        sa.Column("context_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_activity_logs_subject_user_id", "activity_logs", ["subject_user_id"])
    op.create_index("ix_activity_logs_actor_user_id", "activity_logs", ["actor_user_id"])
    op.create_index("ix_activity_logs_activity_type", "activity_logs", ["activity_type"])
    op.create_index("ix_activity_logs_created_at", "activity_logs", ["created_at"])
    # The admin UI's only query: one account's timeline, newest first.
    op.create_index("ix_activity_logs_subject_created", "activity_logs", ["subject_user_id", "created_at"])


def downgrade() -> None:
    for name in ("ix_activity_logs_subject_created", "ix_activity_logs_created_at",
                 "ix_activity_logs_activity_type", "ix_activity_logs_actor_user_id",
                 "ix_activity_logs_subject_user_id"):
        op.drop_index(name, table_name="activity_logs")
    op.drop_table("activity_logs")

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        sa.Enum(name="activitytype").drop(bind, checkfirst=True)
