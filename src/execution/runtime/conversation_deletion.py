"""User-scoped deletion for conversation ledger and conversation-derived memory."""
from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.execution.models.agent_runtime import AgentHandoff, AgentRole, AgentSession
from src.execution.models.conversation import (
    ConversationAttentionCandidate,
    ConversationEpisode,
    ConversationReflectionCursor,
    ConversationTurn,
)
from src.execution.models.memory_work import MemoryWorkCase, MemoryWorkDecision, MemoryWorkEvidence
from src.execution.runtime.workspace import AgentWorkspaceService
from src.memory.models.committed_memory import CommittedMemory
from src.memory.models.memory_source import MemorySource
from src.memory.models.raw_event import RawEvent, SourceType


async def delete_conversation_data(
    db: AsyncSession,
    *,
    user_id: str,
) -> dict[str, int | bool]:
    event_ids = list(
        (
            await db.execute(
                select(RawEvent.id).where(
                    RawEvent.user_id == user_id,
                    RawEvent.source_type == SourceType.CONVERSATION,
                )
            )
        ).scalars()
    )
    event_id_set = set(event_ids)
    counts: dict[str, int | bool] = {}
    if event_ids:
        from src.memory.services.deletion_service import tombstone_memory

        affected_memory_ids = list(
            (
                await db.execute(
                    select(MemorySource.memory_id).where(
                        MemorySource.raw_event_id.in_(event_ids)
                    )
                )
            ).scalars()
        )
        memories = list(
            (
                await db.execute(
                    select(CommittedMemory).where(
                        CommittedMemory.user_id == user_id,
                        CommittedMemory.id.in_(affected_memory_ids),
                    )
                )
            ).scalars()
        )
        tombstoned = 0
        retained = 0
        for memory in memories:
            source_event_ids = set(
                (
                    await db.execute(
                        select(MemorySource.raw_event_id).where(
                            MemorySource.memory_id == memory.id
                        )
                    )
                ).scalars()
            )
            # Keep a formal memory when it still has independent evidence.
            if source_event_ids - event_id_set:
                retained += 1
            else:
                await tombstone_memory(db, memory)
                tombstoned += 1
        counts["committed_memories_tombstoned"] = tombstoned
        counts["committed_memories_retained"] = retained
    if event_ids:
        affected_case_ids = list(
            (
                await db.execute(
                    select(MemoryWorkEvidence.case_id).where(
                        MemoryWorkEvidence.user_id == user_id,
                        MemoryWorkEvidence.raw_event_id.in_(event_ids),
                    )
                )
            ).scalars()
        )
        counts["agent_handoffs"] = int(
            (
                await db.execute(
                    delete(AgentHandoff).where(
                        AgentHandoff.user_id == user_id,
                        (
                            AgentHandoff.source_event_id.in_(event_ids)
                            | AgentHandoff.case_id.in_(affected_case_ids)
                        ),
                    )
                )
            ).rowcount
            or 0
        )
        counts["memory_work_decisions"] = int(
            (
                await db.execute(
                    delete(MemoryWorkDecision).where(
                        MemoryWorkDecision.user_id == user_id,
                        MemoryWorkDecision.source_event_id.in_(event_ids),
                    )
                )
            ).rowcount
            or 0
        )
        counts["memory_work_evidence"] = int(
            (
                await db.execute(
                    delete(MemoryWorkEvidence).where(
                        MemoryWorkEvidence.user_id == user_id,
                        MemoryWorkEvidence.raw_event_id.in_(event_ids),
                    )
                )
            ).rowcount
            or 0
        )
        empty_case_ids: list[str] = []
        for case_id in set(affected_case_ids):
            remaining = await db.scalar(
                select(MemoryWorkEvidence.id)
                .where(MemoryWorkEvidence.case_id == case_id)
                .limit(1)
            )
            if remaining is None:
                empty_case_ids.append(case_id)
            else:
                case = await db.get(MemoryWorkCase, case_id)
                if case is not None:
                    case.status = "open"
        counts["memory_work_cases"] = int(
            (
                await db.execute(
                    delete(MemoryWorkCase).where(
                        MemoryWorkCase.user_id == user_id,
                        MemoryWorkCase.id.in_(empty_case_ids),
                    )
                )
            ).rowcount
            or 0
        ) if empty_case_ids else 0
        counts["memory_sources"] = int(
            (
                await db.execute(
                    delete(MemorySource).where(
                        MemorySource.raw_event_id.in_(event_ids)
                    )
                )
            ).rowcount
            or 0
        )
    if event_ids:
        counts["raw_events"] = int(
            (
                await db.execute(delete(RawEvent).where(RawEvent.id.in_(event_ids)))
            ).rowcount
            or 0
        )

    for name, model in (
        ("attention_candidates", ConversationAttentionCandidate),
        ("reflection_cursors", ConversationReflectionCursor),
        ("episodes", ConversationEpisode),
        ("turns", ConversationTurn),
    ):
        counts[name] = int(
            (await db.execute(delete(model).where(model.user_id == user_id))).rowcount
            or 0
        )
    counts["sessions"] = int(
        (
            await db.execute(
                delete(AgentSession).where(
                    AgentSession.user_id == user_id,
                    AgentSession.agent_role == AgentRole.CONVERSATIONAL,
                )
            )
        ).rowcount
        or 0
    )
    await db.commit()
    counts["workspace_deleted"] = AgentWorkspaceService().delete_user_workspace(
        user_id=user_id
    )
    return counts
