"""Add expiry semantics to active Agent handoffs.

Revision ID: 022
Revises: 021
"""
from alembic import op
import sqlalchemy as sa


revision = "022"
down_revision = "021"
branch_labels = None
depends_on = None


def _columns() -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns("agent_handoffs")}


def upgrade() -> None:
    if "expires_at" not in _columns():
        op.add_column("agent_handoffs", sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True))
        op.create_index("ix_agent_handoffs_active_expiry", "agent_handoffs", ["user_id", "mode", "status", "expires_at"])


def downgrade() -> None:
    columns = _columns()
    if "expires_at" in columns:
        op.drop_index("ix_agent_handoffs_active_expiry", table_name="agent_handoffs")
        op.drop_column("agent_handoffs", "expires_at")
