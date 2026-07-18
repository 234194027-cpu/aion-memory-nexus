"""Read-only attention candidate selection; it never sends a message or owns business data."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.execution.models.agent_runtime import AgentHandoff, AgentHandoffStatus
from src.execution.models.life_task import LifeTask


@dataclass(frozen=True, slots=True)
class AttentionCandidate:
    source_type: str
    source_id: str
    priority: int
    prompt: str


class AttentionService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def list_candidates(self, *, user_id: str, limit: int = 10) -> list[AttentionCandidate]:
        handoffs = list((await self.db.execute(
            select(AgentHandoff)
            .where(
                AgentHandoff.user_id == user_id,
                AgentHandoff.mode == "active",
                AgentHandoff.status == AgentHandoffStatus.ACTIVE,
            )
            .order_by(AgentHandoff.priority.desc(), AgentHandoff.created_at.asc())
        )).scalars())
        now_aware = datetime.now(timezone.utc)
        active_handoffs = []
        for handoff in handoffs:
            expires_at = handoff.expires_at
            if expires_at is not None:
                comparable_expiry = expires_at if expires_at.tzinfo else expires_at.replace(tzinfo=timezone.utc)
                if comparable_expiry <= now_aware:
                    handoff.status = AgentHandoffStatus.EXPIRED
                    continue
            active_handoffs.append(handoff)
        await self.db.flush()
        output = [
            AttentionCandidate("handoff", handoff.id, handoff.priority, handoff.question)
            for handoff in active_handoffs[:limit]
        ]
        if len(output) >= limit:
            return output
        task_rows = list((await self.db.execute(
            select(LifeTask)
            .where(LifeTask.user_id == user_id, LifeTask.status.notin_(["completed", "cancelled"]))
            .order_by(LifeTask.priority_score.desc(), LifeTask.due_at.asc().nullslast())
            .limit(limit - len(output))
        )).scalars())
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        for task in task_rows:
            if task.due_at and task.due_at < now:
                prompt = f"关于待办“{task.title}”，它现在的进展或下一步是什么？"
                output.append(AttentionCandidate("task", task.id, 1, prompt))
        return output[:limit]
