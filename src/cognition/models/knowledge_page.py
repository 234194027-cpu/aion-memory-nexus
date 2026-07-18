"""Persisted, source-backed Wiki topics derived from committed memories."""

from sqlalchemy import Column, DateTime, Float, ForeignKey, Index, Integer, String, Text, func

from src.shared.db.database import Base


class KnowledgePage(Base):
    __tablename__ = "knowledge_pages"

    id = Column(String(64), primary_key=True)
    user_id = Column(String(64), nullable=False, index=True)
    slug = Column(String(160), nullable=False)
    title = Column(String(255), nullable=False)
    summary = Column(Text, nullable=False, default="")
    confidence = Column(Float, nullable=False, default=0.0)
    source_count = Column(Integer, nullable=False, default=0)
    status = Column(String(32), nullable=False, default="active")
    generated_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    __table_args__ = (
        Index("uq_knowledge_pages_user_slug", "user_id", "slug", unique=True),
        Index("ix_knowledge_pages_user_status", "user_id", "status"),
    )


class KnowledgePageMemory(Base):
    __tablename__ = "knowledge_page_memories"

    id = Column(String(64), primary_key=True)
    user_id = Column(String(64), nullable=False, index=True)
    page_id = Column(String(64), ForeignKey("knowledge_pages.id", ondelete="CASCADE"), nullable=False)
    memory_id = Column(String(64), ForeignKey("committed_memories.id"), nullable=False)
    relation_basis = Column(String(32), nullable=False)
    confidence = Column(Float, nullable=False, default=0.0)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        Index("uq_knowledge_page_memories_page_memory", "page_id", "memory_id", unique=True),
        Index("ix_knowledge_page_memories_user_memory", "user_id", "memory_id"),
    )


class KnowledgePageVersion(Base):
    """Derived Wiki state snapshots; no raw memory body is duplicated here."""

    __tablename__ = "knowledge_page_versions"

    id = Column(String(64), primary_key=True)
    user_id = Column(String(64), nullable=False, index=True)
    page_id = Column(String(64), nullable=False, index=True)
    slug = Column(String(160), nullable=False)
    title = Column(String(255), nullable=False)
    summary = Column(Text, nullable=False, default="")
    confidence = Column(Float, nullable=False, default=0.0)
    source_count = Column(Integer, nullable=False, default=0)
    memory_ids = Column(String, nullable=False, default="[]")
    change_reason = Column(String(64), nullable=False)
    generated_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_knowledge_page_versions_user_page_created", "user_id", "page_id", "created_at"),
        Index("ix_knowledge_page_versions_user_slug", "user_id", "slug"),
    )
