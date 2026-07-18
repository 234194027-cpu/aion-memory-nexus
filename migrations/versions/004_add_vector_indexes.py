"""add vector indexes for pgvector

Revision ID: 004
Revises: 003
Create Date: 2026-06-30
"""
from typing import Sequence, Union
from alembic import op


revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    if conn.dialect.name != "postgresql":
        return

    # Enable pgvector extension
    try:
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"pgvector extension skipped: {e}")
        return

    # Check column types before creating indexes
    from sqlalchemy import inspect
    inspector = inspect(conn)

    # committed_memories.embedding - only create index if it's a vector type
    try:
        columns = {c["name"]: c for c in inspector.get_columns("committed_memories")}
        if "embedding" in columns:
            col_type = str(columns["embedding"]["type"]).lower()
            if "vector" in col_type:
                op.execute("""
                    CREATE INDEX IF NOT EXISTS ix_committed_embedding
                    ON committed_memories
                    USING hnsw (embedding vector_cosine_ops)
                """)
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"committed_memories index skipped: {e}")

    # memory_embeddings.embedding_vector - only create index if it's a vector type
    try:
        columns = {c["name"]: c for c in inspector.get_columns("memory_embeddings")}
        if "embedding_vector" in columns:
            col_type = str(columns["embedding_vector"]["type"]).lower()
            if "vector" in col_type:
                op.execute("""
                    CREATE INDEX IF NOT EXISTS ix_memory_embedding_vector
                    ON memory_embeddings
                    USING hnsw (embedding_vector vector_cosine_ops)
                """)
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"memory_embeddings index skipped: {e}")


def downgrade() -> None:
    conn = op.get_bind()
    if conn.dialect.name != "postgresql":
        return

    try:
        op.execute("DROP INDEX IF EXISTS ix_committed_embedding")
        op.execute("DROP INDEX IF EXISTS ix_memory_embedding_vector")
    except Exception:
        pass
