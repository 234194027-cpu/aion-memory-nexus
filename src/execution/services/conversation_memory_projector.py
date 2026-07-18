"""Rebuild the conversational Agent's durable-memory cognitive mirror."""
from __future__ import annotations

from datetime import datetime, timezone
import logging
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.execution.runtime.workspace import AgentWorkspaceService
from src.memory.models.committed_memory import CommittedMemory, CommittedStatus
from src.memory.models.raw_event import SensitivityLevel, VisibilityScope


logger = logging.getLogger(__name__)


async def refresh_conversation_memory_projection(
    db: AsyncSession,
    *,
    user_id: str,
    limit: int = 20,
) -> dict[str, Any]:
    """Rebuild one user's bounded mirror from authoritative committed memories."""
    now = datetime.now(timezone.utc)
    memories = list((await db.execute(
        select(CommittedMemory)
        .where(
            CommittedMemory.user_id == user_id,
            CommittedMemory.status == CommittedStatus.ACTIVE,
            or_(
                CommittedMemory.valid_until.is_(None),
                CommittedMemory.valid_until > now,
            ),
            CommittedMemory.sensitivity.in_((
                SensitivityLevel.PUBLIC,
                SensitivityLevel.NORMAL,
            )),
            CommittedMemory.visibility_scope.in_((
                VisibilityScope.PUBLIC,
                VisibilityScope.PROJECT,
                VisibilityScope.PERSONAL,
            )),
        )
        .order_by(
            CommittedMemory.importance.desc(),
            func.coalesce(
                CommittedMemory.updated_at,
                CommittedMemory.created_at,
            ).desc(),
            CommittedMemory.created_at.desc(),
        )
        .limit(max(1, min(int(limit or 20), 50)))
    )).scalars())
    AgentWorkspaceService().project_formal_memory_digest(
        user_id=user_id,
        memories=memories,
        projected_at=now,
    )
    return {
        "status": "projected",
        "user_id": user_id,
        "item_count": len(memories),
        "projected_at": now.isoformat(),
    }


async def try_refresh_conversation_memory_projection(
    db: AsyncSession,
    *,
    user_id: str,
    limit: int = 20,
) -> dict[str, Any]:
    """Best-effort refresh that never turns projection failure into review failure."""
    try:
        return await refresh_conversation_memory_projection(
            db,
            user_id=user_id,
            limit=limit,
        )
    except Exception as exc:
        logger.warning(
            "Conversation memory projection failed user=%s error_type=%s",
            user_id,
            type(exc).__name__,
        )
        return {
            "status": "failed",
            "user_id": user_id,
            "item_count": 0,
            "error_type": type(exc).__name__,
        }


async def refresh_all_conversation_memory_projections(
    db: AsyncSession,
    *,
    limit_per_user: int = 20,
) -> dict[str, int]:
    """Compensate missing or stale projections for every known memory owner."""
    user_ids = [
        row[0]
        for row in (await db.execute(
            select(CommittedMemory.user_id)
            .where(CommittedMemory.user_id.is_not(None))
            .distinct()
        )).all()
        if row[0]
    ]
    succeeded = 0
    failed = 0
    for user_id in user_ids:
        result = await try_refresh_conversation_memory_projection(
            db,
            user_id=user_id,
            limit=limit_per_user,
        )
        if result["status"] == "projected":
            succeeded += 1
        else:
            failed += 1
    return {"users": len(user_ids), "succeeded": succeeded, "failed": failed}
