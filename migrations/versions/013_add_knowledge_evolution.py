"""Add Wiki evolution snapshots and relation validity metadata.

Revision ID: 013
Revises: 012
"""

from alembic import op
import sqlalchemy as sa


revision = "013"
down_revision = "012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    relation_columns = {column["name"] for column in inspector.get_columns("memory_relations")}
    if "valid_from" not in relation_columns:
        op.add_column("memory_relations", sa.Column("valid_from", sa.DateTime(timezone=True), nullable=True))
    if "valid_until" not in relation_columns:
        op.add_column("memory_relations", sa.Column("valid_until", sa.DateTime(timezone=True), nullable=True))
    if "knowledge_page_versions" not in tables:
        op.create_table(
            "knowledge_page_versions",
            sa.Column("id", sa.String(length=64), nullable=False),
            sa.Column("user_id", sa.String(length=64), nullable=False),
            sa.Column("page_id", sa.String(length=64), nullable=False),
            sa.Column("slug", sa.String(length=160), nullable=False),
            sa.Column("title", sa.String(length=255), nullable=False),
            sa.Column("summary", sa.Text(), nullable=False, server_default=""),
            sa.Column("confidence", sa.Float(), nullable=False, server_default="0"),
            sa.Column("source_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("memory_ids", sa.String(), nullable=False, server_default="[]"),
            sa.Column("change_reason", sa.String(length=64), nullable=False),
            sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_knowledge_page_versions_user_page_created", "knowledge_page_versions", ["user_id", "page_id", "created_at"])
        op.create_index("ix_knowledge_page_versions_user_slug", "knowledge_page_versions", ["user_id", "slug"])


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    if "knowledge_page_versions" in tables:
        op.drop_index("ix_knowledge_page_versions_user_slug", table_name="knowledge_page_versions")
        op.drop_index("ix_knowledge_page_versions_user_page_created", table_name="knowledge_page_versions")
        op.drop_table("knowledge_page_versions")
    relation_columns = {column["name"] for column in inspector.get_columns("memory_relations")}
    if "valid_until" in relation_columns:
        op.drop_column("memory_relations", "valid_until")
    if "valid_from" in relation_columns:
        op.drop_column("memory_relations", "valid_from")
