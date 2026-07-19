"""Add V2.5.1 maintenance safety controls and rollback audit fields.

Revision ID: 031
Revises: 030
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "031"
down_revision = "030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "memory_maintenance_controls",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("user_id", sa.String(length=64), nullable=False, unique=True),
        sa.Column("state", sa.String(length=32), nullable=False, server_default="active"),
        sa.Column("pause_reason", sa.String(length=256), nullable=True),
        sa.Column("last_error_code", sa.String(length=128), nullable=True),
        sa.Column("integrity_fault", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("shadow_passes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("transition_metadata", sa.JSON(), nullable=False),
        sa.Column("paused_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index(
        "ix_memory_maintenance_controls_user_id",
        "memory_maintenance_controls",
        ["user_id"],
        unique=True,
    )
    op.create_index(
        "ix_memory_maintenance_control_state",
        "memory_maintenance_controls",
        ["state", "updated_at"],
    )
    op.add_column("memory_maintenance_actions", sa.Column("rolled_back_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("memory_maintenance_actions", sa.Column("rollback_action_id", sa.String(length=64), nullable=True))
    op.create_index(
        "ix_memory_maintenance_actions_rollback_action_id",
        "memory_maintenance_actions",
        ["rollback_action_id"],
    )
    op.create_index(
        "ix_committed_memory_maintenance_block",
        "committed_memories",
        ["user_id", "status", "memory_type", "sensitivity", "visibility_scope", "updated_at"],
    )
    op.create_table(
        "graph_shadow_observations",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("query_hash", sa.String(length=64), nullable=False),
        sa.Column("baseline_memory_ids", sa.JSON(), nullable=False),
        sa.Column("graph_memory_ids", sa.JSON(), nullable=False),
        sa.Column("graph_relation_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("novel_verified_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("source_coverage", sa.Float(), nullable=False, server_default="0"),
        sa.Column("graph_latency_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("token_used", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("mode", sa.String(length=16), nullable=False, server_default="shadow"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_graph_shadow_observations_user_id", "graph_shadow_observations", ["user_id"])
    op.create_index("ix_graph_shadow_observations_query_hash", "graph_shadow_observations", ["query_hash"])
    op.create_index("ix_graph_shadow_user_created", "graph_shadow_observations", ["user_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_graph_shadow_user_created", table_name="graph_shadow_observations")
    op.drop_index("ix_graph_shadow_observations_query_hash", table_name="graph_shadow_observations")
    op.drop_index("ix_graph_shadow_observations_user_id", table_name="graph_shadow_observations")
    op.drop_table("graph_shadow_observations")
    op.drop_index("ix_committed_memory_maintenance_block", table_name="committed_memories")
    op.drop_index("ix_memory_maintenance_actions_rollback_action_id", table_name="memory_maintenance_actions")
    op.drop_column("memory_maintenance_actions", "rollback_action_id")
    op.drop_column("memory_maintenance_actions", "rolled_back_at")
    op.drop_index("ix_memory_maintenance_control_state", table_name="memory_maintenance_controls")
    op.drop_index("ix_memory_maintenance_controls_user_id", table_name="memory_maintenance_controls")
    op.drop_table("memory_maintenance_controls")
