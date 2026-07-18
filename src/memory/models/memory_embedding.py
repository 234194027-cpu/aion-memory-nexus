from sqlalchemy import Column, String, DateTime, Text, Integer, ForeignKey, func, UniqueConstraint, Index
from src.shared.db.database import Base
from src.shared.db.vector_store import get_embedding_column


class MemoryEmbedding(Base):
    __tablename__ = "memory_embeddings"

    id = Column(String, primary_key=True, index=True)
    memory_id = Column(String, ForeignKey("committed_memories.id"), nullable=False, index=True)
    embedding_model = Column(String, nullable=False, default="default")
    embedding_vector = get_embedding_column(1024)
    content_snapshot = Column(Text, nullable=False)
    dimension = Column(Integer, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("memory_id", "embedding_model", name="uq_memory_model"),
        Index("ix_embedding_memory_model", "memory_id", "embedding_model"),
    )
