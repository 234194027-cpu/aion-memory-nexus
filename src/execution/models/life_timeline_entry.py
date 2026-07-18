from datetime import datetime

from sqlalchemy import Column, DateTime, Float, ForeignKey, String, Text
from src.shared.db.database import Base


class LifeTimelineEntry(Base):
    __tablename__ = "life_timeline_entries"

    id = Column(String(64), primary_key=True)
    user_id = Column(String(64), ForeignKey("users.id"), index=True)
    entry_date = Column(String(10), index=True)
    entry_kind = Column(String(20))
    ref_id = Column(String(64))
    title = Column(String(255))
    snippet = Column(Text, nullable=True)
    importance = Column(Float, default=0.5)
    project_id = Column(String(128), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
