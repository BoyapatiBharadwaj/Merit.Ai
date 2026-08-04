"""biometric consent, erasure audit and retention

Revision ID: 0018
Revises: 0017
Create Date: 2026-08-04

Gives the biometric data this platform stores a lifecycle it previously did not
have at all -- no recorded consent, no expiry, no deletion path short of
hand-written SQL (see app/services/biometric_service.py).

  face_profiles.consent_version / consented_at
      Which consent wording was in force when this face was captured. Stored
      per profile, not once per student: consent only means anything against
      the text that was actually shown, so bumping BIOMETRIC_CONSENT_VERSION
      must leave earlier captures identifiable as consented-to-something-else
      rather than silently inheriting agreement to new terms.

  face_profiles.deleted_at / deletion_reason
      Erasure is recorded, not inferred. The row deliberately survives erasure
      -- attempts reference it, and a cascade from students would take
      assessment history with it -- so these two columns are what distinguish
      "never captured" from "captured and since erased, on this date, for this
      reason", which is itself usually a compliance requirement.

  students.id_consent_version / id_consented_at
      The same, for the ID-card upload. Separate because the two captures
      happen at different moments and a candidate may complete one and abandon
      the other.

Every column is nullable with no server default, so existing rows stay valid.
A NULL consent_version means "captured before consent was recorded", which is
true and is exactly what an institution auditing historical data needs to see --
backfilling it with the current version would manufacture consent that was
never actually given.
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
