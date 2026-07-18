"""Unified read-only view of unfinished user work; source records remain authoritative."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.cognition.models.conflict_record import ConflictRecord
from src.cognition.models.decision_record import DecisionRecord
from src.execution.models.agent_runtime import AgentHandoff, AgentHandoffStatus
from src.execution.models.life_task import LifeTask


@dataclass(frozen=True, slots=True)
class OpenLoop:
    source_type: str
    source_id: str
    title: str
    next_step: str
    priority: int
    due_at: datetime | None = None


class OpenLoopService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def list(self, *, user_id: str, limit: int = 50) -> list[OpenLoop]:
        handoffs, tasks, conflicts, decisions = await self._load(user_id)
        loops = [
            OpenLoop("handoff", item.id, "需要补充证据", item.question, item.priority, None)
            for item in handoffs
        ]
        loops.extend(
            OpenLoop("task", item.id, item.title or "未命名待办", "确认进展或下一步", int((item.priority_score or 0.0) * 10), item.due_at)
            for item in tasks
        )
        loops.extend(
            OpenLoop("conflict", item.id, f"待确认冲突：{item.conflict_type}", item.recommended_action or "review", {"high": 10, "medium": 5}.get(item.severity, 1), None)
            for item in conflicts
        )
        loops.extend(
            OpenLoop("decision", item.id, item.title or "未命名决策", "补充结果或保持开放", 4, None)
            for item in decisions
        )
        return sorted(loops, key=lambda item: (-item.priority, item.due_at or datetime.max))[:limit]

    async def _load(self, user_id: str):
        handoff_result = await self.db.execute(select(AgentHandoff).where(AgentHandoff.user_id == user_id, AgentHandoff.mode == "active", AgentHandoff.status == AgentHandoffStatus.ACTIVE))
        task_result = await self.db.execute(select(LifeTask).where(LifeTask.user_id == user_id, LifeTask.status.notin_(["completed", "cancelled"])))
        conflict_result = await self.db.execute(select(ConflictRecord).where(ConflictRecord.user_id == user_id, ConflictRecord.status.in_(["open", "acknowledged"])))
        decision_result = await self.db.execute(select(DecisionRecord).where(DecisionRecord.user_id == user_id, DecisionRecord.status == "open"))
        handoffs = list(handoff_result.scalars())
        tasks = list(task_result.scalars())
        conflicts = list(conflict_result.scalars())
        decisions = list(decision_result.scalars())
        return handoffs, tasks, conflicts, decisions
