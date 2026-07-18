"""Derived reflection proposals; never a user fact without a separate governed capture."""
from sqlalchemy import Column, DateTime, Float, Index, JSON, String, Text, func

from src.shared.db.database import Base


class InsightProposal(Base):
    __tablename__ = "insight_proposals"

    id = Column(String(64), primary_key=True)
    user_id = Column(String(64), nullable=False, index=True)
    source_key = Column(String(128), nullable=False)
    title = Column(String(255), nullable=False)
    summary = Column(Text, nullable=False)
    support_memory_ids = Column(JSON, nullable=False, default=list)
    counter_memory_ids = Column(JSON, nullable=False, default=list)
    valid_from = Column(DateTime(timezone=True), nullable=True)
    valid_until = Column(DateTime(timezone=True), nullable=True)
    confidence = Column(Float, nullable=False, default=0.0)
    invalidation_condition = Column(Text, nullable=False)
    status = Column(String(24), nullable=False, default="proposed")
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("ix_insight_user_status_created", "user_id", "status", "created_at"),
        Index("ix_insight_user_source_key", "user_id", "source_key", unique=True),
    )
