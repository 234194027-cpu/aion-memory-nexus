"""Durable, autonomous maintenance records for the Working Agent.

These rows are operational audit records, not user memories.  They make the
background "sleep" loop resumable and keep raw-event compaction independent
from the formal-memory truth layer.
"""
from __future__ import annotations

from sqlalchemy import Column, DateTime, Float, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint, func

from src.shared.db.database import Base


class MemoryMaintenanceRun(Base):
    __tablename__ = "memory_maintenance_runs"

    id = Column(String(64), primary_key=True)
    user_id = Column(String(64), nullable=True, index=True)
    kind = Column(String(32), nullable=False)  # daily | weekly | retention | recovery
    state = Column(String(24), nullable=False, default="running")
    idempotency_key = Column(String(96), nullable=False, unique=True, index=True)
    cursor = Column(JSON, nullable=False, default=dict)
    counters = Column(JSON, nullable=False, default=dict)
    token_budget = Column(Integer, nullable=False, default=0)
    token_used = Column(Integer, nullable=False, default=0)
    error = Column(String(256), nullable=True)
    started_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    finished_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (Index("ix_maintenance_run_kind_state", "kind", "state", "started_at"),)


class MemoryMaintenanceAction(Base):
    __tablename__ = "memory_maintenance_actions"

    id = Column(String(64), primary_key=True)
    run_id = Column(String(64), ForeignKey("memory_maintenance_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(String(64), nullable=False, index=True)
    case_id = Column(String(64), ForeignKey("memory_work_cases.id", ondelete="SET NULL"), nullable=True, index=True)
    action = Column(String(32), nullable=False)  # merge | supersede | compact | purge | brief
    state = Column(String(24), nullable=False, default="completed")
    input_memory_ids = Column(JSON, nullable=False, default=list)
    input_event_ids = Column(JSON, nullable=False, default=list)
    output_memory_id = Column(String(64), nullable=True, index=True)
    evidence_seal_id = Column(String(64), ForeignKey("evidence_seals.id", ondelete="SET NULL"), nullable=True, index=True)
    reason_code = Column(String(96), nullable=False)
    details = Column(JSON, nullable=False, default=dict)
    idempotency_key = Column(String(96), nullable=False, unique=True, index=True)
    reversible_until = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class EvidenceSeal(Base):
    __tablename__ = "evidence_seals"

    id = Column(String(64), primary_key=True)
    user_id = Column(String(64), nullable=False, index=True)
    source_type = Column(String(32), nullable=False)
    source_event_id = Column(String(64), nullable=False, index=True)
    content_hash = Column(String(128), nullable=False, index=True)
    excerpt = Column(Text, nullable=True)
    occurred_at = Column(DateTime(timezone=True), nullable=True)
    sensitivity = Column(String(16), nullable=False, default="normal")
    seal_metadata = Column("metadata", JSON, nullable=False, default=dict)
    sealed_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (UniqueConstraint("user_id", "source_event_id", name="uq_evidence_seal_user_event"),)


class UserMemoryBrief(Base):
    __tablename__ = "user_memory_briefs"

    id = Column(String(64), primary_key=True)
    user_id = Column(String(64), nullable=False, unique=True, index=True)
    content = Column(Text, nullable=False)
    memory_ids = Column(JSON, nullable=False, default=list)
    source_revision = Column(String(64), nullable=False, index=True)
    generated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    expires_at = Column(DateTime(timezone=True), nullable=True)
    token_estimate = Column(Integer, nullable=False, default=0)
