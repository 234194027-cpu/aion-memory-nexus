"""Anonymous aggregate comparison for Working-Agent shadow proposals."""
from __future__ import annotations

from collections import Counter

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.execution.models.agent_runtime import AgentRun
from src.execution.models.agent_runtime import AgentRunStatus
from src.execution.models.memory_work import MemoryWorkDecision
from src.memory.models.committed_memory import CommittedMemory


def _has_conflict(value: object) -> bool:
    return bool(value) if isinstance(value, (list, dict)) else False


async def build_shadow_report(db: AsyncSession, *, user_id: str) -> dict[str, object]:
    runs = list((await db.execute(
        select(AgentRun).where(AgentRun.user_id == user_id, AgentRun.trigger_type == "raw_event")
    )).scalars())
    shadow_runs = [run for run in runs if isinstance(run.evidence_payload, dict) and run.evidence_payload.get("mode") == "shadow"]
    event_ids = {str(run.evidence_payload.get("source_event_id")) for run in shadow_runs if run.evidence_payload.get("source_event_id")}
    decisions = list(
        (
            await db.execute(
                select(MemoryWorkDecision).where(
                    MemoryWorkDecision.user_id == user_id,
                    MemoryWorkDecision.source_event_id.in_(event_ids),
                )
            )
        ).scalars()
    ) if event_ids else []
    decision_ids = [item.id for item in decisions]
    memories = list(
        (
            await db.execute(
                select(CommittedMemory).where(
                    CommittedMemory.user_id == user_id,
                    CommittedMemory.source_work_decision_id.in_(decision_ids),
                )
            )
        ).scalars()
    ) if decision_ids else []
    states = Counter(str(run.evidence_payload.get("business_state", "UNKNOWN")) for run in shadow_runs)
    shadow_proposal_count = sum(len(run.evidence_payload.get("memory_proposals", [])) for run in shadow_runs)
    conflict_count = sum(_has_conflict(item.conflict_refs) for item in decisions)
    keyed_memories = [item for item in memories if item.content_hash]
    key_counts = Counter(item.content_hash for item in keyed_memories)
    duplicate_count = sum(count - 1 for count in key_counts.values() if count > 1)
    return {
        "total_shadow_runs": len(shadow_runs),
        "business_state_counts": dict(sorted(states.items())),
        "shadow_memory_proposal_count": shadow_proposal_count,
        "formal_memory_count_for_compared_events": len(memories),
        "compared_event_count": len(event_ids),
        "shadow_failed_run_count": sum(run.status != AgentRunStatus.COMPLETED for run in shadow_runs),
        "shadow_conflict_run_count": states.get("CONFLICT_REVIEW", 0),
        "governed_decision_count_for_compared_events": len(decisions),
        "conflict_decision_count": conflict_count,
        "duplicate_metric_available": bool(keyed_memories),
        "duplicate_formal_memory_count": duplicate_count if keyed_memories else None,
    }
