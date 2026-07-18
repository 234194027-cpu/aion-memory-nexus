"""Privacy-safe audit trail for memory lifecycle operations."""

from sqlalchemy import Column, DateTime, JSON, String, func, Index

from src.shared.db.database import Base


class DataLifecycleAudit(Base):
    __tablename__ = "data_lifecycle_audits"

    id = Column(String(64), primary_key=True)
    user_id = Column(String(64), nullable=False)
    action = Column(String(48), nullable=False)
    target_type = Column(String(48), nullable=False)
    target_id = Column(String(64), nullable=False)
    affected_counts = Column(JSON, nullable=False, default=dict)
    policy_version = Column(String(32), nullable=False, default="lifecycle-v1")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_lifecycle_audits_user_created", "user_id", "created_at"),
        Index("ix_lifecycle_audits_target", "target_type", "target_id"),
    )
