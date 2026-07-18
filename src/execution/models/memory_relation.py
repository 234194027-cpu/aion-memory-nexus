from sqlalchemy import Column, String, DateTime, Text, Float
from src.shared.db.database import Base
from datetime import datetime


class MemoryRelation(Base):
    __tablename__ = "memory_relations"

    id = Column(String(64), primary_key=True, index=True)
    user_id = Column(String(64), nullable=False, index=True)
    source_memory_id = Column(String(64), nullable=False, index=True)
    target_memory_id = Column(String(64), nullable=False, index=True)
    relation_type = Column(String(32), nullable=False, index=True)  # 9种类型
    reason = Column(Text, nullable=True)
    confidence = Column(Float, nullable=False, default=0.5)
    valid_from = Column(DateTime(timezone=True), nullable=True)
    valid_until = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
