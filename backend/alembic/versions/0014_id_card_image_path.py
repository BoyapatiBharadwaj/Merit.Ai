"""store the id card image submitted during verification

Revision ID: 0014
Revises: 0013
Create Date: 2026-08-03

students.id_card_image_path -- the ID card photo captured during
POST /proctoring/id-card/verify, saved to disk the same way a registered
face photo already is (see FaceProfile.image_path). Previously this image
was decoded, OCR'd, and discarded within the same request -- nothing kept
it afterwards, so nobody (student, examiner, or admin) could ever look at
what was actually submitted. Nullable and purely additive: a student who
verified before this migration simply has no image on file until they
re-verify, which does not affect their already-recorded id_verified flag.
"""
import sqlalchemy as sa
from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("students", sa.Column("id_card_image_path", sa.String(255), nullable=True))


def downgrade() -> None:
    op.drop_column("students", "id_card_image_path")
