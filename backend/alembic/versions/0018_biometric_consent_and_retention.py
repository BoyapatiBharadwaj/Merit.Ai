"""biometric consent, erasure audit and retention

Revision ID: 0018
Revises: 0017
Create Date: 2026-08-04
"""
import sqlalchemy as sa
from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("face_profiles", sa.Column("consent_version", sa.String(length=50), nullable=True))
    op.add_column("face_profiles", sa.Column("consented_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("face_profiles", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("face_profiles", sa.Column("deletion_reason", sa.String(length=100), nullable=True))

    op.add_column("students", sa.Column("id_consent_version", sa.String(length=50), nullable=True))
    op.add_column("students", sa.Column("id_consented_at", sa.DateTime(timezone=True), nullable=True))

    # The retention sweep asks "which live profiles exist" on every pass; without
    # this it is a full scan of a table with one row per student.
    op.create_index("ix_face_profiles_deleted_at", "face_profiles", ["deleted_at"])


def downgrade() -> None:
    op.drop_index("ix_face_profiles_deleted_at", table_name="face_profiles")
    op.drop_column("students", "id_consented_at")
    op.drop_column("students", "id_consent_version")
    op.drop_column("face_profiles", "deletion_reason")
    op.drop_column("face_profiles", "deleted_at")
    op.drop_column("face_profiles", "consented_at")
    op.drop_column("face_profiles", "consent_version")
