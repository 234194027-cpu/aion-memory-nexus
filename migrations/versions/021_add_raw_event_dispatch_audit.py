"""Add auditable dispatch metadata to the RawEvent extraction queue.

Revision ID: 021
Revises: 020
"""
from alembic import op
import sqlalchemy as sa


revision = "021"
down_revision = "020"
branch_labels = None
depends_on = None


def _columns() -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns("raw_events")}


def upgrade() -> None:
    columns = _columns()
    if "processing_heartbeat_at" not in columns:
        op.add_column("raw_events", sa.Column("processing_heartbeat_at", sa.DateTime(timezone=True), nullable=True))
    if "processing_next_retry_at" not in columns:
        op.add_column("raw_events", sa.Column("processing_next_retry_at", sa.DateTime(timezone=True), nullable=True))
    if "processing_result" not in columns:
        op.add_column("raw_events", sa.Column("processing_result", sa.String(length=64), nullable=True))


def downgrade() -> None:
    for column in ("processing_result", "processing_next_retry_at", "processing_heartbeat_at"):
        if column in _columns():
            op.drop_column("raw_events", column)
