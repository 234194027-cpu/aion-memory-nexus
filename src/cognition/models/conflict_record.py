from sqlalchemy import Column, String, DateTime, Text, Float
from src.shared.db.database import Base
from datetime import datetime

VALID_CONFLICT_STATUS = ("open", "acknowledged", "resolved", "ignored")
VALID_CONFLICT_TYPES = (
    "belief_conflict", "decision_conflict", "preference_conflict",
    "principle_conflict", "strategy_conflict", "timeline_change", "correction",
)
VALID_INTERPRETATIONS = (
    "growth", "changed_context", "inconsistency", "repeated_error", "unknown",
)


class ConflictRecord(Base):
    __tablename__ = "conflict_records"

    id = Column(String(64), primary_key=True, index=True)
    user_id = Column(String(64), nullable=False, index=True)
    conflict_type = Column(String(32), nullable=False, index=True)  # 7种类型
    current_statement = Column(Text, nullable=False)     # 当前语句
    past_statement = Column(Text, nullable=True)         # 过去语句
    related_memory_ids = Column(Text, nullable=True)     # JSON list
    related_decision_ids = Column(Text, nullable=True)   # JSON list
    severity = Column(String(10), nullable=False, default="low")  # low/medium/high
    interpretation = Column(String(32), nullable=False, default="unknown")  # 5种
    recommended_action = Column(String(32), nullable=False, default="review")
    confidence = Column(Float, nullable=False, default=0.5)
    status = Column(String(20), nullable=False, default="open")  # open/acknowledged/resolved/ignored
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    resolved_at = Column(DateTime, nullable=True)
