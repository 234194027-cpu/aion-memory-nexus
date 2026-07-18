"""Add durable evidence handoffs between V2 agent roles.

Revision ID: 019
Revises: 018
"""
from alembic import op
import sqlalchemy as sa

revision = "019"
down_revision = "018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_handoffs",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("source_run_id", sa.String(length=64), sa.ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_event_id", sa.String(length=64), nullable=True),
        sa.Column("handoff_type", sa.String(length=48), nullable=False),
        sa.Column("mode", sa.String(length=16), nullable=False, server_default="shadow"),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("evidence_payload", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="active"),
        sa.Column("resolved_by_event_id", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_agent_handoffs_user_id", "agent_handoffs", ["user_id"])
    op.create_index("ix_agent_handoffs_source_run_id", "agent_handoffs", ["source_run_id"])
    op.create_index("ix_agent_handoffs_source_event_id", "agent_handoffs", ["source_event_id"])
    op.create_index("ix_agent_handoffs_user_mode_status", "agent_handoffs", ["user_id", "mode", "status"])
    op.create_index("ix_agent_handoffs_event_type", "agent_handoffs", ["source_event_id", "handoff_type"])


def downgrade() -> None:
    op.drop_index("ix_agent_handoffs_event_type", table_name="agent_handoffs")
    op.drop_index("ix_agent_handoffs_user_mode_status", table_name="agent_handoffs")
    op.drop_index("ix_agent_handoffs_source_event_id", table_name="agent_handoffs")
    op.drop_index("ix_agent_handoffs_source_run_id", table_name="agent_handoffs")
    op.drop_index("ix_agent_handoffs_user_id", table_name="agent_handoffs")
    op.drop_table("agent_handoffs")
