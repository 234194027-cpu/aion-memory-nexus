from sqlalchemy import Column, String, DateTime, Text, JSON, Enum, Integer, Index, func
from src.shared.db.database import Base
from enum import Enum as PyEnum

class ProcessingStatus(PyEnum):
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"

class SensitivityLevel(PyEnum):
    PUBLIC = "public"
    NORMAL = "normal"
    PRIVATE = "private"
    SENSITIVE = "sensitive"

class VisibilityScope(PyEnum):
    PUBLIC = "public"
    PROJECT = "project"
    PERSONAL = "personal"
    PRIVATE = "private"

class SourceType(PyEnum):
    MANUAL = "manual"
    CODEX = "codex"
    OPENCLAW = "openclaw"
    OBSIDIAN = "obsidian"
    CHATGPT = "chatgpt"
    FILE_IMPORT = "file_import"
    AGENT_API = "agent_api"
    CONVERSATION = "conversation"


class EpistemicStatus(PyEnum):
    LEGACY_UNCLASSIFIED = "legacy_unclassified"
    USER_ASSERTION = "user_assertion"
    USER_CONFIRMED = "user_confirmed"
    USER_IMPORTED = "user_imported"
    AGENT_ASSERTION = "agent_assertion"
    ASSISTANT_SUPPLIED = "assistant_supplied"
    MODEL_INFERENCE = "model_inference"
    EXTERNAL_CLAIM = "external_claim"

class RawEvent(Base):
    __tablename__ = "raw_events"

    __table_args__ = (
        Index("ix_raw_event_processing_lease", "processing_status", "processing_started_at"),
    )

    id = Column(String, primary_key=True, index=True)
    source_type = Column(Enum(SourceType, values_callable=lambda x: [e.value for e in x]), nullable=False)
    source_id = Column(String, nullable=True)
    agent_id = Column(String, nullable=True)
    user_id = Column(String, nullable=False)
    project_id = Column(String, nullable=True)
    repo_id = Column(String, nullable=True)
    workspace_id = Column(String, nullable=True)
    occurred_at = Column(DateTime(timezone=True), nullable=False)
    ingested_at = Column(DateTime(timezone=True), server_default=func.now())
    content = Column(Text, nullable=False)
    content_hash = Column(String, nullable=False)
    event_metadata = Column(JSON, default=dict)
    sensitivity = Column(Enum(SensitivityLevel, values_callable=lambda x: [e.value for e in x]), default=SensitivityLevel.NORMAL)
    visibility_scope = Column(Enum(VisibilityScope, values_callable=lambda x: [e.value for e in x]), default=VisibilityScope.PROJECT)
    processing_status = Column(Enum(ProcessingStatus, values_callable=lambda x: [e.value for e in x]), default=ProcessingStatus.QUEUED, index=True)
    processing_started_at = Column(DateTime(timezone=True), nullable=True)
    # Durable dispatch audit fields. RawEvent is the extraction outbox-equivalent:
    # it remains authoritative even if broker delivery or a worker process fails.
    processing_heartbeat_at = Column(DateTime(timezone=True), nullable=True)
    processing_next_retry_at = Column(DateTime(timezone=True), nullable=True)
    processing_attempts = Column(Integer, nullable=False, default=0, server_default="0")
    processing_error = Column(String(128), nullable=True)
    processing_result = Column(String(64), nullable=True)
