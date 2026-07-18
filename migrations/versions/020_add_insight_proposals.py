"""Add non-factual reflection proposal records.

Revision ID: 020
Revises: 019
"""
from alembic import op
import sqlalchemy as sa

revision = "020"
down_revision = "019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "insight_proposals",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("source_key", sa.String(length=128), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("support_memory_ids", sa.JSON(), nullable=False),
        sa.Column("counter_memory_ids", sa.JSON(), nullable=False),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0"),
        sa.Column("invalidation_condition", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="proposed"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "source_key", name="uq_insight_user_source_key"),
    )
    op.create_index("ix_insight_proposals_user_id", "insight_proposals", ["user_id"])
    op.create_index("ix_insight_user_status_created", "insight_proposals", ["user_id", "status", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_insight_user_status_created", table_name="insight_proposals")
    op.drop_index("ix_insight_proposals_user_id", table_name="insight_proposals")
    op.drop_table("insight_proposals")
