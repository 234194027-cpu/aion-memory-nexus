"""Durable, rebuildable projection work for the internal Graphiti adapter.

This table is intentionally metadata-only.  The PostgreSQL memory ledger stays
authoritative; workers re-read a source object immediately before projecting it.
"""

from __future__ import annotations

import hashlib
from enum import Enum as PyEnum

from sqlalchemy import Column, DateTime, Enum, Float, Index, Integer, JSON, String, UniqueConstraint, func

from src.shared.db.database import Base


class GraphProjectionOperation(PyEnum):
    UPSERT = "upsert"
    DELETE = "delete"


class GraphProjectionStatus(PyEnum):
    QUEUED = "queued"
    PROCESSING = "processing"
    PROJECTED = "projected"
    FAILED = "failed"


def projection_key(
    source_kind: str,
    source_id: str,
    source_revision: str,
    operation: GraphProjectionOperation,
) -> str:
    """Return a deterministic idempotency key without exposing source content."""
    raw = f"{source_kind}:{source_id}:{source_revision}:{operation.value}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class GraphProjection(Base):
    __tablename__ = "graph_projections"

    id = Column(String(64), primary_key=True)
    projection_key = Column(String(64), nullable=False, unique=True, index=True)
    user_id = Column(String(64), nullable=False, index=True)
    project_id = Column(String(64), nullable=True, index=True)
    source_kind = Column(String(32), nullable=False)
    source_id = Column(String(64), nullable=False)
    source_revision = Column(String(128), nullable=False)
    operation = Column(
        Enum(GraphProjectionOperation, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
    )
    status = Column(
        Enum(GraphProjectionStatus, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
        default=GraphProjectionStatus.QUEUED,
        index=True,
    )
    # Scope/reference metadata only; never persist event or memory prose here.
    projection_metadata = Column(JSON, nullable=False, default=dict)
    attempts = Column(Integer, nullable=False, default=0, server_default="0")
    lease_started_at = Column(DateTime(timezone=True), nullable=True)
    next_retry_at = Column(DateTime(timezone=True), nullable=True)
    error_code = Column(String(128), nullable=True)
    projected_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=True, onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("source_kind", "source_id", "source_revision", "operation", name="uq_graph_projection_source_revision"),
        Index("ix_graph_projection_dispatch", "status", "next_retry_at", "lease_started_at"),
        Index("ix_graph_projection_source", "user_id", "source_kind", "source_id"),
    )


class GraphReplayCheckpoint(Base):
    """Per-user durable cursor for ordered, resumable historical projection."""

    __tablename__ = "graph_replay_checkpoints"

    id = Column(String(64), primary_key=True)
    user_id = Column(String(64), nullable=False)
    source_kind = Column(String(32), nullable=False)
    cursor_occurred_at = Column(DateTime(timezone=True), nullable=True)
    cursor_source_id = Column(String(64), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    scanned_count = Column(Integer, nullable=False, default=0, server_default="0")
    queued_count = Column(Integer, nullable=False, default=0, server_default="0")
    updated_at = Column(DateTime(timezone=True), nullable=True, onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("user_id", "source_kind", name="uq_graph_replay_checkpoint_user_source"),
        Index("ix_graph_replay_checkpoint_user", "user_id", "source_kind"),
    )


class GraphShadowObservation(Base):
    """Content-free retrieval comparison used before Graphiti can be activated."""

    __tablename__ = "graph_shadow_observations"

    id = Column(String(64), primary_key=True)
    user_id = Column(String(64), nullable=False, index=True)
    query_hash = Column(String(64), nullable=False, index=True)
    baseline_memory_ids = Column(JSON, nullable=False, default=list)
    graph_memory_ids = Column(JSON, nullable=False, default=list)
    graph_relation_count = Column(Integer, nullable=False, default=0, server_default="0")
    novel_verified_count = Column(Integer, nullable=False, default=0, server_default="0")
    source_coverage = Column(Float, nullable=False, default=0.0, server_default="0")
    graph_latency_ms = Column(Integer, nullable=False, default=0, server_default="0")
    token_used = Column(Integer, nullable=False, default=0, server_default="0")
    mode = Column(String(16), nullable=False, default="shadow", server_default="shadow")
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        Index("ix_graph_shadow_user_created", "user_id", "created_at"),
    )
