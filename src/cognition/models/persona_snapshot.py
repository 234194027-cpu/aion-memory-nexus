from sqlalchemy import Column, String, DateTime, Text, ForeignKey
from src.shared.db.database import Base
from datetime import datetime


class PersonaSnapshot(Base):
    __tablename__ = "persona_snapshots"

    id = Column(String(64), primary_key=True, index=True)
    user_id = Column(String(64), ForeignKey("users.id"), nullable=False, index=True)
    snapshot_date = Column(String(10), nullable=False, index=True)
    mode = Column(String(20), nullable=False, default="full")
    traits_json = Column(Text, nullable=False, default="[]")
    summary = Column(Text, nullable=False, default="")
    evidence_memory_ids = Column(Text, nullable=False, default="[]")
    embed_method = Column(String(20), nullable=True)
    patterns_json = Column(Text, nullable=True)         # JSON: 行为模式列表
    biases_json = Column(Text, nullable=True)            # JSON: 认知偏差列表
    decision_style_json = Column(Text, nullable=True)    # JSON: 决策风格
    risk_profile_json = Column(Text, nullable=True)      # JSON: 风险偏好
    evolution_json = Column(Text, nullable=True)         # JSON: 进化趋势
    source_decision_ids = Column(Text, nullable=True)    # JSON list
    created_at = Column(DateTime, default=datetime.utcnow)
