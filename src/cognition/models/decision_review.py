from sqlalchemy import Column, String, DateTime, Text, ForeignKey, Index
from src.shared.db.database import Base
from datetime import datetime


class DecisionReview(Base):
    __tablename__ = "decision_reviews"

    id = Column(String(64), primary_key=True, index=True)
    decision_id = Column(String(64), ForeignKey("decision_records.id"), nullable=False, index=True)
    user_id = Column(String(64), ForeignKey("users.id"), nullable=False, index=True)
    review_notes = Column(Text, nullable=False)
    lessons_learned = Column(Text, nullable=True)
    outcome_rating = Column(String(20), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index("ix_decision_review_user", "user_id", "created_at"),
        Index("ix_decision_review_decision", "decision_id", "created_at"),
    )
