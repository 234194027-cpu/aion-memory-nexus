"""Add resumable V3 historical graph replay checkpoints.

Revision ID: 029
Revises: 028
"""

from alembic import op
import sqlalchemy as sa


revision = "029"
down_revision = "028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "graph_replay_checkpoints",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("source_kind", sa.String(length=32), nullable=False),
        sa.Column("cursor_occurred_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cursor_source_id", sa.String(length=64), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("scanned_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("queued_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "source_kind", name="uq_graph_replay_checkpoint_user_source"),
    )
    op.create_index(
        "ix_graph_replay_checkpoint_user", "graph_replay_checkpoints", ["user_id", "source_kind"]
    )


def downgrade() -> None:
    op.drop_table("graph_replay_checkpoints")
