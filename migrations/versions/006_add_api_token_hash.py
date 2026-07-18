"""Add api_token_hash and is_default to agent_profiles

Revision ID: 006
Revises: 005
Create Date: 2026-07-03
"""
from alembic import op
import sqlalchemy as sa


revision = '006'
down_revision = '005'
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    
    # 检查列是否存在
    columns = [c["name"] for c in inspector.get_columns("agent_profiles")]
    
    if "api_token_hash" not in columns:
        op.add_column("agent_profiles", sa.Column("api_token_hash", sa.String, nullable=True))
        op.create_index("ix_agent_api_token_hash", "agent_profiles", ["api_token_hash"])
    
    if "is_default" not in columns:
        op.add_column("agent_profiles", sa.Column("is_default", sa.Boolean, default=False))


def downgrade() -> None:
    op.drop_column("agent_profiles", "is_default")
    op.drop_index("ix_agent_api_token_hash", table_name="agent_profiles")
    op.drop_column("agent_profiles", "api_token_hash")
