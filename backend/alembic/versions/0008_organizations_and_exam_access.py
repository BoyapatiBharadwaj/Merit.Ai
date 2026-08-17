"""organizations, rosters and per-exam participant lists

Revision ID: 0008
Revises: 0007
Create Date: 2026-08-02
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.sql import table, column

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

DEFAULT_ORG_NAME = "Default Organization"


def upgrade() -> None:
    op.create_table(
        "organizations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=150), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_index("ix_organizations_id", "organizations", ["id"])
    op.create_index("ix_organizations_name", "organizations", ["name"])

    op.add_column("examiners", sa.Column("organization_id", sa.Integer(), nullable=True))
    op.create_index("ix_examiners_organization_id", "examiners", ["organization_id"])
    op.create_foreign_key("fk_examiners_organization", "examiners", "organizations",
                          ["organization_id"], ["id"], ondelete="RESTRICT")

    op.add_column("students", sa.Column("organization_id", sa.Integer(), nullable=True))
    op.create_index("ix_students_organization_id", "students", ["organization_id"])
    op.create_foreign_key("fk_students_organization", "students", "organizations",
                          ["organization_id"], ["id"], ondelete="SET NULL")

    op.add_column("exams", sa.Column("organization_id", sa.Integer(), nullable=True))
    op.create_index("ix_exams_organization_id", "exams", ["organization_id"])
    op.create_foreign_key("fk_exams_organization", "exams", "organizations",
                          ["organization_id"], ["id"], ondelete="RESTRICT")

    op.create_table(
        "organization_members",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("email", sa.String(length=150), nullable=False),
        sa.Column("student_id", sa.Integer(), nullable=True),
        sa.Column("linked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("invited_by_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["student_id"], ["students.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["invited_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "email", name="uq_org_member_email"),
    )
    op.create_index("ix_organization_members_id", "organization_members", ["id"])
    op.create_index("ix_organization_members_email", "organization_members", ["email"])
    op.create_index("ix_organization_members_organization_id", "organization_members",
                    ["organization_id"])
    op.create_index("ix_organization_members_student_id", "organization_members", ["student_id"])

    op.create_table(
        "exam_participants",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("exam_id", sa.Integer(), nullable=False),
        sa.Column("student_id", sa.Integer(), nullable=False),
        sa.Column("added_by_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["exam_id"], ["exams.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["student_id"], ["students.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["added_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("exam_id", "student_id", name="uq_exam_participant"),
    )
    op.create_index("ix_exam_participants_id", "exam_participants", ["id"])
    op.create_index("ix_exam_participants_exam_id", "exam_participants", ["exam_id"])
    op.create_index("ix_exam_participants_student_id", "exam_participants", ["student_id"])

    _backfill()


def _backfill() -> None:
    """Give every existing row an organization. See the module docstring."""
    connection = op.get_bind()

    organizations = table("organizations", column("id", sa.Integer),
                          column("name", sa.String))

    # --- 1. one organization per distinct examiner organization_name --------
    existing_names = connection.execute(sa.text(
        "SELECT DISTINCT TRIM(organization_name) AS name FROM examiners "
        "WHERE organization_name IS NOT NULL AND TRIM(organization_name) <> ''"
    )).fetchall()

    # Case-insensitive dedupe, keeping the first spelling encountered as the
    # canonical one.
    canonical: dict[str, str] = {}
    for row in existing_names:
        canonical.setdefault(row.name.lower(), row.name)

    names = list(canonical.values())

    # The default organization is created ONLY if something actually needs it.
    needs_default = connection.execute(sa.text(
        "SELECT COUNT(*) FROM examiners "
        "WHERE organization_name IS NULL OR TRIM(organization_name) = ''"
    )).scalar() > 0
    if needs_default and DEFAULT_ORG_NAME.lower() not in canonical:
        names.append(DEFAULT_ORG_NAME)

    if names:
        connection.execute(organizations.insert(), [{"name": name} for name in names])

    org_ids = {row.name.lower(): row.id for row in
               connection.execute(sa.text("SELECT id, name FROM organizations")).fetchall()}
    default_org_id = org_ids.get(DEFAULT_ORG_NAME.lower())

    # --- 2. link examiners, then exams --------------------------------------
    if default_org_id is not None:
        connection.execute(sa.text(
            "UPDATE examiners SET organization_id = :default_id "
            "WHERE organization_name IS NULL OR TRIM(organization_name) = ''"
        ), {"default_id": default_org_id})

    for lowered, original in canonical.items():
        connection.execute(sa.text(
            "UPDATE examiners SET organization_id = :org_id "
            "WHERE LOWER(TRIM(organization_name)) = :lowered"
        ), {"org_id": org_ids[lowered], "lowered": lowered})

    connection.execute(sa.text(
        "UPDATE exams SET organization_id = ("
        "  SELECT organization_id FROM examiners WHERE examiners.id = exams.examiner_id"
        ")"
    ))

    # --- 3. place existing students ---
    # Whichever organization a student has actually been sitting exams for.
    connection.execute(sa.text(
        "UPDATE students SET organization_id = ("
        "  SELECT e.organization_id"
        "  FROM student_exam_attempts a"
        "  JOIN exams e ON e.id = a.exam_id"
        "  WHERE a.student_id = students.id AND e.organization_id IS NOT NULL"
        "  ORDER BY a.id DESC LIMIT 1"
        ") WHERE organization_id IS NULL"
    ))

    # Students with no attempt history: safe to place only when the install is single-tenant,
    # i.e. there is exactly one organization and therefore no ambiguity about where they belong.
    only_org = connection.execute(sa.text(
        "SELECT id FROM organizations LIMIT 2"
    )).fetchall()
    if len(only_org) == 1:
        connection.execute(sa.text(
            "UPDATE students SET organization_id = :org_id WHERE organization_id IS NULL"
        ), {"org_id": only_org[0].id})

    # Give every placed student a roster row, so their membership is visible
    # and removable in the UI rather than being invisible backfilled state.
    connection.execute(sa.text(
        "INSERT INTO organization_members (organization_id, email, student_id, linked_at) "
        "SELECT s.organization_id, LOWER(u.email), s.id, CURRENT_TIMESTAMP "
        "FROM students s JOIN users u ON u.id = s.user_id "
        "WHERE s.organization_id IS NOT NULL"
    ))


def downgrade() -> None:
    op.drop_table("exam_participants")
    op.drop_table("organization_members")

    op.drop_constraint("fk_exams_organization", "exams", type_="foreignkey")
    op.drop_index("ix_exams_organization_id", table_name="exams")
    op.drop_column("exams", "organization_id")

    op.drop_constraint("fk_students_organization", "students", type_="foreignkey")
    op.drop_index("ix_students_organization_id", table_name="students")
    op.drop_column("students", "organization_id")

    op.drop_constraint("fk_examiners_organization", "examiners", type_="foreignkey")
    op.drop_index("ix_examiners_organization_id", table_name="examiners")
    op.drop_column("examiners", "organization_id")

    op.drop_table("organizations")
