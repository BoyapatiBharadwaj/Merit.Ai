"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-07-31
"""
from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


exam_status_enum = sa.Enum("draft", "published", "closed", name="examstatus")
attempt_status_enum = sa.Enum("in_progress", "submitted", "auto_submitted", "terminated", name="attemptstatus")
event_type_enum = sa.Enum(
    "no_face", "multiple_faces", "face_mismatch", "fullscreen_exit", "tab_switch",
    "copy_paste_attempt", "right_click_attempt", "noise_detected", "id_name_mismatch",
    name="eventtype",
)
severity_enum = sa.Enum("low", "medium", "high", name="severity")


def upgrade() -> None:
    bind = op.get_bind()

    op.create_table(
        "roles",
        sa.Column("id", sa.Integer, primary_key=True, index=True),
        sa.Column("name", sa.String(20), unique=True, nullable=False, index=True),
    )

    op.create_table(
        "users",
        sa.Column("id", sa.Integer, primary_key=True, index=True),
        sa.Column("full_name", sa.String(150), nullable=False),
        sa.Column("email", sa.String(150), unique=True, nullable=False, index=True),
        sa.Column("hashed_password", sa.String(255), nullable=False),
        sa.Column("role_id", sa.Integer, sa.ForeignKey("roles.id", ondelete="RESTRICT"), nullable=False, index=True),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "students",
        sa.Column("id", sa.Integer, primary_key=True, index=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False, index=True),
        sa.Column("roll_number", sa.String(50), unique=True, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "examiners",
        sa.Column("id", sa.Integer, primary_key=True, index=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False, index=True),
        sa.Column("department", sa.String(100), nullable=True),
        sa.Column("created_by_admin_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "face_profiles",
        sa.Column("id", sa.Integer, primary_key=True, index=True),
        sa.Column("student_id", sa.Integer, sa.ForeignKey("students.id", ondelete="CASCADE"), unique=True, nullable=False, index=True),
        sa.Column("image_path", sa.String(255), nullable=False),
        sa.Column("encoding", sa.Text, nullable=False),
        sa.Column("registered_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_table(
        "exams",
        sa.Column("id", sa.Integer, primary_key=True, index=True),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("examiner_id", sa.Integer, sa.ForeignKey("examiners.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("duration_minutes", sa.Integer, nullable=False),
        sa.Column("status", exam_status_enum, nullable=False, server_default="draft", index=True),
        sa.Column("randomize_questions", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("proctoring_enabled", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("start_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("end_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "sections",
        sa.Column("id", sa.Integer, primary_key=True, index=True),
        sa.Column("exam_id", sa.Integer, sa.ForeignKey("exams.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("title", sa.String(150), nullable=False),
        sa.Column("order_index", sa.Integer, nullable=False, server_default="0"),
    )

    op.create_table(
        "questions",
        sa.Column("id", sa.Integer, primary_key=True, index=True),
        sa.Column("section_id", sa.Integer, sa.ForeignKey("sections.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("marks", sa.Integer, nullable=False, server_default="1"),
        sa.Column("order_index", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "options",
        sa.Column("id", sa.Integer, primary_key=True, index=True),
        sa.Column("question_id", sa.Integer, sa.ForeignKey("questions.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("text", sa.String(500), nullable=False),
        sa.Column("is_correct", sa.Boolean, nullable=False, server_default=sa.false()),
    )
    op.create_table(
        "student_exam_attempts",
        sa.Column("id", sa.Integer, primary_key=True, index=True),
        sa.Column("student_id", sa.Integer, sa.ForeignKey("students.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("exam_id", sa.Integer, sa.ForeignKey("exams.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("status", attempt_status_enum, nullable=False, server_default="in_progress", index=True),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("question_order", sa.String, nullable=True),
        sa.UniqueConstraint("student_id", "exam_id", name="uq_student_exam"),
    )

    op.create_table(
        "student_answers",
        sa.Column("id", sa.Integer, primary_key=True, index=True),
        sa.Column("attempt_id", sa.Integer, sa.ForeignKey("student_exam_attempts.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("question_id", sa.Integer, sa.ForeignKey("questions.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("selected_option_id", sa.Integer, sa.ForeignKey("options.id", ondelete="SET NULL"), nullable=True),
        sa.Column("answered_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("attempt_id", "question_id", name="uq_attempt_question"),
    )

    op.create_table(
        "exam_results",
        sa.Column("id", sa.Integer, primary_key=True, index=True),
        sa.Column("attempt_id", sa.Integer, sa.ForeignKey("student_exam_attempts.id", ondelete="CASCADE"), unique=True, nullable=False, index=True),
        sa.Column("total_marks", sa.Integer, nullable=False),
        sa.Column("scored_marks", sa.Integer, nullable=False),
        sa.Column("percentage", sa.Float, nullable=False),
        sa.Column("correct_count", sa.Integer, nullable=False),
        sa.Column("incorrect_count", sa.Integer, nullable=False),
        sa.Column("unattempted_count", sa.Integer, nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_table(
        "proctor_events",
        sa.Column("id", sa.Integer, primary_key=True, index=True),
        sa.Column("attempt_id", sa.Integer, sa.ForeignKey("student_exam_attempts.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("event_type", event_type_enum, nullable=False, index=True),
        sa.Column("severity", severity_enum, nullable=False, server_default="low", index=True),
        sa.Column("description", sa.String(255), nullable=True),
        sa.Column("screenshot_path", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), index=True),
    )


def downgrade() -> None:
    op.drop_table("proctor_events")
    op.drop_table("exam_results")
    op.drop_table("student_answers")
    op.drop_table("student_exam_attempts")
    op.drop_table("options")
    op.drop_table("questions")
    op.drop_table("sections")
    op.drop_table("exams")
    op.drop_table("face_profiles")
    op.drop_table("examiners")
    op.drop_table("students")
    op.drop_table("users")
    op.drop_table("roles")

    bind = op.get_bind()
    event_type_enum.drop(bind, checkfirst=True)
    severity_enum.drop(bind, checkfirst=True)
    attempt_status_enum.drop(bind, checkfirst=True)
    exam_status_enum.drop(bind, checkfirst=True)


