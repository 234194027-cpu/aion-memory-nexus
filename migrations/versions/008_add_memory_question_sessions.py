"""Add memory question sessions

Revision ID: 008
Revises: 007
Create Date: 2026-07-04
"""
from alembic import op
import sqlalchemy as sa


revision = "008"
down_revision = "007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if "memory_question_sessions" in inspector.get_table_names():
        return

    op.create_table(
        "memory_question_sessions",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("wecom_contact_id", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="active"),
        sa.Column("questions", sa.JSON(), nullable=False),
        sa.Column("answers", sa.JSON(), nullable=False),
        sa.Column("current_index", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("source_summary", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_memory_question_sessions_id", "memory_question_sessions", ["id"])
    op.create_index("ix_memory_question_sessions_user_id", "memory_question_sessions", ["user_id"])
    op.create_index("ix_memory_question_sessions_wecom_contact_id", "memory_question_sessions", ["wecom_contact_id"])
    op.create_index("ix_memory_question_sessions_status", "memory_question_sessions", ["status"])
    op.create_index("ix_question_sessions_user_status", "memory_question_sessions", ["user_id", "status"])
    op.create_index("ix_question_sessions_contact_status", "memory_question_sessions", ["wecom_contact_id", "status"])


def downgrade() -> None:
    op.drop_index("ix_question_sessions_contact_status", table_name="memory_question_sessions")
    op.drop_index("ix_question_sessions_user_status", table_name="memory_question_sessions")
    op.drop_index("ix_memory_question_sessions_status", table_name="memory_question_sessions")
    op.drop_index("ix_memory_question_sessions_wecom_contact_id", table_name="memory_question_sessions")
    op.drop_index("ix_memory_question_sessions_user_id", table_name="memory_question_sessions")
    op.drop_index("ix_memory_question_sessions_id", table_name="memory_question_sessions")
    op.drop_table("memory_question_sessions")
