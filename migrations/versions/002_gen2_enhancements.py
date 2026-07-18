"""gen2 enhancements - new tables and DecisionRecord columns

Revision ID: 002
Revises: 001
Create Date: 2026-06-29
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ---- decision_reviews ----
    op.create_table(
        "decision_reviews",
        sa.Column("id", sa.String(64), primary_key=True, index=True),
        sa.Column("decision_id", sa.String(64), sa.ForeignKey("decision_records.id"), nullable=False, index=True),
        sa.Column("user_id", sa.String(64), sa.ForeignKey("users.id"), nullable=False, index=True),
        sa.Column("review_date", sa.DateTime, nullable=False),
        sa.Column("expected_vs_actual", sa.Text, nullable=True),
        sa.Column("result_summary", sa.Text, nullable=True),
        sa.Column("what_went_right", sa.Text, nullable=True),
        sa.Column("what_went_wrong", sa.Text, nullable=True),
        sa.Column("lesson_learned", sa.Text, nullable=True),
        sa.Column("future_adjustment", sa.Text, nullable=True),
        sa.Column("review_score", sa.Float, nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False),
    )

    # ---- conflict_records ----
    op.create_table(
        "conflict_records",
        sa.Column("id", sa.String(64), primary_key=True, index=True),
        sa.Column("user_id", sa.String(64), nullable=False, index=True),
        sa.Column("conflict_type", sa.String(32), nullable=False, index=True),
        sa.Column("current_statement", sa.Text, nullable=False),
        sa.Column("past_statement", sa.Text, nullable=True),
        sa.Column("related_memory_ids", sa.Text, nullable=True),
        sa.Column("related_decision_ids", sa.Text, nullable=True),
        sa.Column("severity", sa.String(10), nullable=False, default="low"),
        sa.Column("interpretation", sa.String(32), nullable=False, default="unknown"),
        sa.Column("recommended_action", sa.String(32), nullable=False, default="review"),
        sa.Column("confidence", sa.Float, nullable=False, default=0.5),
        sa.Column("status", sa.String(20), nullable=False, default="open"),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("resolved_at", sa.DateTime, nullable=True),
    )

    # ---- memory_relations ----
    op.create_table(
        "memory_relations",
        sa.Column("id", sa.String(64), primary_key=True, index=True),
        sa.Column("user_id", sa.String(64), nullable=False, index=True),
        sa.Column("source_memory_id", sa.String(64), nullable=False, index=True),
        sa.Column("target_memory_id", sa.String(64), nullable=False, index=True),
        sa.Column("relation_type", sa.String(32), nullable=False, index=True),
        sa.Column("reason", sa.Text, nullable=True),
        sa.Column("confidence", sa.Float, nullable=False, default=0.5),
        sa.Column("created_at", sa.DateTime, nullable=False),
    )

    # ---- advisor_sessions ----
    op.create_table(
        "advisor_sessions",
        sa.Column("id", sa.String(64), primary_key=True, index=True),
        sa.Column("user_id", sa.String(64), nullable=False, index=True),
        sa.Column("question", sa.Text, nullable=False),
        sa.Column("advisor_mode", sa.String(32), nullable=False, default="decision"),
        sa.Column("answer", sa.Text, nullable=False),
        sa.Column("direct_recommendation", sa.Text, nullable=True),
        sa.Column("cited_memory_ids", sa.Text, nullable=True),
        sa.Column("cited_decision_ids", sa.Text, nullable=True),
        sa.Column("risk_points", sa.Text, nullable=True),
        sa.Column("uncertainty", sa.Text, nullable=True),
        sa.Column("confidence", sa.Float, nullable=False, default=0.5),
        sa.Column("created_at", sa.DateTime, nullable=False),
    )

    # ---- decision_records: add Gen 2 columns ----
    op.add_column("decision_records", sa.Column("alternatives_json", sa.Text, nullable=True))
    op.add_column("decision_records", sa.Column("confidence", sa.Float, nullable=False, server_default="0.5"))
    op.add_column("decision_records", sa.Column("importance", sa.Float, nullable=False, server_default="0.5"))
    op.add_column("decision_records", sa.Column("decision_type", sa.String(32), nullable=False, server_default="other"))
    op.add_column("decision_records", sa.Column("review_at", sa.DateTime, nullable=True))
    op.add_column("decision_records", sa.Column("reviewed_at", sa.DateTime, nullable=True))
    op.add_column("decision_records", sa.Column("created_from_memory_id", sa.String(64), nullable=True))


def downgrade() -> None:
    # ---- decision_records: remove Gen 2 columns ----
    op.drop_column("decision_records", "created_from_memory_id")
    op.drop_column("decision_records", "reviewed_at")
    op.drop_column("decision_records", "review_at")
    op.drop_column("decision_records", "decision_type")
    op.drop_column("decision_records", "importance")
    op.drop_column("decision_records", "confidence")
    op.drop_column("decision_records", "alternatives_json")

    # ---- drop new tables ----
    op.drop_table("advisor_sessions")
    op.drop_table("memory_relations")
    op.drop_table("conflict_records")
    op.drop_table("decision_reviews")
