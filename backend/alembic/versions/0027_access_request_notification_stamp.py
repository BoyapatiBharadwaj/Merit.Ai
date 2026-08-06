"""record when admins were last notified about an access request

Revision ID: 0027
Revises: 0026
Create Date: 2026-08-05

The de-dupe in access_request_service.submit was permanent. Once a pending row
existed for an address, submit() returned it early and never sent another
notification, however long ago that was. The intent was right -- a double-click
must not fill the admin's inbox -- but "never again" is the wrong duration:

  * A request submitted while email was misconfigured stayed permanently
    unannounced. The queue showed it; no message was ever sent.
  * Resubmitting, the obvious thing a person tries, silently did nothing.
    From outside that is indistinguishable from a broken mail server -- and
    it was mistaken for exactly that.

`last_notified_at` turns the permanent suppression into a cooldown
(ACCESS_REQUEST_RENOTIFY_SECONDS, default 15 minutes). Two clicks a second
apart still send one email; submitting again an hour later gets through.

Backfilled as NULL rather than now(). NULL reads as "never notified", so any
request currently stuck in the queue re-notifies on its next submission --
which is the whole point. Stamping now() would preserve the bug for exactly the
rows that are suffering from it.
"""
import sqlalchemy as sa
from alembic import op

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "access_requests",
        sa.Column("last_notified_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("access_requests", "last_notified_at")
