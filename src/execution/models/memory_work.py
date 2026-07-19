"""Database-authoritative work ledger for the V2.2 Working Agent."""
from __future__ import annotations

from sqlalchemy import (
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    func,
)

from src.shared.db.database import Base


class MemoryWorkCase(Base):
    __tablename__ = "memory_work_cases"

    id = Column(String(64), primary_key=True)
    user_id = Column(String(64), nullable=False, index=True)
    proposition_key = Column(String(64), nullable=False)
    case_type = Column(String(32), nullable=False, default="fact")
    title = Column(String(240), nullable=False)
    summary = Column(Text, nullable=True)
    status = Column(String(32), nullable=False, default="open")
    sensitivity = Column(String(16), nullable=False, default="normal")
    confidence = Column(Float, nullable=False, default=0.0)
    active_memory_id = Column(
        String(64),
        ForeignKey("committed_memories.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    version = Column(Integer, nullable=False, default=1)
    case_metadata = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
    resolved_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("user_id", "proposition_key", name="uq_memory_work_case_user_proposition"),
        Index("ix_memory_work_case_user_status", "user_id", "status", "updated_at"),
    )


class MemoryWorkEvidence(Base):
    __tablename__ = "memory_work_evidence"

    id = Column(String(64), primary_key=True)
    case_id = Column(
        String(64),
        ForeignKey("memory_work_cases.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id = Column(String(64), nullable=False, index=True)
    raw_event_id = Column(
        String(64),
        ForeignKey("raw_events.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    evidence_seal_id = Column(String(64), ForeignKey("evidence_seals.id", ondelete="SET NULL"), nullable=True, index=True)
    source_turn_id = Column(String(64), nullable=True, index=True)
    episode_id = Column(String(64), nullable=True, index=True)
    quote = Column(Text, nullable=True)
    relationship = Column(String(24), nullable=False, default="supports")
    source_type = Column(String(32), nullable=False)
    trust_class = Column(String(32), nullable=False, default="unclassified")
    occurred_at = Column(DateTime(timezone=True), nullable=True)
    evidence_metadata = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint(
            "case_id",
            "raw_event_id",
            "relationship",
            name="uq_memory_work_evidence_case_event_relation",
        ),
        Index("ix_memory_work_evidence_user_case", "user_id", "case_id"),
    )


class MemoryWorkDecision(Base):
    __tablename__ = "memory_work_decisions"

    id = Column(String(64), primary_key=True)
    case_id = Column(
        String(64),
        ForeignKey("memory_work_cases.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id = Column(String(64), nullable=False, index=True)
    source_run_id = Column(
        String(64),
        ForeignKey("agent_runs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    source_event_id = Column(String(64), nullable=True, index=True)
    state = Column(String(40), nullable=False)
    rationale = Column(Text, nullable=True)
    rationale_codes = Column(JSON, nullable=False, default=list)
    duplicate_refs = Column(JSON, nullable=False, default=list)
    conflict_refs = Column(JSON, nullable=False, default=list)
    memory_ids = Column(JSON, nullable=False, default=list)
    policy_result = Column(JSON, nullable=False, default=dict)
    model = Column(String(128), nullable=True)
    prompt_id = Column(String(96), nullable=True)
    prompt_version = Column(String(32), nullable=True)
    idempotency_key = Column(String(64), nullable=False, unique=True, index=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        Index("ix_memory_work_decision_user_state", "user_id", "state", "created_at"),
    )
