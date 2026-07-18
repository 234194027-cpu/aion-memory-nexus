"""Add idempotency_key column to candidate_memories (D20).

Revision ID: 016
Revises: 015

WP-0A-T06: 为后续变更型工具契约预留 idempotency_key。
本迁移只增加可空字段和普通索引，不宣称已经实现去重；实际写入语义与
user/tool 作用域唯一约束由 WP-1 在工具契约冻结后完成。
"""

from alembic import op
import sqlalchemy as sa


revision = "016"
down_revision = "015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    if "candidate_memories" not in tables:
        # 测试环境或全新部署时表尚未创建；CREATE_SCHEMA_ON_STARTUP 会基于 model 自动建表。
        return

    columns = {col["name"] for col in inspector.get_columns("candidate_memories")}
    if "idempotency_key" in columns:
        # 幂等：列已存在则跳过
        return

    op.add_column(
        "candidate_memories",
        sa.Column("idempotency_key", sa.String(length=64), nullable=True),
    )
    op.create_index(
        "ix_candidate_memories_idempotency_key",
        "candidate_memories",
        ["idempotency_key"],
    )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    if "candidate_memories" not in tables:
        return

    indexes = {idx["name"] for idx in inspector.get_indexes("candidate_memories")}
    if "ix_candidate_memories_idempotency_key" in indexes:
        op.drop_index(
            "ix_candidate_memories_idempotency_key",
            table_name="candidate_memories",
        )

    columns = {col["name"] for col in inspector.get_columns("candidate_memories")}
    if "idempotency_key" in columns:
        op.drop_column("candidate_memories", "idempotency_key")
