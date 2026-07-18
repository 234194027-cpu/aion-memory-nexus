"""BeliefSystem — 用户长期信念演化模型 (Gen 3 Cognitive OS).

跟踪用户跨时间变化的核心观点、价值观、决策风格。
每条 belief 可被多条 memory 支撑 (evidence_memory_ids)，
当新证据与旧信念冲突时，触发 evolution 记录。
"""
from sqlalchemy import Column, String, DateTime, Text, Float, JSON, Index, func
from src.shared.db.database import Base


class BeliefSystem(Base):
    __tablename__ = "belief_systems"

    id = Column(String(64), primary_key=True, index=True)
    user_id = Column(String(64), nullable=False, index=True)
    project_id = Column(String(64), nullable=True)

    # 信念分类
    belief_category = Column(String(32), nullable=False, index=True)
    # 类别: decision_style / risk_preference / technical_belief /
    #        personal_value / working_habit / social_tendency

    # 信念内容
    title = Column(String(255), nullable=False)
    content = Column(Text, nullable=False)

    # 置信度 (0-1): 基于支撑证据的数量和质量
    confidence = Column(Float, nullable=False, default=0.5)

    # 稳定性 (0-1): 1.0=从未变化, 0.0=频繁变化
    stability = Column(Float, nullable=False, default=1.0)

    # 状态: active / superseded / abandoned
    status = Column(String(20), nullable=False, default="active", index=True)

    # 支撑证据
    evidence_memory_ids = Column(JSON, default=[])
    evidence_decision_ids = Column(JSON, default=[])

    # 演化历史: [{timestamp, old_content, new_content, trigger_memory_id, reason}]
    evolution_history = Column(JSON, default=[])

    # 时间戳
    valid_from = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    valid_until = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    last_challenged_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_belief_user_category", "user_id", "belief_category"),
        Index("ix_belief_user_status", "user_id", "status"),
    )
