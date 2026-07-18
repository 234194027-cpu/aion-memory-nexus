"""Low-risk reflective proposals from confirmed memories; never promotes an insight to fact."""
from __future__ import annotations

from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.cognition.models.insight_proposal import InsightProposal
from src.execution.models.audit_log import AuditLog
from src.memory.models.committed_memory import CommittedMemory, CommittedStatus
from src.shared.ids.id_generator import generate_audit_log_id, generate_id


class ReflectionService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def refresh(self, *, user_id: str, limit: int = 5) -> list[InsightProposal]:
        memories = list((await self.db.execute(
            select(CommittedMemory)
            .where(CommittedMemory.user_id == user_id, CommittedMemory.status == CommittedStatus.ACTIVE)
            .order_by(CommittedMemory.updated_at.desc().nullslast(), CommittedMemory.created_at.desc())
            .limit(100)
        )).scalars())
        by_tag: dict[str, list[CommittedMemory]] = defaultdict(list)
        for memory in memories:
            for tag in memory.tags or []:
                if isinstance(tag, str) and tag.strip():
                    by_tag[tag.strip().lower()[:80]].append(memory)
        proposals: list[InsightProposal] = []
        for tag, related in sorted(by_tag.items(), key=lambda item: len(item[1]), reverse=True):
            if len(related) < 2 or len(proposals) >= limit:
                continue
            source_key = f"repeated-tag:{tag}"
            existing = (await self.db.execute(
                select(InsightProposal).where(InsightProposal.user_id == user_id, InsightProposal.source_key == source_key)
            )).scalar_one_or_none()
            support_ids = [memory.id for memory in related[:10]]
            if existing is not None:
                if existing.status == "proposed":
                    existing.support_memory_ids = support_ids
                    existing.summary = f"近期有 {len(related)} 条已确认记忆关联“{tag}”。这是一条待验证的重复主题，不等于用户固定特质。"
                    existing.confidence = min(0.75, 0.25 + len(related) * 0.08)
                proposals.append(existing)
                continue
            proposal = InsightProposal(
                id=generate_id("ins"),
                user_id=user_id,
                source_key=source_key,
                title=f"重复主题：{tag}",
                summary=f"近期有 {len(related)} 条已确认记忆关联“{tag}”。这是一条待验证的重复主题，不等于用户固定特质。",
                support_memory_ids=support_ids,
                counter_memory_ids=[],
                confidence=min(0.75, 0.25 + len(related) * 0.08),
                invalidation_condition="用户明确纠正、忽略，或后续证据表明这些记录只是偶然重合。",
                status="proposed",
            )
            self.db.add(proposal)
            proposals.append(proposal)
        await self.db.flush()
        return proposals

    async def list(self, *, user_id: str) -> list[InsightProposal]:
        return list((await self.db.execute(
            select(InsightProposal).where(InsightProposal.user_id == user_id).order_by(InsightProposal.updated_at.desc())
        )).scalars())

    async def record_feedback(
        self,
        *,
        proposal: InsightProposal,
        user_id: str,
        status: str,
    ) -> InsightProposal:
        """Persist explicit insight feedback without turning it into a user fact.

        Feedback is deliberately stored as an auditable, content-free signal.
        It may inform an offline evaluation or a user-visible preference review,
        but never changes prompts, tools, policies, or committed memories online.
        """
        if proposal.user_id != user_id:
            raise ValueError("insight proposal does not belong to user")
        if status not in {"accepted", "corrected", "ignored", "closed"}:
            raise ValueError("unsupported insight feedback status")
        proposal.status = status
        self.db.add(AuditLog(
            id=generate_audit_log_id(),
            user_id=user_id,
            actor_type="user",
            actor_id=user_id,
            action="insight_feedback",
            target_type="insight_proposal",
            target_id=proposal.id,
            detail=f'{{"status":"{status}","learning":"offline_review_only"}}',
        ))
        await self.db.flush()
        return proposal
