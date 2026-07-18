"""Add compatible processing lease metadata to RawEvent.

Revision ID: 014
Revises: 013
"""

from alembic import op
import sqlalchemy as sa


revision = "014"
down_revision = "013"
branch_labels = None
depends_on = None


def _columns() -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns("raw_events")}


def upgrade() -> None:
    columns = _columns()
    if "processing_started_at" not in columns:
        op.add_column("raw_events", sa.Column("processing_started_at", sa.DateTime(timezone=True), nullable=True))
    if "processing_attempts" not in columns:
        op.add_column("raw_events", sa.Column("processing_attempts", sa.Integer(), nullable=False, server_default="0"))
    if "processing_error" not in columns:
        op.add_column("raw_events", sa.Column("processing_error", sa.String(length=128), nullable=True))
    indexes = {index["name"] for index in sa.inspect(op.get_bind()).get_indexes("raw_events")}
    if "ix_raw_event_processing_lease" not in indexes:
        op.create_index("ix_raw_event_processing_lease", "raw_events", ["processing_status", "processing_started_at"])


def downgrade() -> None:
    bind = op.get_bind()
    indexes = {index["name"] for index in sa.inspect(bind).get_indexes("raw_events")}
    if "ix_raw_event_processing_lease" in indexes:
        op.drop_index("ix_raw_event_processing_lease", table_name="raw_events")
    for column in ("processing_error", "processing_attempts", "processing_started_at"):
        if column in _columns():
            op.drop_column("raw_events", column)
