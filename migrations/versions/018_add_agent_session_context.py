"""Add bounded durable conversation context to V2 agent sessions.

Revision ID: 018
Revises: 017
"""
from alembic import op
import sqlalchemy as sa

revision = "018"
down_revision = "017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("agent_sessions", sa.Column("context_payload", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("agent_sessions", "context_payload")
