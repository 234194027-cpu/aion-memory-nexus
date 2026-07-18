"""记忆治理 API — 去重分析 / 冲突检查 / 合并 端点。

为前端 governance 页面提供:
- POST /api/governance/dedup-analysis  调用 MemoryDeduplicator.find_duplicates
- POST /api/governance/conflict-check   返回最近的 ConflictRecord 列表
- POST /api/governance/merge            接收 {primary_id, secondary_id} 调用 MemoryDeduplicator.merge
"""
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.shared.db.database import get_db
from src.shared.security.dependencies import get_current_user
from src.memory.services.deduplicator import MemoryDeduplicator
from src.cognition.models.conflict_record import ConflictRecord

logger = logging.getLogger(__name__)
router = APIRouter()


class DedupAnalysisRequest(BaseModel):
    memory_id: Optional[str] = None
    similarity_threshold: float = 0.85
    top_k: int = 20


class MergeRequest(BaseModel):
    primary_id: str
    secondary_id: str
    merged_body: Optional[str] = None


@router.post("/dedup-analysis")
async def dedup_analysis(
    request: DedupAnalysisRequest,
    db: AsyncSession = Depends(get_db),
    user = Depends(get_current_user),
):
    """查找用户库中相似度超过阈值的 memory 对。"""
    try:
        dedup = MemoryDeduplicator(db)
        pairs = await dedup.find_duplicates(
            user_id=user.id,
            memory_id=request.memory_id,
            similarity_threshold=request.similarity_threshold,
            top_k=request.top_k,
        )
        return {
            "status": "ok",
            "pairs": pairs,
            "scanned": len(pairs),
            "warnings": [],
        }
    except Exception as e:
        logger.exception(f"dedup_analysis failed: {e}")
        return {
            "status": "error",
            "pairs": [],
            "scanned": 0,
            "warnings": [f"server_error: {type(e).__name__}"],
        }


@router.post("/conflict-check")
async def conflict_check(
    request: dict,
    db: AsyncSession = Depends(get_db),
    user = Depends(get_current_user),
):
    """返回最近的 ConflictRecord 列表。"""
    try:
        limit = int(request.get("limit", 50)) if isinstance(request, dict) else 50
        limit = max(1, min(limit, 200))
        query = (
            select(ConflictRecord)
            .where(ConflictRecord.user_id == user.id)
            .order_by(ConflictRecord.created_at.desc())
            .limit(limit)
        )
        result = await db.execute(query)
        records = result.scalars().all()
        conflicts = [
            {
                "id": r.id,
                "conflict_type": r.conflict_type,
                "current_statement": r.current_statement,
                "past_statement": r.past_statement,
                "severity": r.severity,
                "interpretation": r.interpretation,
                "recommended_action": r.recommended_action,
                "confidence": r.confidence,
                "status": r.status,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in records
        ]
        return {
            "status": "ok",
            "conflicts": conflicts,
            "total": len(conflicts),
            "warnings": [] if conflicts else ["no_conflicts_found"],
        }
    except Exception as e:
        logger.exception(f"conflict_check failed: {e}")
        return {
            "status": "error",
            "conflicts": [],
            "total": 0,
            "warnings": [f"server_error: {type(e).__name__}"],
        }


@router.post("/merge")
async def merge_memories(
    request: MergeRequest,
    db: AsyncSession = Depends(get_db),
    user = Depends(get_current_user),
):
    """合并两条 memory: secondary 标记 SUPERSEDED, primary body 替换。"""
    if request.primary_id == request.secondary_id:
        raise HTTPException(status_code=400, detail="primary_id and secondary_id must be different")

    dedup = MemoryDeduplicator(db)
    try:
        merged_id = await dedup.merge(
            primary_memory_id=request.primary_id,
            secondary_memory_id=request.secondary_id,
            merged_body=request.merged_body,
            expected_user_id=user.id,
        )
    except LookupError as le:
        raise HTTPException(status_code=404, detail=str(le))
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        logger.exception(f"merge_memories failed: {e}")
        raise HTTPException(status_code=500, detail="merge_failed")

    return {
        "status": "merged",
        "primary_id": request.primary_id,
        "secondary_id": request.secondary_id,
        "merged_memory_id": merged_id,
    }
