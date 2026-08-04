"""add the two enum values that exist in Python but never reached PostgreSQL

Revision ID: 0017
Revises: 0016
Create Date: 2026-08-04

Closes a latent production bug. This schema stores enums as NATIVE PostgreSQL
enum types, so every member of a Python enum needs a matching
`ALTER TYPE ... ADD VALUE` before a row carrying it can be inserted. Two members
never got one:

  * QuestionType.MULTI_SELECT ("multi_select")
      Added to app/models/enums.py alongside migration 0011, which added the
      student_answers.selected_option_ids_json COLUMN but not the enum VALUE.
      questiontype was created in 0004 with exactly ("mcq", "coding").

  * EventType.SCREEN_SHARE_STOPPED ("screen_share_stopped")
      Added with the screen-share lockdown work. eventtype gained values in
      0002, 0003, 0005 and 0007; this one is in none of them.

Why the test suite never caught it: tests/conftest.py builds the schema with
Base.metadata.create_all() against SQLite, which reflects whatever the Python
enums currently say and ignores migration history entirely. Both features
therefore pass every test and fail on the first real INSERT against a migrated
Postgres database -- with `InvalidTextRepresentation`, and for
screen_share_stopped that lands mid-exam, on the lockdown strike path.

This is the same class of bug as the index issue fixed in 0012: invisible to
SQLite, real against Postgres.

Run inside an explicit autocommit block. `ALTER TYPE ... ADD VALUE` cannot run
in a transaction on PostgreSQL before 12, and on 12+ the new value still cannot
be USED until the enclosing transaction commits -- so a later migration in the
same run that tried to insert one of these would fail even though the ALTER
"succeeded". Committing immediately sidesteps both.
"""
from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None

# (enum type name, value to add)
MISSING_VALUES = [
    ("questiontype", "multi_select"),
    ("eventtype", "screen_share_stopped"),
]


def upgrade() -> None:
    bind = op.get_bind()
    # SQLite (and anything else non-Postgres) has no native enum types -- the
    # columns are plain VARCHARs with a CHECK, so there is nothing to alter and
    # the statement would be a syntax error. Skipping keeps this migration
    # runnable end-to-end against SQLite, which is what makes a full-chain
    # migration test possible at all.
    if bind.dialect.name != "postgresql":
        return

    with op.get_context().autocommit_block():
        for type_name, value in MISSING_VALUES:
            op.execute(f"ALTER TYPE {type_name} ADD VALUE IF NOT EXISTS '{value}'")


def downgrade() -> None:
    # No-op, matching 0002/0003/0005/0007. PostgreSQL cannot drop a single enum
    # value: it requires recreating the type and rewriting every column that
    # references it, which would be far more destructive than leaving an unused
    # label in place.
    pass
