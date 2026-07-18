"""Decision Tracker (Gen 2).

跟踪用户决策的全生命周期:
- 创建决策 (track_decision)
- 补充实际结果 (update_outcome)
- 列出 open 决策 / 历史决策
- 从 DECISION 类 committed memory 自动建跟踪记录 (auto_track_from_committed_memory)

按白皮书第 5 节, Decision Tracker 与 Persona / Conflict / Rewriter 并列。
"""
from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.memory.models.committed_memory import CommittedMemory
from src.memory.models.memory_type import MemoryType
from src.cognition.models.decision_record import DecisionRecord
from src.shared.ids.id_generator import generate_decision_id


VALID_STATUSES = {"open", "resolved", "abandoned"}


def _ensure_utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


class DecisionTracker:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def track_decision(
        self,
        user_id: str,
        *,
        title: str,
        context: str,
        decision: str,
        rationale: str,
        expected_outcome: Optional[str] = None,
        project_id: Optional[str] = None,
        linked_memory_id: Optional[str] = None,
    ) -> DecisionRecord:
        if not title or not title.strip():
            raise ValueError("title is required")
        if not decision or not decision.strip():
            raise ValueError("decision is required")

        record = DecisionRecord(
            id=generate_decision_id(),
            user_id=user_id,
            title=title.strip(),
            context=context or "",
            decision=decision,
            rationale=rationale or "",
            expected_outcome=expected_outcome,
            actual_outcome=None,
            status="open",
            linked_memory_id=linked_memory_id,
            project_id=project_id,
            decided_at=datetime.now(timezone.utc),
            resolved_at=None,
            review_count=0,
        )
        self.db.add(record)
        await self.db.commit()
        await self.db.refresh(record)

        from src.execution.services.audit_logger import AuditLogger
        await AuditLogger.log(
            self.db,
            user_id=user_id,
            action="decision_track",
            actor_type="user",
            actor_id=user_id,
            target_type="decision",
            target_id=record.id,
            detail={"title": title, "project_id": project_id},
        )

        return record

    async def update_outcome(
        self,
        decision_id: str,
        actual_outcome: str,
        status: str = "resolved",
    ) -> DecisionRecord:
        if status not in VALID_STATUSES:
            raise ValueError(f"invalid status: {status}")

        result = await self.db.execute(
            select(DecisionRecord).where(DecisionRecord.id == decision_id)
        )
        record = result.scalar_one_or_none()
        if record is None:
            raise LookupError(f"decision not found: {decision_id}")

        record.actual_outcome = actual_outcome
        record.status = status
        record.resolved_at = datetime.now(timezone.utc)
        record.review_count = (record.review_count or 0) + 1
        record.updated_at = datetime.now(timezone.utc)

        await self.db.commit()
        await self.db.refresh(record)
        return record

    async def list_open_decisions(
        self,
        user_id: str,
        *,
        project_id: Optional[str] = None,
        limit: int = 20,
    ) -> List[DecisionRecord]:
        filters = [
            DecisionRecord.user_id == user_id,
            DecisionRecord.status == "open",
        ]
        if project_id:
            filters.append(DecisionRecord.project_id == project_id)
        result = await self.db.execute(
            select(DecisionRecord)
            .where(and_(*filters))
            .order_by(DecisionRecord.decided_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def history(
        self,
        user_id: str,
        *,
        project_id: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 50,
    ) -> List[DecisionRecord]:
        filters = [DecisionRecord.user_id == user_id]
        if project_id:
            filters.append(DecisionRecord.project_id == project_id)
        if status:
            if status not in VALID_STATUSES:
                raise ValueError(f"invalid status: {status}")
            filters.append(DecisionRecord.status == status)

        result = await self.db.execute(
            select(DecisionRecord)
            .where(and_(*filters))
            .order_by(DecisionRecord.decided_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def auto_track_from_committed_memory(
        self,
        user_id: str,
        memory_id: str,
    ) -> Optional[DecisionRecord]:
        """为一条 memory_type=DECISION 的 committed memory 自动建跟踪记录。

        - 如果已经为该 memory 建过跟踪, 返回 existing;
        - 否则从 memory.title / body 抽取 context, decision, rationale 字段建一条新的。
        """
        mem_result = await self.db.execute(
            select(CommittedMemory).where(CommittedMemory.id == memory_id)
        )
        memory = mem_result.scalar_one_or_none()
        if memory is None:
            return None
        if memory.user_id != user_id:
            raise PermissionError("memory does not belong to user")
        if memory.memory_type != MemoryType.DECISION:
            return None

        existing_result = await self.db.execute(
            select(DecisionRecord).where(
                and_(
                    DecisionRecord.user_id == user_id,
                    DecisionRecord.linked_memory_id == memory_id,
                )
            )
        )
        existing = existing_result.scalar_one_or_none()
        if existing is not None:
            return existing

        title = memory.title or "未命名决策"
        body = memory.body or ""
        decision_text = body.splitlines()[0] if body else title
        rationale = body if body else ""
        expected = memory.body if memory.body else None

        return await self.track_decision(
            user_id=user_id,
            title=title,
            context=memory.body or "",
            decision=decision_text,
            rationale=rationale,
            expected_outcome=expected,
            project_id=memory.project_id,
            linked_memory_id=memory.id,
        )
