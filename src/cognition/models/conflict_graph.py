"""ConflictGraph — 记忆间冲突关系网 (Gen 3 Cognitive OS).

与 ConflictRecord 的区别:
- ConflictRecord: 单次检测到的冲突事件 (扁平)
- ConflictGraph: 维护 memory 之间的持久冲突关系网 (图结构)

每条边表示两个 memory 之间存在语义矛盾，
支持冲突传播: A↔B, B↔C → 可推断 A↔C 可能冲突。
"""
from sqlalchemy import Column, String, DateTime, Text, Float, Index, func
from src.shared.db.database import Base


class ConflictGraphEdge(Base):
    __tablename__ = "conflict_graph_edges"

    id = Column(String(64), primary_key=True, index=True)
    user_id = Column(String(64), nullable=False, index=True)
    project_id = Column(String(64), nullable=True)

    # 冲突的两条记忆
    memory_id_a = Column(String(64), nullable=False, index=True)
    memory_id_b = Column(String(64), nullable=False, index=True)

    # 冲突属性
    conflict_type = Column(String(32), nullable=False, index=True)
    # 类型: belief_conflict / decision_conflict / preference_conflict /
    #        principle_conflict / strategy_conflict / factual_contradiction

    severity = Column(String(10), nullable=False, default="medium")
    # low / medium / high

    # 冲突描述
    statement_a = Column(Text, nullable=True)   # memory_a 的立场
    statement_b = Column(Text, nullable=True)   # memory_b 的立场
    explanation = Column(Text, nullable=True)    # 冲突原因

    # 解析状态: unresolved / acknowledged / resolved / superseded
    resolution_status = Column(String(20), nullable=False, default="unresolved", index=True)
    resolution_note = Column(Text, nullable=True)

    # 置信度 (0-1)
    confidence = Column(Float, nullable=False, default=0.5)

    # 检测来源: conflict_checker / manual / belief_evolution
    detected_by = Column(String(32), nullable=False, default="conflict_checker")

    # 关联的 ConflictRecord ID (如果有)
    linked_conflict_record_id = Column(String(64), nullable=True)

    # 时间戳
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    resolved_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_conflict_graph_user_pair", "user_id", "memory_id_a", "memory_id_b"),
        Index("ix_conflict_graph_user_status", "user_id", "resolution_status"),
    )
