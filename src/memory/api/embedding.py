"""Embedding System API — TASK 4.

POST /api/memory/embedding/backfill

Generates embeddings for committed memories that lack them.
Embedding failure MUST NOT block the system; fallback to keyword search.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from src.shared.db.database import get_db
from src.memory.models.committed_memory import CommittedMemory, CommittedStatus
from src.memory.models.memory_embedding import MemoryEmbedding
from src.memory.schemas.ingest import EmbeddingBackfillRequest, EmbeddingBackfillResponse
from src.shared.security.dependencies import get_current_user
from src.memory.tasks.memory_extraction import generate_embedding_for_memory_with_retry

router = APIRouter()


@router.post("/memory/embedding/backfill", response_model=EmbeddingBackfillResponse)
async def backfill_embeddings(
    request: EmbeddingBackfillRequest = None,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """Generate embeddings for committed memories that don't have one yet.

    Only processes active memories owned by the current user.
    Fails gracefully — embedding errors do NOT block the system.
    """
    if request is None:
        request = EmbeddingBackfillRequest()

    # Count total active memories
    total_q = (
        select(func.count(CommittedMemory.id))
        .where(CommittedMemory.user_id == user.id)
        .where(CommittedMemory.status == CommittedStatus.ACTIVE)
    )
    total = (await db.execute(total_q)).scalar() or 0

    # Find memories without embeddings
    embedded_ids_q = select(MemoryEmbedding.memory_id)
    embedded_ids_result = await db.execute(embedded_ids_q)
    embedded_ids = {row[0] for row in embedded_ids_result.all()}

    pending_q = (
        select(CommittedMemory.id)
        .where(CommittedMemory.user_id == user.id)
        .where(CommittedMemory.status == CommittedStatus.ACTIVE)
    )
    if embedded_ids:
        pending_q = pending_q.where(CommittedMemory.id.notin_(embedded_ids))
    pending_q = pending_q.limit(request.batch_size)

    pending_result = await db.execute(pending_q)
    pending_ids = [row[0] for row in pending_result.all()]

    total_pending = total - len(embedded_ids)

    # Process
    success = 0
    failed = 0
    for mid in pending_ids:
        try:
            ok = await generate_embedding_for_memory_with_retry(db, mid)
            if ok:
                success += 1
            else:
                failed += 1
        except Exception:
            failed += 1

    return EmbeddingBackfillResponse(
        total_pending=max(total_pending, 0),
        processed=len(pending_ids),
        success=success,
        failed=failed,
    )
