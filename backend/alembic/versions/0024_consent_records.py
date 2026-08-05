"""record what a candidate consented to, and when

Revision ID: 0024
Revises: 0023
Create Date: 2026-08-05

The registration form's two consent checkboxes -- the Terms, and a separate
acknowledgement that identity and proctoring data are collected -- were enforced
entirely in React. They were never in the request body, so any caller that
skipped the browser registered without agreeing to anything, and even for
someone who ticked them nothing recorded that they had, when, or which wording
they saw.

On a platform whose whole premise is collecting a face photograph and an ID card
image, that is the one consent record that matters, and it did not exist.

Nullable, and NOT backfilled. Marking existing accounts as having consented
would be inventing a record of something that may never have happened -- the
opposite of what a consent trail is for. NULL is the honest answer for an
account created before anyone was asked, and it is a value the application can
act on later (prompt for consent at next sign-in) rather than a lie it cannot.
"""
import sqlalchemy as sa
from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("terms_accepted_at", sa.DateTime(timezone=True), nullable=True))
    # The version string, not just a boolean: terms change, and "they agreed"
    # without "to what" cannot answer a dispute. Storing it also means a later
    # revision cannot be applied retroactively to people who never saw it.
    op.add_column("users", sa.Column("terms_version", sa.String(length=20), nullable=True))
    op.add_column("users", sa.Column("proctoring_consent_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "proctoring_consent_at")
    op.drop_column("users", "terms_version")
    op.drop_column("users", "terms_accepted_at")
