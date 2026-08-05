"""activation links instead of mailed passwords

Revision ID: 0026
Revises: 0025
Create Date: 2026-08-05

Two changes, one column and one enum value.

`users.must_change_password` records that an account holds a password its owner
never chose. Approving an access request used to have the administrator type a
password which was then emailed to the new examiner in plain text. That is wrong
in three ways no warning label fixes: the password sits in a mailbox
indefinitely, the administrator knows it (so nothing the account does is
attributable to its owner alone), and a mailbox compromise hands over a live
credential rather than an expiring link. Accounts are now created with an
unusable random secret, and the owner sets the real one through a single-use
activation link.

`otppurpose.activation` lets those links reuse the OTP table, whose mechanics
are already exactly right for this: a hashed, expiring, single-use secret keyed
on an email address. Only the shape of the secret differs (256 random bits in a
link, rather than six digits to type) and nothing in the schema cares.

Backfill: FALSE for every existing row -- deliberately, not for convenience.
Turning it on for accounts created under the old flow would lock out every
examiner in the middle of whatever they were doing, to remediate a password
they may well have already changed. New accounts get the guarantee; existing
ones are told to rotate, which is a conversation rather than a migration.
"""
import sqlalchemy as sa
from alembic import op

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()

    op.add_column(
        "users",
        sa.Column("must_change_password", sa.Boolean(), nullable=False,
                  server_default=sa.false()),
    )

    if bind.dialect.name == "postgresql":
        # SQLite has no native enum -- the column is a VARCHAR with a CHECK that
        # SQLAlchemy renders from the Python enum, so nothing to alter there.
        # ADD VALUE cannot run inside a transaction block on older servers;
        # COMMIT first, matching what migration 0003 does for eventtype.
        op.execute("COMMIT")
        op.execute("ALTER TYPE otppurpose ADD VALUE IF NOT EXISTS 'activation'")


def downgrade() -> None:
    op.drop_column("users", "must_change_password")
    # The enum value stays. Postgres cannot drop one without recreating the type
    # and rewriting every row that references it, and an unused value is inert.
