"""email verification state and a password-change epoch

Revision ID: 0022
Revises: 0021
Create Date: 2026-08-05

Two nullable timestamps on users, both of which close a hole that existed
because the information simply was not recorded anywhere:

  * email_verified_at -- verified and unverified accounts were indistinguishable.
    The OTP was checked and then forgotten, so nothing could display, enforce or
    re-require verification, and an address changed after being verified
    inherited the old address's trust.

  * password_changed_at -- tokens carried only sub/role/exp, so resetting a
    password did nothing to a token already stolen from that account. This is
    the cutoff every token is now checked against.

Backfill is the interesting part of this migration.

  * email_verified_at stays NULL for every existing row. Marking them verified
    would be a lie about accounts that never proved anything, and NULL is the
    honest answer to "did this person confirm their address?" for an account
    created before anyone was asked.

  * password_changed_at is backfilled to each user's created_at, NOT to now().
    Using now() would stamp the epoch later than every token currently in
    circulation and sign every candidate mid-exam straight out. created_at is
    both true (the password was set when the account was made) and safely in the
    past, so live sessions survive the deployment.
"""
import sqlalchemy as sa
from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("password_changed_at", sa.DateTime(timezone=True), nullable=True))

    # See the module docstring: created_at, not now(), so deploying this does
    # not invalidate every token in flight.
    op.execute("UPDATE users SET password_changed_at = created_at WHERE password_changed_at IS NULL")

    # Partial-ish index for "show me the accounts that never verified", which is
    # the query an administrator actually runs against this column. Plain btree
    # rather than a partial index so it works identically on SQLite, which the
    # test suite builds with create_all and which has no partial-index support
    # worth relying on here.
    op.create_index("ix_users_email_verified_at", "users", ["email_verified_at"])


def downgrade() -> None:
    op.drop_index("ix_users_email_verified_at", table_name="users")
    op.drop_column("users", "password_changed_at")
    op.drop_column("users", "email_verified_at")
