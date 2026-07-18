"""Add append-only memory lifecycle transition audit records.

Revision ID: 015
Revises: 014
"""

from alembic import op
import sqlalchemy as sa


revision = "015"
down_revision = "014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    if "memory_state_transitions" not in tables:
        op.create_table(
            "memory_state_transitions",
            sa.Column("id", sa.String(length=64), nullable=False),
            sa.Column("user_id", sa.String(length=64), nullable=False),
            sa.Column("subject_type", sa.String(length=32), nullable=False),
            sa.Column("subject_id", sa.String(length=64), nullable=False),
            sa.Column("from_state", sa.String(length=48), nullable=True),
            sa.Column("to_state", sa.String(length=48), nullable=False),
            sa.Column("actor_type", sa.String(length=32), nullable=False),
            sa.Column("actor_id", sa.String(length=64), nullable=True),
            sa.Column("reason", sa.String(length=128), nullable=True),
            sa.Column("evidence_refs", sa.JSON(), nullable=False, server_default="[]"),
            sa.Column("policy_version", sa.String(length=32), nullable=False, server_default="memory-governance-v1"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_memory_state_transitions_user_created", "memory_state_transitions", ["user_id", "created_at"])
        op.create_index("ix_memory_state_transitions_subject", "memory_state_transitions", ["subject_type", "subject_id"])


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "memory_state_transitions" in set(inspector.get_table_names()):
        op.drop_index("ix_memory_state_transitions_subject", table_name="memory_state_transitions")
        op.drop_index("ix_memory_state_transitions_user_created", table_name="memory_state_transitions")
        op.drop_table("memory_state_transitions")
