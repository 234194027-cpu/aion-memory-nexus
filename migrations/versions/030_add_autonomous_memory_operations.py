"""Add autonomous Working-Agent maintenance and evidence compaction records.

Revision ID: 030
Revises: 029
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "030"
down_revision = "029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    dialect = op.get_bind().dialect.name
    op.create_table(
        "memory_maintenance_runs",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("user_id", sa.String(length=64), nullable=True),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("state", sa.String(length=24), nullable=False, server_default="running"),
        sa.Column("idempotency_key", sa.String(length=96), nullable=False, unique=True),
        sa.Column("cursor", sa.JSON(), nullable=False),
        sa.Column("counters", sa.JSON(), nullable=False),
        sa.Column("token_budget", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("token_used", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error", sa.String(length=256), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_maintenance_run_kind_state", "memory_maintenance_runs", ["kind", "state", "started_at"])
    op.create_table(
        "evidence_seals",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("source_event_id", sa.String(length=64), nullable=False),
        sa.Column("content_hash", sa.String(length=128), nullable=False),
        sa.Column("excerpt", sa.Text(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sensitivity", sa.String(length=16), nullable=False, server_default="normal"),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("sealed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "source_event_id", name="uq_evidence_seal_user_event"),
    )
    op.create_index("ix_evidence_seals_user_id", "evidence_seals", ["user_id"])
    op.create_index("ix_evidence_seals_source_event_id", "evidence_seals", ["source_event_id"])
    op.create_table(
        "user_memory_briefs",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("user_id", sa.String(length=64), nullable=False, unique=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("memory_ids", sa.JSON(), nullable=False),
        sa.Column("source_revision", sa.String(length=64), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("token_estimate", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index("ix_user_memory_briefs_source_revision", "user_memory_briefs", ["source_revision"])
    op.create_table(
        "memory_maintenance_actions",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("run_id", sa.String(length=64), sa.ForeignKey("memory_maintenance_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("case_id", sa.String(length=64), sa.ForeignKey("memory_work_cases.id", ondelete="SET NULL"), nullable=True),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("state", sa.String(length=24), nullable=False, server_default="completed"),
        sa.Column("input_memory_ids", sa.JSON(), nullable=False),
        sa.Column("input_event_ids", sa.JSON(), nullable=False),
        sa.Column("output_memory_id", sa.String(length=64), nullable=True),
        sa.Column("evidence_seal_id", sa.String(length=64), sa.ForeignKey("evidence_seals.id", ondelete="SET NULL"), nullable=True),
        sa.Column("reason_code", sa.String(length=96), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=96), nullable=False, unique=True),
        sa.Column("reversible_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    for name, columns in (
        ("ix_maintenance_action_run_id", ["run_id"]),
        ("ix_maintenance_action_user_id", ["user_id"]),
        ("ix_maintenance_action_case_id", ["case_id"]),
        ("ix_maintenance_action_output_memory_id", ["output_memory_id"]),
        ("ix_maintenance_action_evidence_seal_id", ["evidence_seal_id"]),
    ):
        op.create_index(name, "memory_maintenance_actions", columns)

    op.add_column("raw_events", sa.Column("retention_state", sa.String(length=24), nullable=False, server_default="active"))
    op.add_column("raw_events", sa.Column("purge_after", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_raw_events_retention_state", "raw_events", ["retention_state"])
    op.create_index("ix_raw_events_purge_after", "raw_events", ["purge_after"])

    op.add_column("memory_sources", sa.Column("evidence_seal_id", sa.String(length=64), nullable=True))
    op.add_column("memory_work_evidence", sa.Column("evidence_seal_id", sa.String(length=64), nullable=True))
    # SQLite cannot add foreign-key constraints to existing tables.  The
    # production PostgreSQL migration adds the constraints; SQLite remains a
    # supported migration-test dialect with the same nullable columns.
    if dialect != "sqlite":
        op.create_foreign_key("fk_memory_sources_evidence_seal", "memory_sources", "evidence_seals", ["evidence_seal_id"], ["id"], ondelete="SET NULL")
        op.create_foreign_key("fk_memory_work_evidence_seal", "memory_work_evidence", "evidence_seals", ["evidence_seal_id"], ["id"], ondelete="SET NULL")
        op.alter_column("memory_sources", "raw_event_id", existing_type=sa.String(), nullable=True)
        op.alter_column("memory_work_evidence", "raw_event_id", existing_type=sa.String(length=64), nullable=True)


def downgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect != "sqlite":
        op.drop_constraint("fk_memory_work_evidence_seal", "memory_work_evidence", type_="foreignkey")
    op.drop_column("memory_work_evidence", "evidence_seal_id")
    if dialect != "sqlite":
        op.drop_constraint("fk_memory_sources_evidence_seal", "memory_sources", type_="foreignkey")
    op.drop_column("memory_sources", "evidence_seal_id")
    op.drop_index("ix_raw_events_purge_after", table_name="raw_events")
    op.drop_index("ix_raw_events_retention_state", table_name="raw_events")
    op.drop_column("raw_events", "purge_after")
    op.drop_column("raw_events", "retention_state")
    op.drop_table("memory_maintenance_actions")
    op.drop_table("user_memory_briefs")
    op.drop_table("evidence_seals")
    op.drop_table("memory_maintenance_runs")
