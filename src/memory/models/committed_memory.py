from sqlalchemy import Column, String, DateTime, Text, JSON, Float, Enum, func, ForeignKey, Index, Integer
from src.shared.db.database import Base
from enum import Enum as PyEnum
from src.memory.models.raw_event import EpistemicStatus, SensitivityLevel, VisibilityScope
from src.memory.models.memory_type import MemoryType
from src.shared.db.vector_store import get_embedding_column

class CommittedStatus(PyEnum):
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    EXPIRED = "expired"
    REVOKED = "revoked"
    DELETED = "deleted"

class CommittedMemory(Base):
    __tablename__ = "committed_memories"

    id = Column(String, primary_key=True, index=True)
    source_work_case_id = Column(
        String(64), ForeignKey("memory_work_cases.id", ondelete="SET NULL"), nullable=True, index=True
    )
    source_work_decision_id = Column(
        String(64), ForeignKey("memory_work_decisions.id", ondelete="SET NULL"), nullable=True, unique=True, index=True
    )
    origin_kind = Column(String(32), nullable=False, default="working_agent")
    revision = Column(Integer, nullable=False, default=1)
    automation_metadata = Column(JSON, nullable=False, default=dict)
    user_id = Column(String, nullable=False)
    project_id = Column(String, nullable=True, index=True)
    repo_id = Column(String, nullable=True, index=True)
    workspace_id = Column(String, nullable=True)
    memory_type = Column(Enum(MemoryType, values_callable=lambda x: [e.value for e in x]), nullable=False)
    title = Column(String, nullable=False)
    body = Column(Text, nullable=False)
    confidence = Column(Float, default=0.0)
    importance = Column(Float, default=0.0)
    sensitivity = Column(Enum(SensitivityLevel, values_callable=lambda x: [e.value for e in x]), default=SensitivityLevel.NORMAL)
    epistemic_status = Column(String(32), nullable=False, default=EpistemicStatus.LEGACY_UNCLASSIFIED.value)
    visibility_scope = Column(Enum(VisibilityScope, values_callable=lambda x: [e.value for e in x]), default=VisibilityScope.PROJECT)
    status = Column(
        Enum(CommittedStatus, values_callable=lambda x: [e.value for e in x]),
        default=CommittedStatus.ACTIVE,
    )
    valid_from = Column(DateTime(timezone=True), nullable=False)
    valid_until = Column(DateTime(timezone=True), nullable=True)
    tags = Column(JSON, default=[])
    embedding = get_embedding_column(1024)
    content_hash = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    last_accessed_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_user_status_type", "user_id", "status", "memory_type"),
        Index("ix_user_importance", "user_id", "importance"),
        Index("ix_user_valid_from", "user_id", "valid_from"),
        Index("ix_content_hash_unique", "content_hash", unique=True),
    )
