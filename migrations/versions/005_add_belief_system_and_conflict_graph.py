"""Add belief_system and conflict_graph tables

Revision ID: 005
Revises: 004
Create Date: 2026-06-30

Gen 3 Cognitive OS 增强：
- belief_systems: 用户长期信念演化模型
- conflict_graph_edges: 记忆间冲突关系网
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '005'
down_revision = '004'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # belief_systems 表
    op.create_table(
        'belief_systems',
        sa.Column('id', sa.String(64), primary_key=True, index=True),
        sa.Column('user_id', sa.String(64), nullable=False, index=True),
        sa.Column('project_id', sa.String(64), nullable=True),
        sa.Column('belief_category', sa.String(32), nullable=False, index=True),
        sa.Column('title', sa.String(255), nullable=False),
        sa.Column('content', sa.Text, nullable=False),
        sa.Column('confidence', sa.Float, nullable=False, default=0.5),
        sa.Column('stability', sa.Float, nullable=False, default=1.0),
        sa.Column('status', sa.String(20), nullable=False, default='active', index=True),
        sa.Column('evidence_memory_ids', sa.JSON, default=[]),
        sa.Column('evidence_decision_ids', sa.JSON, default=[]),
        sa.Column('evolution_history', sa.JSON, default=[]),
        sa.Column('valid_from', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('valid_until', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), onupdate=sa.func.now()),
        sa.Column('last_challenged_at', sa.DateTime(timezone=True), nullable=True),
        sa.Index('ix_belief_user_category', 'user_id', 'belief_category'),
        sa.Index('ix_belief_user_status', 'user_id', 'status'),
    )

    # conflict_graph_edges 表
    op.create_table(
        'conflict_graph_edges',
        sa.Column('id', sa.String(64), primary_key=True, index=True),
        sa.Column('user_id', sa.String(64), nullable=False, index=True),
        sa.Column('project_id', sa.String(64), nullable=True),
        sa.Column('memory_id_a', sa.String(64), nullable=False, index=True),
        sa.Column('memory_id_b', sa.String(64), nullable=False, index=True),
        sa.Column('conflict_type', sa.String(32), nullable=False, index=True),
        sa.Column('severity', sa.String(10), nullable=False, default='medium'),
        sa.Column('statement_a', sa.Text, nullable=True),
        sa.Column('statement_b', sa.Text, nullable=True),
        sa.Column('explanation', sa.Text, nullable=True),
        sa.Column('resolution_status', sa.String(20), nullable=False, default='unresolved', index=True),
        sa.Column('resolution_note', sa.Text, nullable=True),
        sa.Column('confidence', sa.Float, nullable=False, default=0.5),
        sa.Column('detected_by', sa.String(32), nullable=False, default='conflict_checker'),
        sa.Column('linked_conflict_record_id', sa.String(64), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), onupdate=sa.func.now()),
        sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
        sa.Index('ix_conflict_graph_user_pair', 'user_id', 'memory_id_a', 'memory_id_b'),
        sa.Index('ix_conflict_graph_user_status', 'user_id', 'resolution_status'),
    )


def downgrade() -> None:
    op.drop_table('conflict_graph_edges')
    op.drop_table('belief_systems')
