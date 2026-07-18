"""Owner-only operations for the disposable V3 graph projection."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.memory.models.graph_projection import GraphProjection, GraphProjectionStatus
from src.memory.services.graph_projection import (
    enqueue_replay_batch,
    graph_projection_status,
    trigger_graph_projection,
)
from src.shared.db.database import get_db
from src.shared.security.dependencies import get_graph_admin_user

router = APIRouter()


class ReplayRequest(BaseModel):
    batch_size: int = Field(default=50, ge=1, le=200)
    dry_run: bool = False
    reset: bool = False


@router.get("/status")
async def status(db: AsyncSession = Depends(get_db), user=Depends(get_graph_admin_user)):
    """Return counts only; graph content is never exposed from this endpoint."""
    return await graph_projection_status(db, user_id=user.id)


@router.post("/replay")
async def replay(
    request: ReplayRequest,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_graph_admin_user),
):
    result = await enqueue_replay_batch(
        db, user_id=user.id, batch_size=request.batch_size, dry_run=request.dry_run, reset=request.reset
    )
    if not request.dry_run:
        await db.commit()
    return result


@router.get("/failures")
async def failures(
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_graph_admin_user),
):
    rows = list(
        (
            await db.execute(
                select(GraphProjection)
                .where(
                    GraphProjection.user_id == user.id,
                    GraphProjection.status == GraphProjectionStatus.FAILED,
                )
                .order_by(GraphProjection.created_at.desc())
                .limit(max(1, min(limit, 200)))
            )
        ).scalars()
    )
    return {
        "items": [
            {
                "id": row.id,
                "source_kind": row.source_kind,
                "source_id": row.source_id,
                "operation": row.operation.value,
                "attempts": row.attempts,
                "error_code": row.error_code,
                "next_retry_at": row.next_retry_at,
            }
            for row in rows
        ]
    }


@router.post("/failures/{projection_id}/retry")
async def retry_failure(
    projection_id: str,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_graph_admin_user),
):
    row = await db.scalar(
        select(GraphProjection).where(
            GraphProjection.id == projection_id, GraphProjection.user_id == user.id
        )
    )
    if row is None:
        raise HTTPException(status_code=404, detail="projection_not_found")
    if row.status != GraphProjectionStatus.FAILED:
        raise HTTPException(status_code=409, detail="projection_not_failed")
    row.status = GraphProjectionStatus.QUEUED
    row.next_retry_at = None
    row.error_code = None
    await db.commit()
    trigger_graph_projection(row.id)
    return {"status": "queued", "projection_id": row.id}
