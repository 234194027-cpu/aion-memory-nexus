"""Decision Tracker API (P1).

Gen 2 决策追踪的 HTTP 端点:
- POST /api/decisions                 新建决策
- GET  /api/decisions/open            列出 open 决策
- GET  /api/decisions/history         历史 (可按 status / project_id 过滤)
- PATCH /api/decisions/{id}/outcome   补充实际结果并切换状态
- POST /api/decisions/auto-track      从 DECISION 类 memory 自动建跟踪记录
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from src.shared.db.database import get_db
from src.shared.security.dependencies import get_current_user
from src.cognition.models.decision_record import DecisionRecord
from src.cognition.schemas.governance import (
    DecisionAutoTrackRequest,
    DecisionResponse,
    DecisionTrackRequest,
    DecisionUpdateRequest,
)
from src.cognition.services.decision_tracker import DecisionTracker

logger = logging.getLogger(__name__)
router = APIRouter()


def _decision_to_response(d: DecisionRecord) -> DecisionResponse:
    return DecisionResponse(
        id=d.id,
        user_id=d.user_id,
        title=d.title,
        context=d.context or "",
        decision=d.decision,
        rationale=d.rationale or "",
        expected_outcome=d.expected_outcome,
        actual_outcome=d.actual_outcome,
        status=d.status,
        linked_memory_id=d.linked_memory_id,
        project_id=d.project_id,
        decided_at=d.decided_at.isoformat() if d.decided_at else None,
        resolved_at=d.resolved_at.isoformat() if d.resolved_at else None,
        review_count=int(d.review_count or 0),
        confidence=float(getattr(d, "confidence", 0.5) or 0.5),
        importance=float(getattr(d, "importance", 0.5) or 0.5),
        decision_type=getattr(d, "decision_type", "other") or "other",
    )


@router.post("/decisions", response_model=DecisionResponse)
async def create_decision(
    request: DecisionTrackRequest,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """新建决策。"""
    tracker = DecisionTracker(db)
    try:
        record = await tracker.track_decision(
            user_id=user.id,
            title=request.title,
            context=request.context,
            decision=request.decision,
            rationale=request.rationale,
            expected_outcome=request.expected_outcome,
            project_id=request.project_id,
            linked_memory_id=request.linked_memory_id,
        )
        return _decision_to_response(record)
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        logger.exception(f"create_decision failed: {e}")
        raise HTTPException(status_code=500, detail=f"create_failed: {e}")


@router.get("/decisions/open", response_model=list[DecisionResponse])
async def list_open_decisions(
    project_id: Optional[str] = Query(None),
    limit: int = Query(20, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """列出 open 状态的决策。"""
    tracker = DecisionTracker(db)
    records = await tracker.list_open_decisions(
        user_id=user.id,
        project_id=project_id,
        limit=limit,
    )
    return [_decision_to_response(r) for r in records]


@router.get("/decisions/history", response_model=list[DecisionResponse])
async def list_decision_history(
    project_id: Optional[str] = Query(None),
    status: Optional[str] = Query(None, description="open | resolved | abandoned"),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """历史决策 (可按 status / project_id 过滤)。"""
    tracker = DecisionTracker(db)
    try:
        records = await tracker.history(
            user_id=user.id,
            project_id=project_id,
            status=status,
            limit=limit,
        )
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    return [_decision_to_response(r) for r in records]


@router.patch("/decisions/{decision_id}/outcome", response_model=DecisionResponse)
async def update_decision_outcome(
    decision_id: str,
    request: DecisionUpdateRequest,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """补充实际结果并切换状态。"""
    tracker = DecisionTracker(db)
    try:
        record = await tracker.update_outcome(
            decision_id=decision_id,
            actual_outcome=request.actual_outcome,
            status=request.status,
        )
    except LookupError as le:
        raise HTTPException(status_code=404, detail=str(le))
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))

    if record.user_id != user.id:
        raise HTTPException(status_code=403, detail="not_authorized_for_decision")

    return _decision_to_response(record)


@router.post("/decisions/auto-track", response_model=DecisionResponse)
async def auto_track_decision(
    request: DecisionAutoTrackRequest,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """从一条 DECISION 类 committed memory 自动建跟踪记录。"""
    tracker = DecisionTracker(db)
    try:
        record = await tracker.auto_track_from_committed_memory(
            user_id=user.id,
            memory_id=request.memory_id,
        )
    except PermissionError as pe:
        raise HTTPException(status_code=403, detail=str(pe))
    except Exception as e:
        logger.exception(f"auto_track_decision failed: {e}")
        raise HTTPException(status_code=500, detail=f"auto_track_failed: {e}")

    if record is None:
        raise HTTPException(
            status_code=404,
            detail=f"memory_not_found_or_not_decision_type: {request.memory_id}",
        )
    return _decision_to_response(record)