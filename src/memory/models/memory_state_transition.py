"""Append-only audit records for candidate and committed-memory state changes."""

from sqlalchemy import Column, DateTime, JSON, String, Index, func

from src.shared.db.database import Base


class MemoryStateTransition(Base):
    __tablename__ = "memory_state_transitions"

    id = Column(String(64), primary_key=True)
    user_id = Column(String(64), nullable=False)
    subject_type = Column(String(32), nullable=False)
    subject_id = Column(String(64), nullable=False)
    from_state = Column(String(48), nullable=True)
    to_state = Column(String(48), nullable=False)
    actor_type = Column(String(32), nullable=False)
    actor_id = Column(String(64), nullable=True)
    reason = Column(String(128), nullable=True)
    evidence_refs = Column(JSON, nullable=False, default=list)
    policy_version = Column(String(32), nullable=False, default="memory-governance-v1")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_memory_state_transitions_user_created", "user_id", "created_at"),
        Index("ix_memory_state_transitions_subject", "subject_type", "subject_id"),
    )
