"""User-scoped deletion for the conversation ledger and every derived trace."""
from __future__ import annotations

from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.execution.models.agent_runtime import AgentHandoff, AgentRole, AgentSession
from src.execution.models.conversation import (
    ConversationAttentionCandidate,
    ConversationEpisode,
    ConversationReflectionCursor,
    ConversationTurn,
)
from src.execution.models.memory_operations import (
    EvidenceSeal,
    MemoryMaintenanceAction,
    MemoryMaintenanceRun,
    UserMemoryBrief,
)
from src.execution.models.memory_work import MemoryWorkCase, MemoryWorkDecision, MemoryWorkEvidence
from src.execution.runtime.workspace import AgentWorkspaceService
from src.memory.models.committed_memory import CommittedMemory
from src.memory.models.memory_source import MemorySource
from src.memory.models.raw_event import RawEvent, SourceType
from src.memory.models.graph_projection import GraphShadowObservation


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
    seal_ids = list(
        (
            await db.execute(
                select(EvidenceSeal.id).where(
                    EvidenceSeal.user_id == user_id,
                    EvidenceSeal.source_type == SourceType.CONVERSATION.value,
                )
            )
        ).scalars()
    )
    event_id_set = set(event_ids)
    seal_id_set = set(seal_ids)
    counts: dict[str, int | bool] = {}

    affected_memory_ids = list(
        dict.fromkeys(
            (
                await db.execute(
                    select(MemorySource.memory_id).where(
                        or_(
                            MemorySource.raw_event_id.in_(event_ids) if event_ids else False,
                            MemorySource.evidence_seal_id.in_(seal_ids) if seal_ids else False,
                        )
                    )
                )
            ).scalars()
        )
    )
    tombstoned_memory_ids: list[str] = []
    if affected_memory_ids:
        from src.memory.services.deletion_service import tombstone_memory

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
        retained = 0
        for memory in memories:
            sources = list(
                (
                    await db.execute(
                        select(MemorySource.raw_event_id, MemorySource.evidence_seal_id).where(
                            MemorySource.memory_id == memory.id
                        )
                    )
                ).all()
            )
            has_independent_source = any(
                (raw_event_id not in event_id_set and evidence_seal_id not in seal_id_set)
                for raw_event_id, evidence_seal_id in sources
            )
            if has_independent_source:
                retained += 1
            else:
                await tombstone_memory(db, memory)
                tombstoned_memory_ids.append(memory.id)
        counts["committed_memories_tombstoned"] = len(tombstoned_memory_ids)
        counts["committed_memories_retained"] = retained

    evidence_filter = or_(
        MemoryWorkEvidence.raw_event_id.in_(event_ids) if event_ids else False,
        MemoryWorkEvidence.evidence_seal_id.in_(seal_ids) if seal_ids else False,
    )
    affected_case_ids = list(
        dict.fromkeys(
            (
                await db.execute(
                    select(MemoryWorkEvidence.case_id).where(
                        MemoryWorkEvidence.user_id == user_id,
                        evidence_filter,
                    )
                )
            ).scalars()
        )
    )
    if event_ids or affected_case_ids:
        handoff_filter = []
        if event_ids:
            handoff_filter.append(AgentHandoff.source_event_id.in_(event_ids))
        if affected_case_ids:
            handoff_filter.append(AgentHandoff.case_id.in_(affected_case_ids))
        counts["agent_handoffs"] = int(
            (
                await db.execute(
                    delete(AgentHandoff).where(
                        AgentHandoff.user_id == user_id,
                        or_(*handoff_filter),
                    )
                )
            ).rowcount
            or 0
        )

    if event_ids:
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
    if event_ids or seal_ids:
        counts["memory_work_evidence"] = int(
            (
                await db.execute(
                    delete(MemoryWorkEvidence).where(
                        MemoryWorkEvidence.user_id == user_id,
                        evidence_filter,
                    )
                )
            ).rowcount
            or 0
        )

    empty_case_ids: list[str] = []
    for case_id in affected_case_ids:
        remaining = await db.scalar(
            select(MemoryWorkEvidence.id).where(MemoryWorkEvidence.case_id == case_id).limit(1)
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

    # Maintenance details may contain pre-change memory text.  Remove any
    # action that touched the deleted conversation sources or their memories.
    affected_action_ids: list[str] = []
    affected_run_ids: set[str] = set()
    for action in (
        await db.execute(select(MemoryMaintenanceAction).where(MemoryMaintenanceAction.user_id == user_id))
    ).scalars():
        action_events = set(action.input_event_ids or [])
        action_memories = set(action.input_memory_ids or [])
        if (
            action_events.intersection(event_id_set)
            or action_memories.intersection(affected_memory_ids)
            or action.evidence_seal_id in seal_id_set
        ):
            affected_action_ids.append(action.id)
            affected_run_ids.add(action.run_id)
    if affected_action_ids:
        counts["memory_maintenance_actions"] = int(
            (
                await db.execute(
                    delete(MemoryMaintenanceAction).where(MemoryMaintenanceAction.id.in_(affected_action_ids))
                )
            ).rowcount
            or 0
        )

    source_filters = []
    if event_ids:
        source_filters.append(MemorySource.raw_event_id.in_(event_ids))
    if seal_ids:
        source_filters.append(MemorySource.evidence_seal_id.in_(seal_ids))
    if source_filters:
        counts["memory_sources"] = int(
            (await db.execute(delete(MemorySource).where(or_(*source_filters)))).rowcount or 0
        )

    if seal_ids:
        counts["evidence_seals"] = int(
            (
                await db.execute(
                    delete(EvidenceSeal).where(
                        EvidenceSeal.user_id == user_id,
                        EvidenceSeal.id.in_(seal_ids),
                    )
                )
            ).rowcount
            or 0
        )
    if event_ids:
        counts["raw_events"] = int(
            (await db.execute(delete(RawEvent).where(RawEvent.id.in_(event_ids)))).rowcount or 0
        )

    for run_id in affected_run_ids:
        remaining_action = await db.scalar(
            select(MemoryMaintenanceAction.id).where(MemoryMaintenanceAction.run_id == run_id).limit(1)
        )
        if remaining_action is None:
            await db.execute(
                delete(MemoryMaintenanceRun).where(
                    MemoryMaintenanceRun.id == run_id,
                    MemoryMaintenanceRun.user_id == user_id,
                )
            )

    for name, model in (
        ("attention_candidates", ConversationAttentionCandidate),
        ("reflection_cursors", ConversationReflectionCursor),
        ("episodes", ConversationEpisode),
        ("turns", ConversationTurn),
    ):
        counts[name] = int(
            (await db.execute(delete(model).where(model.user_id == user_id))).rowcount or 0
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
    counts["memory_briefs"] = int(
        (await db.execute(delete(UserMemoryBrief).where(UserMemoryBrief.user_id == user_id))).rowcount or 0
    )
    counts["graph_shadow_observations"] = int(
        (await db.execute(delete(GraphShadowObservation).where(GraphShadowObservation.user_id == user_id))).rowcount or 0
    )

    # Rebuild the derived brief from remaining formal memories before commit.
    from src.execution.services.memory_operations import MemoryOperationsCoordinator

    await MemoryOperationsCoordinator(db).refresh_user_brief(user_id)
    await db.commit()
    counts["workspace_deleted"] = AgentWorkspaceService().delete_user_workspace(user_id=user_id)
    from src.execution.services.conversation_memory_projector import try_refresh_conversation_memory_projection

    await try_refresh_conversation_memory_projection(db, user_id=user_id)
    await db.commit()
    from src.shared.llm.providers import clear_llm_runtime_caches

    clear_llm_runtime_caches()
    return counts
