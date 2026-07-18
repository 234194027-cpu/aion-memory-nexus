from sqlalchemy import Column, String, DateTime, Text, Integer, Float, ForeignKey, Index
from src.shared.db.database import Base
from datetime import datetime


class DecisionRecord(Base):
    __tablename__ = "decision_records"

    id = Column(String(64), primary_key=True, index=True)
    user_id = Column(String(64), ForeignKey("users.id"), nullable=False, index=True)
    title = Column(String(255), nullable=False)
    context = Column(Text, nullable=False)
    decision = Column(Text, nullable=False)
    rationale = Column(Text, nullable=False)
    expected_outcome = Column(Text, nullable=True)
    actual_outcome = Column(Text, nullable=True)
    status = Column(String(20), nullable=False, default="open")
    linked_memory_id = Column(String(64), nullable=True)
    project_id = Column(String(128), nullable=True, index=True)
    decided_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    resolved_at = Column(DateTime, nullable=True)
    review_count = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    # Gen 2 v2.0 新增列
    alternatives_json = Column(Text, nullable=True)                        # JSON list of alternative options
    confidence = Column(Float, nullable=False, default=0.5)                # 决策置信度
    importance = Column(Float, nullable=False, default=0.5)                # 决策重要性
    decision_type = Column(String(32), nullable=False, default="other")    # 9种类型
    review_at = Column(DateTime, nullable=True)                            # 计划复盘时间
    reviewed_at = Column(DateTime, nullable=True)                          # 实际复盘时间
    created_from_memory_id = Column(String(64), nullable=True)             # 来源 memory id

    __table_args__ = (
        Index("ix_decision_user_status", "user_id", "status"),
        Index("ix_decision_user_project", "user_id", "project_id"),
        Index("ix_decision_user_decided_at", "user_id", "decided_at"),
    )