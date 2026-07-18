"""Add source-backed knowledge workspace tables.

Revision ID: 010
Revises: 009
"""

from alembic import op
import sqlalchemy as sa


revision = "010"
down_revision = "009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    if "knowledge_pages" not in tables:
        op.create_table(
            "knowledge_pages",
            sa.Column("id", sa.String(length=64), nullable=False),
            sa.Column("user_id", sa.String(length=64), nullable=False),
            sa.Column("slug", sa.String(length=160), nullable=False),
            sa.Column("title", sa.String(length=255), nullable=False),
            sa.Column("summary", sa.Text(), nullable=False, server_default=""),
            sa.Column("confidence", sa.Float(), nullable=False, server_default="0"),
            sa.Column("source_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="active"),
            sa.Column("generated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("user_id", "slug", name="uq_knowledge_pages_user_slug"),
        )
        op.create_index("ix_knowledge_pages_user_status", "knowledge_pages", ["user_id", "status"])
    if "knowledge_page_memories" not in tables:
        op.create_table(
            "knowledge_page_memories",
            sa.Column("id", sa.String(length=64), nullable=False),
            sa.Column("user_id", sa.String(length=64), nullable=False),
            sa.Column("page_id", sa.String(length=64), nullable=False),
            sa.Column("memory_id", sa.String(length=64), nullable=False),
            sa.Column("relation_basis", sa.String(length=32), nullable=False),
            sa.Column("confidence", sa.Float(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.ForeignKeyConstraint(["page_id"], ["knowledge_pages.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["memory_id"], ["committed_memories.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("page_id", "memory_id", name="uq_knowledge_page_memories_page_memory"),
        )
        op.create_index("ix_knowledge_page_memories_user_memory", "knowledge_page_memories", ["user_id", "memory_id"])


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    if "knowledge_page_memories" in tables:
        op.drop_index("ix_knowledge_page_memories_user_memory", table_name="knowledge_page_memories")
        op.drop_table("knowledge_page_memories")
    if "knowledge_pages" in tables:
        op.drop_index("ix_knowledge_pages_user_status", table_name="knowledge_pages")
        op.drop_table("knowledge_pages")
