"""Weekly Review API (P1).

周期性周报的 HTTP 端点:
- POST /api/weekly-reviews/generate    生成周报 (默认 dry_run=True)
- GET  /api/weekly-reviews/latest      获取最新周报
- GET  /api/weekly-reviews/history     获取历史周报列表
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from src.shared.db.database import get_db
from src.shared.security.dependencies import get_current_user
from src.cognition.schemas.governance import WeeklyReviewRequest, WeeklyReviewResponse
from src.cognition.services.weekly_review import WeeklyReviewService

logger = logging.getLogger(__name__)
router = APIRouter()


def _review_to_response(record_dict: dict) -> WeeklyReviewResponse:
    return WeeklyReviewResponse(
        id=record_dict.get("id"),
        user_id=record_dict.get("user_id", ""),
        week_start=record_dict.get("week_start", ""),
        week_end=record_dict.get("week_end", ""),
        summary=record_dict.get("summary", ""),
        key_decisions=record_dict.get("key_decisions", []) or [],
        important_insights=record_dict.get("important_insights", []) or [],
        repeated_themes=record_dict.get("repeated_themes", []) or [],
        conflicts_or_changes=record_dict.get("conflicts_or_changes", []) or [],
        risks_to_watch=record_dict.get("risks_to_watch", []) or [],
        suggested_focus_next_week=record_dict.get("suggested_focus_next_week", []) or [],
        persona_observations=record_dict.get("persona_observations", []) or [],
        open_loops=record_dict.get("open_loops", []) or [],
        cited_memories=record_dict.get("cited_memories", []) or [],
        cited_decisions=record_dict.get("cited_decisions", []) or [],
        new_memories_count=int(record_dict.get("new_memories_count", 0)),
        decisions_count=int(record_dict.get("decisions_count", 0)),
        word_count=int(record_dict.get("word_count", 0)),
        warnings=record_dict.get("warnings", []) or [],
        persisted=bool(record_dict.get("persisted", False)),
        generated_at=datetime.now(timezone.utc).isoformat(),
    )


@router.post("/weekly-reviews/generate", response_model=WeeklyReviewResponse)
async def generate_weekly_review(
    request: WeeklyReviewRequest,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """生成周报 (默认 dry_run=True 不持久化)。"""
    service = WeeklyReviewService(db)
    try:
        result = await service.generate(
            user_id=user.id,
            week_start=request.week_start,
            dry_run=request.dry_run,
        )
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        logger.exception(f"generate_weekly_review failed: {e}")
        return WeeklyReviewResponse(
            user_id=user.id,
            week_start=request.week_start or "",
            week_end="",
            summary=f"周报生成失败: {e}",
            warnings=[f"server_error: {e}"],
            generated_at=datetime.now(timezone.utc).isoformat(),
        )
    return _review_to_response(result)


@router.get("/weekly-reviews/latest", response_model=WeeklyReviewResponse)
async def get_latest_weekly_review(
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """获取最新一条周报记录。"""
    service = WeeklyReviewService(db)
    record = await service.latest(user_id=user.id)
    if record is None:
        raise HTTPException(status_code=404, detail="no_weekly_review_found")

    return WeeklyReviewResponse(
        id=record.id,
        user_id=record.user_id,
        week_start=record.week_start,
        week_end=record.week_end,
        summary=record.summary or "",
        word_count=int(record.word_count or 0),
        generated_at=record.created_at.isoformat() if record.created_at else None,
    )


@router.get("/weekly-reviews/history")
async def get_weekly_review_history(
    limit: int = Query(12, ge=1, le=52),
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """获取历史周报列表 (默认 12 条)。"""
    service = WeeklyReviewService(db)
    records = await service.history(user_id=user.id, limit=limit)
    return {
        "user_id": user.id,
        "count": len(records),
        "items": [
            {
                "id": r.id,
                "week_start": r.week_start,
                "week_end": r.week_end,
                "summary": r.summary or "",
                "word_count": int(r.word_count or 0),
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in records
        ],
    }