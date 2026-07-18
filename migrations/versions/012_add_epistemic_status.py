"""Add compatibility-safe epistemic status to memory records.

Revision ID: 012
Revises: 011
"""

from alembic import op
import sqlalchemy as sa


revision = "012"
down_revision = "011"
branch_labels = None
depends_on = None


def _add_column_if_missing(table_name: str, column_name: str) -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns(table_name)}
    if column_name not in columns:
        op.add_column(
            table_name,
            sa.Column(column_name, sa.String(length=32), nullable=False, server_default="legacy_unclassified"),
        )


def upgrade() -> None:
    _add_column_if_missing("candidate_memories", "epistemic_status")
    _add_column_if_missing("committed_memories", "epistemic_status")


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    for table_name in ("committed_memories", "candidate_memories"):
        columns = {column["name"] for column in inspector.get_columns(table_name)}
        if "epistemic_status" in columns:
            op.drop_column(table_name, "epistemic_status")
