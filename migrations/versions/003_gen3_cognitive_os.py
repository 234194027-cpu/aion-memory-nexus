"""gen3 cognitive os - add all Gen 3 tables and indexes

Revision ID: 003
Revises: 003_missing_tables
Create Date: 2026-06-30
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision: str = "003"
down_revision: Union[str, None] = "003_missing_tables"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_exists(inspector, table_name: str, column_name: str) -> bool:
    columns = [c["name"] for c in inspector.get_columns(table_name)]
    return column_name in columns


def _index_exists(inspector, index_name: str, table_name: str = None) -> bool:
    if table_name:
        tables = [table_name]
    else:
        tables = inspector.get_table_names()
    for tbl in tables:
        try:
            indexes = inspector.get_indexes(tbl)
            if any(idx["name"] == index_name for idx in indexes):
                return True
        except Exception:
            pass
    return False


def upgrade() -> None:
    conn = op.get_bind()
    inspector = inspect(conn)

    # ---- life_tasks (already exists in 001, add missing columns) ----
    if not _column_exists(inspector, "life_tasks", "assigned_agent_id"):
        op.add_column("life_tasks", sa.Column("assigned_agent_id", sa.String(64), nullable=True))
    if not _column_exists(inspector, "life_tasks", "priority_score"):
        op.add_column("life_tasks", sa.Column("priority_score", sa.Float, nullable=False, server_default="0.5"))
    if not _column_exists(inspector, "life_tasks", "sub_tasks_count"):
        op.add_column("life_tasks", sa.Column("sub_tasks_count", sa.Integer, nullable=False, server_default="0"))

    # ---- weekly_reviews (already exists in 001, add missing columns) ----
    if not _column_exists(inspector, "weekly_reviews", "persona_observations_json"):
        op.add_column("weekly_reviews", sa.Column("persona_observations_json", sa.Text, nullable=True))
    if not _column_exists(inspector, "weekly_reviews", "open_loops_json"):
        op.add_column("weekly_reviews", sa.Column("open_loops_json", sa.Text, nullable=True))
    if not _column_exists(inspector, "weekly_reviews", "risks_to_watch_json"):
        op.add_column("weekly_reviews", sa.Column("risks_to_watch_json", sa.Text, nullable=True))
    if not _column_exists(inspector, "weekly_reviews", "suggested_focus_json"):
        op.add_column("weekly_reviews", sa.Column("suggested_focus_json", sa.Text, nullable=True))

    # ---- persona_snapshots (already exists in 001, add missing columns) ----
    if not _column_exists(inspector, "persona_snapshots", "patterns_json"):
        op.add_column("persona_snapshots", sa.Column("patterns_json", sa.Text, nullable=True))
    if not _column_exists(inspector, "persona_snapshots", "biases_json"):
        op.add_column("persona_snapshots", sa.Column("biases_json", sa.Text, nullable=True))
    if not _column_exists(inspector, "persona_snapshots", "decision_style_json"):
        op.add_column("persona_snapshots", sa.Column("decision_style_json", sa.Text, nullable=True))
    if not _column_exists(inspector, "persona_snapshots", "risk_profile_json"):
        op.add_column("persona_snapshots", sa.Column("risk_profile_json", sa.Text, nullable=True))
    if not _column_exists(inspector, "persona_snapshots", "evolution_json"):
        op.add_column("persona_snapshots", sa.Column("evolution_json", sa.Text, nullable=True))
    if not _column_exists(inspector, "persona_snapshots", "source_decision_ids"):
        op.add_column("persona_snapshots", sa.Column("source_decision_ids", sa.Text, nullable=True))

    # ---- memory_embeddings: update index ----
    if _index_exists(inspector, "ix_memory_embedding_memory_model", "memory_embeddings"):
        op.drop_index("ix_memory_embedding_memory_model", table_name="memory_embeddings")
    if not _index_exists(inspector, "ix_embedding_memory_model", "memory_embeddings"):
        op.create_index("ix_embedding_memory_model", "memory_embeddings", ["memory_id", "embedding_model"])

    # ---- agent_profiles: add unique index ----
    if not _index_exists(inspector, "ix_agent_token_hash", "agent_profiles"):
        op.create_index("ix_agent_token_hash", "agent_profiles", ["token_hash"], unique=True)

    # ---- committed_memories: add content_hash column and index ----
    if not _column_exists(inspector, "committed_memories", "content_hash"):
        op.add_column("committed_memories", sa.Column("content_hash", sa.String, nullable=True))
    if not _index_exists(inspector, "ix_content_hash_unique", "committed_memories"):
        op.create_index("ix_content_hash_unique", "committed_memories", ["content_hash"], unique=True)


def downgrade() -> None:
    # ---- committed_memories: remove content_hash ----
    op.drop_index("ix_content_hash_unique", table_name="committed_memories")
    op.drop_column("committed_memories", "content_hash")

    # ---- agent_profiles: remove unique index ----
    op.drop_index("ix_agent_token_hash", table_name="agent_profiles")

    # ---- memory_embeddings: restore old index ----
    op.drop_index("ix_embedding_memory_model", table_name="memory_embeddings")
    op.create_index("ix_memory_embedding_memory_model", "memory_embeddings", ["memory_id", "embedding_model"])

    # ---- persona_snapshots: remove columns ----
    op.drop_column("persona_snapshots", "source_decision_ids")
    op.drop_column("persona_snapshots", "evolution_json")
    op.drop_column("persona_snapshots", "risk_profile_json")
    op.drop_column("persona_snapshots", "decision_style_json")
    op.drop_column("persona_snapshots", "biases_json")
    op.drop_column("persona_snapshots", "patterns_json")

    # ---- weekly_reviews: remove columns ----
    op.drop_column("weekly_reviews", "suggested_focus_json")
    op.drop_column("weekly_reviews", "risks_to_watch_json")
    op.drop_column("weekly_reviews", "open_loops_json")
    op.drop_column("weekly_reviews", "persona_observations_json")

    # ---- life_tasks: remove columns ----
    op.drop_column("life_tasks", "sub_tasks_count")
    op.drop_column("life_tasks", "priority_score")
    op.drop_column("life_tasks", "assigned_agent_id")
