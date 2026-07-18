"""Add V3 Graphiti derived-projection outbox.

Revision ID: 026
Revises: 025
"""

from alembic import op
import sqlalchemy as sa


revision = "026"
down_revision = "025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "graph_projections",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("projection_key", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("project_id", sa.String(length=64), nullable=True),
        sa.Column("source_kind", sa.String(length=32), nullable=False),
        sa.Column("source_id", sa.String(length=64), nullable=False),
        sa.Column("source_revision", sa.String(length=128), nullable=False),
        sa.Column("operation", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="queued"),
        sa.Column("projection_metadata", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("lease_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=128), nullable=True),
        sa.Column("projected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("projection_key", name="uq_graph_projections_projection_key"),
        sa.UniqueConstraint("source_kind", "source_id", "source_revision", "operation", name="uq_graph_projection_source_revision"),
    )
    op.create_index("ix_graph_projections_projection_key", "graph_projections", ["projection_key"])
    op.create_index("ix_graph_projections_user_id", "graph_projections", ["user_id"])
    op.create_index("ix_graph_projections_project_id", "graph_projections", ["project_id"])
    op.create_index("ix_graph_projections_status", "graph_projections", ["status"])
    op.create_index("ix_graph_projection_dispatch", "graph_projections", ["status", "next_retry_at", "lease_started_at"])
    op.create_index("ix_graph_projection_source", "graph_projections", ["user_id", "source_kind", "source_id"])


def downgrade() -> None:
    op.drop_table("graph_projections")
