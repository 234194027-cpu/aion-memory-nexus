from sqlalchemy import Column, String, DateTime, Text, Integer, ForeignKey, Index
from src.shared.db.database import Base
from datetime import datetime


class WeeklyReview(Base):
    __tablename__ = "weekly_reviews"

    id = Column(String(64), primary_key=True, index=True)
    user_id = Column(String(64), ForeignKey("users.id"), nullable=False, index=True)
    week_start = Column(String(10), nullable=False, index=True)
    week_end = Column(String(10), nullable=False)
    new_memories_json = Column(Text, nullable=False, default="[]")
    decisions_json = Column(Text, nullable=False, default="[]")
    highlights_json = Column(Text, nullable=False, default="[]")
    open_questions_json = Column(Text, nullable=False, default="[]")
    summary = Column(Text, nullable=False, default="")
    word_count = Column(Integer, nullable=False, default=0)
    persona_observations_json = Column(Text, nullable=True)  # JSON list
    open_loops_json = Column(Text, nullable=True)             # JSON list
    risks_to_watch_json = Column(Text, nullable=True)         # JSON list
    suggested_focus_json = Column(Text, nullable=True)        # JSON list
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index("ix_weekly_user_week", "user_id", "week_start"),
    )