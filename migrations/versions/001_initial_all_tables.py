"""initial all tables

Revision ID: 001
Revises: None
Create Date: 2026-06-29
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ---- users ----
    op.create_table(
        "users",
        sa.Column("id", sa.String, primary_key=True, index=True),
        sa.Column("email", sa.String, unique=True, index=True),
        sa.Column("hashed_password", sa.String),
        sa.Column("is_active", sa.Boolean, default=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
    )

    # ---- raw_events ----
    op.create_table(
        "raw_events",
        sa.Column("id", sa.String, primary_key=True, index=True),
        sa.Column("source_type", sa.String(20), nullable=False),
        sa.Column("source_id", sa.String, nullable=True),
        sa.Column("agent_id", sa.String, nullable=True),
        sa.Column("user_id", sa.String, nullable=False),
        sa.Column("project_id", sa.String, nullable=True),
        sa.Column("repo_id", sa.String, nullable=True),
        sa.Column("workspace_id", sa.String, nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ingested_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("content_hash", sa.String, nullable=False),
        sa.Column("event_metadata", sa.JSON, default=dict),
        sa.Column("sensitivity", sa.String(20), default="normal"),
        sa.Column("visibility_scope", sa.String(20), default="project"),
        sa.Column("processing_status", sa.String(20), default="queued"),
    )

    # ---- candidate_memories ----
    op.create_table(
        "candidate_memories",
        sa.Column("id", sa.String, primary_key=True, index=True),
        sa.Column("user_id", sa.String, nullable=False),
        sa.Column("raw_event_ids", sa.JSON, nullable=False),
        sa.Column("memory_type", sa.String(30), nullable=False),
        sa.Column("title", sa.String, nullable=False),
        sa.Column("body", sa.Text, nullable=False),
        sa.Column("importance_score", sa.Float, default=0.0),
        sa.Column("confidence_score", sa.Float, default=0.0),
        sa.Column("sensitivity", sa.String(20), default="normal"),
        sa.Column("proposed_action", sa.String, nullable=True),
        sa.Column("status", sa.String(30), default="pending"),
        sa.Column("rationale", sa.Text, nullable=True),
        sa.Column("conflict_memory_ids", sa.JSON, default=[]),
        sa.Column("tags", sa.JSON),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewed_by", sa.String, nullable=True),
    )

    # ---- committed_memories ----
    op.create_table(
        "committed_memories",
        sa.Column("id", sa.String, primary_key=True, index=True),
        sa.Column("candidate_id", sa.String, sa.ForeignKey("candidate_memories.id"), nullable=True),
        sa.Column("user_id", sa.String, nullable=False),
        sa.Column("project_id", sa.String, nullable=True, index=True),
        sa.Column("repo_id", sa.String, nullable=True, index=True),
        sa.Column("workspace_id", sa.String, nullable=True),
        sa.Column("memory_type", sa.String(30), nullable=False),
        sa.Column("title", sa.String, nullable=False),
        sa.Column("body", sa.Text, nullable=False),
        sa.Column("confidence", sa.Float, default=0.0),
        sa.Column("importance", sa.Float, default=0.0),
        sa.Column("sensitivity", sa.String(20), default="normal"),
        sa.Column("visibility_scope", sa.String(20), default="project"),
        sa.Column("status", sa.String(20), default="active"),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("tags", sa.JSON),
        sa.Column("embedding", sa.JSON, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
        sa.Column("last_accessed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_user_status_type", "committed_memories", ["user_id", "status", "memory_type"])
    op.create_index("ix_user_importance", "committed_memories", ["user_id", "importance"])
    op.create_index("ix_user_valid_from", "committed_memories", ["user_id", "valid_from"])

    # ---- memory_sources ----
    op.create_table(
        "memory_sources",
        sa.Column("id", sa.String, primary_key=True, index=True),
        sa.Column("memory_id", sa.String, nullable=False),
        sa.Column("raw_event_id", sa.String, sa.ForeignKey("raw_events.id"), nullable=False),
        sa.Column("quote", sa.Text, nullable=True),
        sa.Column("location", sa.String, nullable=True),
        sa.Column("source_type", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # ---- memory_embeddings ----
    op.create_table(
        "memory_embeddings",
        sa.Column("id", sa.String, primary_key=True, index=True),
        sa.Column("memory_id", sa.String, sa.ForeignKey("committed_memories.id"), nullable=False, index=True),
        sa.Column("embedding_model", sa.String, nullable=False, default="default"),
        sa.Column("embedding_vector", sa.JSON, nullable=False),
        sa.Column("content_snapshot", sa.Text, nullable=False),
        sa.Column("dimension", sa.Integer, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("memory_id", "embedding_model", name="uq_memory_model"),
    )
    op.create_index("ix_memory_embedding_memory_model", "memory_embeddings", ["memory_id", "embedding_model"])

    # ---- agent_profiles ----
    op.create_table(
        "agent_profiles",
        sa.Column("id", sa.String, primary_key=True, index=True),
        sa.Column("user_id", sa.String, nullable=False, default="default", index=True),
        sa.Column("agent_name", sa.String, nullable=False),
        sa.Column("agent_type", sa.String(20), nullable=False),
        sa.Column("allowed_write_scopes", sa.JSON, default=[]),
        sa.Column("allowed_read_scopes", sa.JSON, default=[]),
        sa.Column("default_recall_level", sa.String(20), default="task_only"),
        sa.Column("token_hash", sa.String, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.Boolean, default=True),
        sa.Column("llm_provider", sa.String(20), nullable=True),
        sa.Column("llm_model", sa.String, nullable=True),
        sa.Column("llm_api_key", sa.String, nullable=True),
        sa.Column("llm_api_base", sa.String, nullable=True),
        sa.Column("llm_temperature", sa.Float, default=0.7),
        sa.Column("llm_max_tokens", sa.Integer, default=4096),
        sa.Column("custom_provider_key", sa.String, nullable=True),
        sa.Column("mission", sa.String, nullable=True),
        sa.Column("role", sa.String, nullable=True),
        sa.Column("goals", sa.JSON, default=[]),
        sa.Column("constraints", sa.JSON, default=[]),
        sa.Column("instructions", sa.String, nullable=True),
        sa.Column("schedule_enabled", sa.Boolean, default=True),
        sa.Column("event_extraction_interval", sa.Integer, default=5),
        sa.Column("memory_organize_hour", sa.Integer, default=2),
        sa.Column("weekly_summary_day", sa.Integer, default=0),
        sa.Column("obsidian_sync_interval", sa.Integer, default=60),
    )

    # ---- agent_permissions ----
    op.create_table(
        "agent_permissions",
        sa.Column("id", sa.String(64), primary_key=True, index=True),
        sa.Column("user_id", sa.String(64), sa.ForeignKey("users.id"), nullable=False, index=True),
        sa.Column("agent_id", sa.String(64), nullable=False, index=True),
        sa.Column("tool_name", sa.String(64), nullable=False, index=True),
        sa.Column("scope", sa.String(20), nullable=False, default="allow"),
        sa.Column("created_at", sa.DateTime, nullable=False),
    )
    op.create_index("uq_agent_permissions_agent_tool", "agent_permissions", ["agent_id", "tool_name"], unique=True)
    op.create_index("ix_agent_permissions_user_agent", "agent_permissions", ["user_id", "agent_id"])

    # ---- persona_snapshots ----
    op.create_table(
        "persona_snapshots",
        sa.Column("id", sa.String(64), primary_key=True, index=True),
        sa.Column("user_id", sa.String(64), sa.ForeignKey("users.id"), nullable=False, index=True),
        sa.Column("snapshot_date", sa.String(10), nullable=False, index=True),
        sa.Column("mode", sa.String(20), nullable=False, default="full"),
        sa.Column("traits_json", sa.Text, nullable=False, default="[]"),
        sa.Column("summary", sa.Text, nullable=False, default=""),
        sa.Column("evidence_memory_ids", sa.Text, nullable=False, default="[]"),
        sa.Column("embed_method", sa.String(20), nullable=True),
        sa.Column("created_at", sa.DateTime),
    )

    # ---- decision_records ----
    op.create_table(
        "decision_records",
        sa.Column("id", sa.String(64), primary_key=True, index=True),
        sa.Column("user_id", sa.String(64), sa.ForeignKey("users.id"), nullable=False, index=True),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("context", sa.Text, nullable=False),
        sa.Column("decision", sa.Text, nullable=False),
        sa.Column("rationale", sa.Text, nullable=False),
        sa.Column("expected_outcome", sa.Text, nullable=True),
        sa.Column("actual_outcome", sa.Text, nullable=True),
        sa.Column("status", sa.String(20), nullable=False, default="open"),
        sa.Column("linked_memory_id", sa.String(64), nullable=True),
        sa.Column("project_id", sa.String(128), nullable=True, index=True),
        sa.Column("decided_at", sa.DateTime, nullable=False),
        sa.Column("resolved_at", sa.DateTime, nullable=True),
        sa.Column("review_count", sa.Integer, nullable=False, default=0),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("updated_at", sa.DateTime, nullable=False),
    )
    op.create_index("ix_decision_user_status", "decision_records", ["user_id", "status"])
    op.create_index("ix_decision_user_project", "decision_records", ["user_id", "project_id"])
    op.create_index("ix_decision_user_decided_at", "decision_records", ["user_id", "decided_at"])

    # ---- weekly_reviews ----
    op.create_table(
        "weekly_reviews",
        sa.Column("id", sa.String(64), primary_key=True, index=True),
        sa.Column("user_id", sa.String(64), sa.ForeignKey("users.id"), nullable=False, index=True),
        sa.Column("week_start", sa.String(10), nullable=False, index=True),
        sa.Column("week_end", sa.String(10), nullable=False),
        sa.Column("new_memories_json", sa.Text, nullable=False, default="[]"),
        sa.Column("decisions_json", sa.Text, nullable=False, default="[]"),
        sa.Column("highlights_json", sa.Text, nullable=False, default="[]"),
        sa.Column("open_questions_json", sa.Text, nullable=False, default="[]"),
        sa.Column("summary", sa.Text, nullable=False, default=""),
        sa.Column("word_count", sa.Integer, nullable=False, default=0),
        sa.Column("created_at", sa.DateTime, nullable=False),
    )
    op.create_index("ix_weekly_user_week", "weekly_reviews", ["user_id", "week_start"])

    # ---- life_tasks ----
    op.create_table(
        "life_tasks",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.String(64), sa.ForeignKey("users.id"), index=True),
        sa.Column("title", sa.String(255)),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("status", sa.String(20)),
        sa.Column("priority", sa.String(10)),
        sa.Column("project_id", sa.String(128), nullable=True),
        sa.Column("parent_task_id", sa.String(64), nullable=True),
        sa.Column("linked_memory_ids", sa.Text, nullable=True),
        sa.Column("linked_decision_ids", sa.Text, nullable=True),
        sa.Column("due_at", sa.DateTime, nullable=True),
        sa.Column("started_at", sa.DateTime, nullable=True),
        sa.Column("completed_at", sa.DateTime, nullable=True),
        sa.Column("created_at", sa.DateTime),
        sa.Column("updated_at", sa.DateTime),
    )

    # ---- life_timeline_entries ----
    op.create_table(
        "life_timeline_entries",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.String(64), sa.ForeignKey("users.id"), index=True),
        sa.Column("entry_date", sa.String(10), index=True),
        sa.Column("entry_kind", sa.String(20)),
        sa.Column("ref_id", sa.String(64)),
        sa.Column("title", sa.String(255)),
        sa.Column("snippet", sa.Text, nullable=True),
        sa.Column("importance", sa.Float, default=0.5),
        sa.Column("project_id", sa.String(128), nullable=True),
        sa.Column("created_at", sa.DateTime),
    )

    # ---- simulation_runs ----
    op.create_table(
        "simulation_runs",
        sa.Column("id", sa.String(64), primary_key=True, index=True),
        sa.Column("user_id", sa.String(64), sa.ForeignKey("users.id"), nullable=False, index=True),
        sa.Column("question", sa.Text, nullable=False),
        sa.Column("baseline_summary", sa.Text, nullable=True),
        sa.Column("counterfactual", sa.Text, nullable=True),
        sa.Column("outcome", sa.Text, nullable=True),
        sa.Column("confidence", sa.Float, nullable=False, default=0.4),
        sa.Column("linked_memory_ids", sa.Text, nullable=True),
        sa.Column("horizon_days", sa.String(20), nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False),
    )
    op.create_index("ix_simulation_runs_user_created", "simulation_runs", ["user_id", "created_at"])

    # ---- audit_logs ----
    op.create_table(
        "audit_logs",
        sa.Column("id", sa.String(64), primary_key=True, index=True),
        sa.Column("user_id", sa.String(64), nullable=False, index=True),
        sa.Column("actor_type", sa.String(20), nullable=False, default="user"),
        sa.Column("actor_id", sa.String(64), nullable=True),
        sa.Column("action", sa.String(64), nullable=False, index=True),
        sa.Column("target_type", sa.String(32), nullable=True),
        sa.Column("target_id", sa.String(64), nullable=True, index=True),
        sa.Column("detail", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False),
    )


def downgrade() -> None:
    op.drop_table("audit_logs")
    op.drop_table("simulation_runs")
    op.drop_table("life_timeline_entries")
    op.drop_table("life_tasks")
    op.drop_table("weekly_reviews")
    op.drop_table("decision_records")
    op.drop_table("persona_snapshots")
    op.drop_table("agent_permissions")
    op.drop_table("agent_profiles")
    op.drop_table("memory_embeddings")
    op.drop_table("memory_sources")
    op.drop_table("committed_memories")
    op.drop_table("candidate_memories")
    op.drop_table("raw_events")
    op.drop_table("users")
