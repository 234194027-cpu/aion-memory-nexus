from sqlalchemy import Column, String, DateTime, Text, Float, ForeignKey, Index
from src.shared.db.database import Base
from datetime import datetime


class SimulationRun(Base):
    __tablename__ = "simulation_runs"

    id = Column(String(64), primary_key=True, index=True)
    user_id = Column(String(64), ForeignKey("users.id"), nullable=False, index=True)
    question = Column(Text, nullable=False)
    baseline_summary = Column(Text, nullable=True)
    counterfactual = Column(Text, nullable=True)
    outcome = Column(Text, nullable=True)
    confidence = Column(Float, nullable=False, default=0.4)
    linked_memory_ids = Column(Text, nullable=True)
    horizon_days = Column(String(20), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index("ix_simulation_runs_user_created", "user_id", "created_at"),
    )
