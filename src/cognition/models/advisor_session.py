from sqlalchemy import Column, String, DateTime, Text, Float
from src.shared.db.database import Base
from datetime import datetime


class AdvisorSession(Base):
    __tablename__ = "advisor_sessions"

    id = Column(String(64), primary_key=True, index=True)
    user_id = Column(String(64), nullable=False, index=True)
    question = Column(Text, nullable=False)
    advisor_mode = Column(String(32), nullable=False, default="decision")
    answer = Column(Text, nullable=False)
    direct_recommendation = Column(Text, nullable=True)
    cited_memory_ids = Column(Text, nullable=True)     # JSON list
    cited_decision_ids = Column(Text, nullable=True)    # JSON list
    risk_points = Column(Text, nullable=True)           # JSON list
    uncertainty = Column(Text, nullable=True)
    confidence = Column(Float, nullable=False, default=0.5)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
