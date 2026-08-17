"""store the id card image submitted during verification

Revision ID: 0014
Revises: 0013
Create Date: 2026-08-03
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
