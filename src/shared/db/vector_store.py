"""
Vector storage abstraction: pgvector for PostgreSQL, JSON fallback for SQLite.
"""
import logging
from typing import List
from sqlalchemy import Column, JSON, select, and_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import SQLAlchemyError
from src.shared.config import settings
from src.shared.utils.runtime_metrics import runtime_metrics

logger = logging.getLogger(__name__)

# Try to import pgvector, fallback to JSON for SQLite
try:
    from pgvector.sqlalchemy import Vector
    PGVECTOR_AVAILABLE = True
except ImportError:
    PGVECTOR_AVAILABLE = False
    logger.info("pgvector not available, using JSON fallback for embeddings")


def get_embedding_column(dimension: int = 1024):
    """Get appropriate embedding column type based on database backend."""
    if not settings.POSTGRES_URL.startswith("sqlite") and PGVECTOR_AVAILABLE:
        return Column(Vector(dimension), nullable=True)
    else:
        return Column(JSON, nullable=True)


def vector_store_mode() -> str:
    """Return a safe capability label for health checks, never a connection URL."""
    if settings.POSTGRES_URL.startswith("sqlite"):
        return "sqlite_json_fallback"
    return "postgres_pgvector" if PGVECTOR_AVAILABLE else "postgres_json_fallback"


async def vector_similarity_search(
    db: AsyncSession,
    table,
    query_vector: List[float],
    user_id: str,
    filter_conditions: list,
    top_k: int = 20,
    embedding_column_name: str = "embedding_vector",
    id_column_name: str = "id",
) -> list:
    """
    Perform vector similarity search.
    Uses pgvector operators if available, otherwise falls back to Python computation.
    """
    if not settings.POSTGRES_URL.startswith("sqlite") and PGVECTOR_AVAILABLE:
        # Use pgvector cosine distance operator <=>
        try:
            # Build query with pgvector
            query = select(table).where(and_(*filter_conditions))

            # Add vector similarity ordering
            embedding_col = getattr(table, embedding_column_name)
            query = query.order_by(embedding_col.cosine_distance(query_vector)).limit(top_k)

            result = await db.execute(query)
            runtime_metrics.record_task("vector_search")
            return result.scalars().all()
        except SQLAlchemyError as exc:
            # Some deployed databases still have JSON embedding columns from
            # earlier migrations. In that case PostgreSQL raises
            # "operator does not exist: json <=> unknown". Roll back the
            # failed read transaction and use the portable Python fallback.
            logger.warning("pgvector search failed; falling back to Python similarity: %s", exc)
            runtime_metrics.record_task("vector_search", failed=True)
            await db.rollback()

    results = await _python_similarity_search(
        db=db,
        table=table,
        query_vector=query_vector,
        filter_conditions=filter_conditions,
        top_k=top_k,
        embedding_column_name=embedding_column_name,
    )
    runtime_metrics.record_task("vector_search")
    return results


async def _python_similarity_search(
    db: AsyncSession,
    table,
    query_vector: List[float],
    filter_conditions: list,
    top_k: int,
    embedding_column_name: str,
) -> list:
    """Portable vector fallback for SQLite or legacy JSON-vector schemas."""
    query = select(table).where(and_(*filter_conditions))
    result = await db.execute(query)
    all_items = result.scalars().all()

    if not all_items:
        return []

    # Compute similarity in Python
    from src.memory.services.retrieval_engine import cosine_similarity_batch

    vectors = []
    valid_items = []
    for item in all_items:
        emb = getattr(item, embedding_column_name, None)
        if emb:
            vectors.append(emb)
            valid_items.append(item)

    if not vectors:
        return []

    similarities = cosine_similarity_batch(query_vector, vectors)

    # Sort by similarity
    items_with_scores = list(zip(valid_items, similarities))
    items_with_scores.sort(key=lambda x: x[1], reverse=True)

    return [item for item, _ in items_with_scores[:top_k]]


async def create_vector_index_if_needed(db: AsyncSession, table_name: str, column_name: str, dimension: int = 1024):
    """Create HNSW index for vector column if using pgvector."""
    if settings.POSTGRES_URL.startswith("sqlite") or not PGVECTOR_AVAILABLE:
        return
    
    try:
        # Check if index exists
        check_query = f"""
        SELECT indexname FROM pg_indexes 
        WHERE tablename = '{table_name}' AND indexname LIKE '%{column_name}_idx%'
        """
        result = await db.execute(check_query)
        if result.scalar():
            return
        
        # Create HNSW index
        create_query = f"""
        CREATE INDEX IF NOT EXISTS {column_name}_idx 
        ON {table_name} 
        USING hnsw ({column_name} vector_cosine_ops)
        """
        await db.execute(create_query)
        runtime_metrics.record_task("vector_index_create")
        logger.info(f"Created HNSW index on {table_name}.{column_name}")
    except Exception as e:
        runtime_metrics.record_task("vector_index_create", failed=True)
        logger.warning(f"Failed to create vector index: {e}")
