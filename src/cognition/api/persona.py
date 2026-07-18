import json
import logging
from datetime import datetime
from typing import Any, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.shared.db.database import get_db
from src.shared.security.dependencies import get_current_user
from src.memory.models.committed_memory import CommittedMemory, CommittedStatus
from src.cognition.models.persona_snapshot import PersonaSnapshot
from src.execution.models.user import User
from src.cognition.services.persona_engine import PersonaEngine
from src.cognition.schemas.governance import PersonaFeedbackRequest


logger = logging.getLogger(__name__)
router = APIRouter()


class PersonaRebuildRequest(BaseModel):
    max_memories: int = Field(default=200, ge=1, le=2000)
    top_k_by_type: int = Field(default=8, ge=1, le=50)
    mode: str = Field(default="full")


class PersonaTrait(BaseModel):
    category: str
    claim: str
    evidence_memory_ids: List[str]
    confidence: float


class PersonaResponse(BaseModel):
    user_id: str
    traits: Any  # v2.0: 可以是 List[dict] 或 Dict（含 decision_style 等）
    summary: str
    evidence_count: int = 0
    snapshot_id: Optional[str] = None
    snapshot_date: Optional[str] = None
    generated_at: str = ""
    embed_method: str = "keyword"


class PersonaWithStatsResponse(PersonaResponse):
    stats: dict = Field(default_factory=dict)


def _serialize_snapshot(record: PersonaSnapshot) -> dict:
    try:
        traits = json.loads(record.traits_json or "[]")
    except Exception:
        traits = []
    try:
        evidence_ids = json.loads(record.evidence_memory_ids or "[]")
    except Exception:
        evidence_ids = []

    return {
        "user_id": record.user_id,
        "traits": traits,
        "summary": record.summary or "",
        "evidence_count": len(evidence_ids) if isinstance(evidence_ids, list) else 0,
        "snapshot_id": record.id,
        "snapshot_date": record.snapshot_date,
        "generated_at": (record.created_at or datetime.utcnow()).isoformat(),
        "embed_method": record.embed_method or "keyword",
    }


def _empty_persona_response(user_id: str, *, include_stats: bool = False) -> dict:
    response = {
        "user_id": user_id,
        "traits": [],
        "summary": "",
        "evidence_count": 0,
        "snapshot_id": None,
        "snapshot_date": None,
        "generated_at": "",
        "embed_method": "keyword",
    }
    if include_stats:
        response["stats"] = {
            "today_memory_count": 0,
            "today_by_type": {},
            "snapshot_created_at": None,
            "snapshot_mode": None,
        }
    return response


async def _load_latest_snapshot(
    db: AsyncSession, user_id: str
) -> Optional[PersonaSnapshot]:
    result = await db.execute(
        select(PersonaSnapshot)
        .where(PersonaSnapshot.user_id == user_id)
        .order_by(PersonaSnapshot.created_at.desc())
        .limit(1)
    )
    return result.scalars().first()


async def _ensure_user_exists(db: AsyncSession, user_id: str) -> None:
    result = await db.execute(select(User).where(User.id == user_id))
    if result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="User not found")


@router.post("/rebuild", response_model=PersonaResponse)
async def rebuild_persona(
    request: PersonaRebuildRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if request.mode not in ("full", "incremental"):
        raise HTTPException(status_code=400, detail="mode must be 'full' or 'incremental'")

    await _ensure_user_exists(db, user.id)

    engine = PersonaEngine(db)
    result = await engine.build_persona(
        user_id=user.id,
        max_memories=request.max_memories,
        top_k_by_type=request.top_k_by_type,
    )
    return result


@router.get("/latest", response_model=PersonaResponse)
async def get_latest_persona(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    snapshot = await _load_latest_snapshot(db, user.id)
    if snapshot is None:
        return _empty_persona_response(user.id)
    return _serialize_snapshot(snapshot)


@router.get("", response_model=PersonaWithStatsResponse)
@router.get("/", response_model=PersonaWithStatsResponse, include_in_schema=False)
async def get_current_persona(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    snapshot = await _load_latest_snapshot(db, user.id)
    if snapshot is None:
        return _empty_persona_response(user.id, include_stats=True)

    base = _serialize_snapshot(snapshot)

    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    mem_result = await db.execute(
        select(CommittedMemory).where(
            and_(
                CommittedMemory.user_id == user.id,
                CommittedMemory.status == CommittedStatus.ACTIVE,
                CommittedMemory.created_at >= today_start,
            )
        )
    )
    today_memories = mem_result.scalars().all()
    type_counter = {}
    for m in today_memories:
        key = m.memory_type.value if m.memory_type else "unknown"
        type_counter[key] = type_counter.get(key, 0) + 1

    base["stats"] = {
        "today_memory_count": len(today_memories),
        "today_by_type": type_counter,
        "snapshot_created_at": (
            snapshot.created_at.isoformat() if snapshot.created_at else None
        ),
        "snapshot_mode": snapshot.mode,
    }
    return base


# ---------------------------------------------------------------------------
# POST /api/persona/feedback
# GET /api/persona/snapshots
# ---------------------------------------------------------------------------


@router.post("/feedback")
async def persona_feedback(
    request: PersonaFeedbackRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """用户对 persona 的反馈。"""
    result = await db.execute(
        select(PersonaSnapshot).where(
            PersonaSnapshot.id == request.snapshot_id,
            PersonaSnapshot.user_id == user.id,
        )
    )
    snapshot = result.scalar_one_or_none()
    if not snapshot:
        raise HTTPException(status_code=404, detail="Snapshot not found")

    feedback_type = request.feedback_type
    if feedback_type not in ("accurate", "inaccurate", "needs_update", "delete"):
        raise HTTPException(status_code=400, detail="Invalid feedback_type")

    if feedback_type == "delete":
        try:
            await db.delete(snapshot)
            await db.commit()
        except Exception as e:
            await db.rollback()
            logger.exception(f"persona_feedback delete failed: {e}")
            raise HTTPException(status_code=500, detail="delete_failed")
        return {"status": "deleted", "snapshot_id": request.snapshot_id}

    # 记录反馈到审计日志
    from src.execution.services.audit_logger import AuditLogger
    await AuditLogger.log(
        db,
        user_id=user.id,
        action="persona_feedback",
        actor_type="user",
        actor_id=user.id,
        target_type="persona_snapshot",
        target_id=request.snapshot_id,
        detail={
            "feedback_type": feedback_type,
            "comment": request.comment or "",
        },
    )

    if feedback_type == "needs_update":
        # 标记为需要重新生成（通过审计日志记录，实际重建由用户触发）
        pass

    return {
        "status": "recorded",
        "feedback_type": feedback_type,
        "snapshot_id": request.snapshot_id,
    }


@router.get("/snapshots")
async def list_persona_snapshots(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    limit: int = Query(default=20, ge=1, le=100),
):
    """历史 persona 快照列表。"""
    result = await db.execute(
        select(PersonaSnapshot)
        .where(PersonaSnapshot.user_id == user.id)
        .order_by(PersonaSnapshot.created_at.desc())
        .limit(limit)
    )
    snapshots = result.scalars().all()
    return [_serialize_snapshot(s) for s in snapshots]
