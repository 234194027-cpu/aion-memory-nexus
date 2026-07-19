from sqlalchemy import Column, String, DateTime, Text, Enum, func, ForeignKey
from src.shared.db.database import Base
from src.memory.models.raw_event import SourceType

class MemorySource(Base):
    __tablename__ = "memory_sources"
    
    id = Column(String, primary_key=True, index=True)
    memory_id = Column(String, nullable=False)
    raw_event_id = Column(String, ForeignKey("raw_events.id", ondelete="SET NULL"), nullable=True)
    evidence_seal_id = Column(String(64), ForeignKey("evidence_seals.id", ondelete="SET NULL"), nullable=True, index=True)
    quote = Column(Text, nullable=True)
    location = Column(String, nullable=True)
    source_type = Column(Enum(SourceType, values_callable=lambda x: [e.value for e in x]), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
