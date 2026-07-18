"""Add privacy-safe lifecycle audit records.

Revision ID: 011
Revises: 010
"""

from alembic import op
import sqlalchemy as sa


revision = "011"
down_revision = "010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "data_lifecycle_audits" in inspector.get_table_names():
        return
    op.create_table(
        "data_lifecycle_audits",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("action", sa.String(length=48), nullable=False),
        sa.Column("target_type", sa.String(length=48), nullable=False),
        sa.Column("target_id", sa.String(length=64), nullable=False),
        sa.Column("affected_counts", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("policy_version", sa.String(length=32), nullable=False, server_default="lifecycle-v1"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_lifecycle_audits_user_created", "data_lifecycle_audits", ["user_id", "created_at"])
    op.create_index("ix_lifecycle_audits_target", "data_lifecycle_audits", ["target_type", "target_id"])


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "data_lifecycle_audits" not in inspector.get_table_names():
        return
    op.drop_index("ix_lifecycle_audits_target", table_name="data_lifecycle_audits")
    op.drop_index("ix_lifecycle_audits_user_created", table_name="data_lifecycle_audits")
    op.drop_table("data_lifecycle_audits")
