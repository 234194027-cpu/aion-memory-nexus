"""gen3 missing tables - obsidian_sync_records, custom_llm_providers, persona columns, weekly_review columns, life_task columns

Revision ID: 003_missing_tables
Revises: 002
Create Date: 2026-06-30
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = "003_missing_tables"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ---- obsidian_sync_records ----
    op.create_table(
        "obsidian_sync_records",
        sa.Column("id", sa.String, primary_key=True, index=True),
        sa.Column("user_id", sa.String, nullable=False, default="default", index=True),
        sa.Column("memory_id", sa.String, nullable=False, index=True),
        sa.Column("vault_path", sa.String, nullable=True),
        sa.Column("file_path", sa.String, nullable=True),
        sa.Column("content_hash", sa.String, nullable=True),
        sa.Column("sync_status", sa.String(20), nullable=False, default="pending"),
        sa.Column("last_exported_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_imported_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_obsidian_sync_user_memory", "obsidian_sync_records", ["user_id", "memory_id"])

    # ---- custom_llm_providers ----
    op.create_table(
        "custom_llm_providers",
        sa.Column("id", sa.String, primary_key=True, index=True),
        sa.Column("user_id", sa.String, nullable=False, default="default", index=True),
        sa.Column("provider_name", sa.String, nullable=False),
        sa.Column("provider_key", sa.String, nullable=False),
        sa.Column("base_url", sa.String, nullable=False),
        sa.Column("api_key", sa.String, nullable=True),
        sa.Column("model_name", sa.String, nullable=True),
        sa.Column("api_format", sa.String, nullable=False, default="openai"),
        sa.Column("headers", sa.JSON, default={}),
        sa.Column("is_preset", sa.Boolean, default=False),
        sa.Column("icon", sa.String, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
        sa.Column("status", sa.Boolean, default=True),
        sa.UniqueConstraint(
            "user_id",
            "provider_name",
            name="uq_custom_provider_user_name",
        ),
        sa.UniqueConstraint(
            "user_id",
            "provider_key",
            name="uq_custom_provider_user_key",
        ),
    )

    # ---- persona_snapshots: add missing columns ----
    op.add_column("persona_snapshots", sa.Column("patterns_json", sa.Text, nullable=True))
    op.add_column("persona_snapshots", sa.Column("biases_json", sa.Text, nullable=True))
    op.add_column("persona_snapshots", sa.Column("decision_style_json", sa.Text, nullable=True))
    op.add_column("persona_snapshots", sa.Column("risk_profile_json", sa.Text, nullable=True))
    op.add_column("persona_snapshots", sa.Column("evolution_json", sa.Text, nullable=True))
    op.add_column("persona_snapshots", sa.Column("source_decision_ids", sa.Text, nullable=True))

    # ---- weekly_reviews: add missing columns ----
    op.add_column("weekly_reviews", sa.Column("persona_observations_json", sa.Text, nullable=True))
    op.add_column("weekly_reviews", sa.Column("open_loops_json", sa.Text, nullable=True))
    op.add_column("weekly_reviews", sa.Column("risks_to_watch_json", sa.Text, nullable=True))
    op.add_column("weekly_reviews", sa.Column("suggested_focus_json", sa.Text, nullable=True))

    # ---- life_tasks: add missing columns ----
    op.add_column("life_tasks", sa.Column("assigned_agent_id", sa.String, nullable=True))
    op.add_column("life_tasks", sa.Column("priority_score", sa.Float, nullable=False, server_default="0.5"))
    op.add_column("life_tasks", sa.Column("sub_tasks_count", sa.Integer, nullable=False, server_default="0"))

    # ---- agent_profiles: add user_id index if not exists ----
    # (already has user_id column from 001, just ensure index)

    # ---- committed_memories: add missing project/repo/workspace columns ----
    # (already added in 001, these were in the original create_table)


def downgrade() -> None:
    # ---- life_tasks: remove columns ----
    op.drop_column("life_tasks", "sub_tasks_count")
    op.drop_column("life_tasks", "priority_score")
    op.drop_column("life_tasks", "assigned_agent_id")

    # ---- weekly_reviews: remove columns ----
    op.drop_column("weekly_reviews", "suggested_focus_json")
    op.drop_column("weekly_reviews", "risks_to_watch_json")
    op.drop_column("weekly_reviews", "open_loops_json")
    op.drop_column("weekly_reviews", "persona_observations_json")

    # ---- persona_snapshots: remove columns ----
    op.drop_column("persona_snapshots", "source_decision_ids")
    op.drop_column("persona_snapshots", "evolution_json")
    op.drop_column("persona_snapshots", "risk_profile_json")
    op.drop_column("persona_snapshots", "decision_style_json")
    op.drop_column("persona_snapshots", "biases_json")
    op.drop_column("persona_snapshots", "patterns_json")

    # ---- drop tables ----
    op.drop_table("custom_llm_providers")
    op.drop_table("obsidian_sync_records")
