"""V2.2 case-based coordinator for the Working Agent."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.execution.models.agent_runtime import (
    AgentHandoff,
    AgentHandoffStatus,
    AgentRole,
    AgentRun,
    AgentSession,
    AgentSessionStatus,
)
from src.execution.models.conversation import ConversationAttentionCandidate
from src.execution.models.memory_work import MemoryWorkCase, MemoryWorkDecision, MemoryWorkEvidence
from src.execution.services.builtin_runtime_permission import WORKING_RUNTIME_ID
from src.execution.services.memory_case_service import MemoryCaseService
from src.execution.services.memory_commit_service import MemoryCommitService
from src.memory.models.raw_event import RawEvent, SensitivityLevel
from src.shared.ids.id_generator import generate_id
from src.shared.llm.providers import get_llm_provider

from .factory import build_working_runtime
from .model import JsonCompatibilityModel, RuntimeModel
from .profile import WORKING_PROFILE
from .workspace import AgentWorkspaceService
from .working_agent import (
    WorkingActiveResult,
    WorkingBusinessState,
    _handoff_expiry,
    _load_active_handoff_context,
    _parse_business_result,
    build_working_event_message,
)


class WorkingCoordinator:
    """Turns RawEvents into governed cases, decisions and formal memories."""

    def __init__(
        self,
        db: AsyncSession,
        *,
        model: RuntimeModel | None = None,
        workspace: AgentWorkspaceService | None = None,
    ) -> None:
        self.db = db
        self.model = model
        self.cases = MemoryCaseService(db)
        self.commits = MemoryCommitService(db)
        self.workspace = workspace or AgentWorkspaceService()

    async def process(self, raw_event_id: str) -> WorkingActiveResult | None:
        event = await self.db.scalar(select(RawEvent).where(RawEvent.id == raw_event_id))
        if event is None:
            return None
        mapping = _event_mapping(event)
        batch_ids = (event.event_metadata or {}).get("batch_source_event_ids")
        if isinstance(batch_ids, list):
            normalized = [str(item) for item in batch_ids if isinstance(item, str) and item][:8]
            if normalized:
                events = list((await self.db.execute(
                    select(RawEvent).where(
                        RawEvent.user_id == event.user_id,
                        RawEvent.id.in_(normalized),
                    )
                )).scalars())
                by_id = {item.id: item for item in events}
                metadata = dict(mapping["metadata"])
                metadata["batch_evidence"] = [
                    {
                        "id": item.id,
                        "content": item.content[:2000],
                        "occurred_at": item.occurred_at,
                        "source_turn_id": (item.event_metadata or {}).get("source_turn_id"),
                    }
                    for event_id in normalized
                    if (item := by_id.get(event_id)) is not None
                ]
                metadata["batch_source_event_ids"] = normalized
                mapping["metadata"] = metadata
                mapping["event_metadata"] = metadata
        return await self.process_mapping(mapping)

    async def process_mapping(self, raw_event: Mapping[str, Any]) -> WorkingActiveResult | None:
        if not raw_event.get("id") or not raw_event.get("user_id") or not isinstance(raw_event.get("content"), str):
            return None

        user_id = str(raw_event["user_id"])
        event_id = str(raw_event["id"])
        prior_decisions = list(
            (
                await self.db.execute(
                    select(MemoryWorkDecision)
                    .where(
                        MemoryWorkDecision.user_id == user_id,
                        MemoryWorkDecision.source_event_id == event_id,
                    )
                    .order_by(MemoryWorkDecision.created_at.asc())
                )
            ).scalars()
        )
        if prior_decisions:
            try:
                prior_state = WorkingBusinessState(prior_decisions[-1].state)
            except ValueError:
                prior_state = WorkingBusinessState.NEEDS_MORE_EVIDENCE
            persisted_memory_ids = tuple(
                dict.fromkeys(
                    memory_id
                    for decision in prior_decisions
                    for memory_id in (decision.memory_ids or [])
                )
            )
            if prior_state == WorkingBusinessState.MEMORY_READY and not persisted_memory_ids:
                resumed_ids: list[str] = []
                for decision in prior_decisions:
                    proposal = (decision.policy_result or {}).get("memory_proposal")
                    case = await self.db.get(MemoryWorkCase, decision.case_id)
                    if case is None or not isinstance(proposal, Mapping):
                        continue
                    committed = await self.commits.materialize(
                        case=case,
                        decision=decision,
                        proposal=proposal,
                    )
                    if committed.memory_id:
                        resumed_ids.append(committed.memory_id)
                persisted_memory_ids = tuple(dict.fromkeys(resumed_ids))
            handoff = await self.db.scalar(
                select(AgentHandoff)
                .where(
                    AgentHandoff.user_id == user_id,
                    AgentHandoff.source_event_id == event_id,
                    AgentHandoff.mode == "active",
                )
                .order_by(AgentHandoff.created_at.desc())
                .limit(1)
            )
            return WorkingActiveResult(
                prior_decisions[-1].source_run_id or "",
                prior_state,
                persisted_memory_ids,
                handoff.id if handoff is not None else None,
            )
        profile = self.workspace.apply_to_profile(
            user_id=user_id,
            agent="working",
            profile=WORKING_PROFILE,
        )
        handoff_context, source_event_ids = await _load_active_handoff_context(
            self.db,
            raw_event=raw_event,
        )
        batch_ids = _metadata(raw_event).get("batch_source_event_ids")
        if isinstance(batch_ids, list):
            source_event_ids = tuple(dict.fromkeys([
                *source_event_ids,
                *(str(item) for item in batch_ids if isinstance(item, str) and item),
            ]))
        runtime = build_working_runtime(
            self.db,
            self.model
            or JsonCompatibilityModel(get_llm_provider(), max_tokens=1200, role="working"),
            shadow=False,
        )
        context = runtime.new_context(
            user_id=user_id,
            profile=profile,
            channel="system",
            channel_session_key=event_id,
            trigger_type="raw_event",
            trigger_id=event_id,
            agent_id=WORKING_RUNTIME_ID,
            context_version="memory-case-v2.4",
        )
        result = await runtime.run(
            context,
            (
                {
                    "role": "user",
                    "content": build_working_event_message(
                        raw_event,
                        mode="active",
                        handoff_context=handoff_context,
                    ),
                },
            ),
        )
        from src.execution.models.agent_runtime import AgentRunStatus

        if result.status != AgentRunStatus.COMPLETED:
            return None

        state, proposals, question = _parse_business_result(result.final_text)
        run = await self.db.get(AgentRun, result.run_id)
        if run is not None:
            run.evidence_payload = {
                "mode": "active",
                "business_state": state.value,
                "proposal_count": len(proposals),
                "source_event_id": event_id,
                "ledger": "memory-case-v1",
            }

        linked_handoff = await self._linked_handoff(raw_event)
        preferred_case_id = (
            linked_handoff.case_id
            if linked_handoff is not None
            else await self._preferred_case_id(raw_event)
        )
        cases: list[MemoryWorkCase] = []
        memory_ids: list[str] = []

        work_items: Sequence[Mapping[str, Any]]
        if proposals:
            work_items = proposals
        else:
            metadata = _metadata(raw_event)
            work_items = (
                {
                    "memory_type": metadata.get("event_kind") or "fact",
                    "title": _fallback_title(raw_event["content"]),
                    "content": raw_event["content"],
                    "confidence": metadata.get("quality_score", 0.3),
                    "sensitivity": getattr(raw_event.get("sensitivity"), "value", raw_event.get("sensitivity")) or "normal",
                    "reason": f"Working Agent state {state.value}",
                    "proposition_key": metadata.get("proposition_key"),
                },
            )

        for index, proposal in enumerate(work_items):
            case = await self.cases.route_case(
                user_id=user_id,
                memory_type=proposal.get("memory_type", "fact"),
                title=str(proposal.get("title") or _fallback_title(raw_event["content"])),
                content=str(proposal.get("content") or raw_event["content"]),
                sensitivity=proposal.get("sensitivity", raw_event.get("sensitivity", "normal")),
                confidence=_score(proposal.get("confidence", 0.3)),
                proposition_key=proposal.get("proposition_key"),
                preferred_case_id=preferred_case_id if index == 0 else None,
                metadata={
                    "source": "working_coordinator",
                    "source_event_id": event_id,
                    "episode_id": _metadata(raw_event).get("episode_id"),
                },
            )
            cases.append(case)
            await self._attach_source_evidence(
                case=case,
                raw_event=raw_event,
                source_event_ids=source_event_ids,
            )
            decision = await self.cases.record_decision(
                case=case,
                user_id=user_id,
                event_id=event_id,
                state=state.value,
                run_id=result.run_id,
                proposal=proposal if proposals else None,
                rationale=str(proposal.get("reason") or f"Working Agent state {state.value}"),
                model=run.model if run is not None else None,
                prompt_id=WORKING_PROFILE.prompt_id,
                prompt_version=WORKING_PROFILE.prompt_version,
                duplicate_refs=_list_of_strings(proposal.get("duplicate_memory_ids")),
                conflict_refs=_list_of_strings(proposal.get("conflict_memory_ids")),
                rationale_codes=_list_of_strings(proposal.get("rationale_codes")),
                policy_result={
                    "governance": "working-agent-v2.4",
                    "commit_allowed": False,
                    "memory_proposal": _bounded_proposal(proposal),
                },
            )
            self.cases.apply_state(case, state.value)
            if state == WorkingBusinessState.MEMORY_READY and proposals:
                committed = await self.commits.materialize(
                    case=case,
                    decision=decision,
                    proposal=proposal,
                )
                if committed.memory_id:
                    memory_ids.append(committed.memory_id)

        handoff_id = None
        if (
            state
            in {
                WorkingBusinessState.NEEDS_MORE_EVIDENCE,
                WorkingBusinessState.CONFLICT_REVIEW,
                WorkingBusinessState.USER_CONFIRMATION_REQUIRED,
            }
            and question
            and cases
        ):
            handoff_id = await self._request_evidence(
                case=cases[0],
                run_id=result.run_id,
                raw_event=raw_event,
                state=state,
                question=question,
            )

        if linked_handoff is not None and state in {
            WorkingBusinessState.MEMORY_READY,
            WorkingBusinessState.DISCARDED,
        }:
            linked_handoff.status = AgentHandoffStatus.RESOLVED
            linked_handoff.resolved_by_event_id = event_id
            linked_handoff.responded_at = datetime.now(timezone.utc)

        await self.db.flush()
        try:
            self.workspace.project_work_cases(
                user_id=user_id,
                cases=cases,
                event_id=event_id,
                state=state.value,
            )
        except OSError:
            pass
        return WorkingActiveResult(result.run_id, state, tuple(memory_ids), handoff_id)

    async def materialize_preclassified(
        self,
        *,
        event: RawEvent,
        proposals: Sequence[Mapping[str, Any]],
        origin: str,
    ) -> tuple[str, ...]:
        """Persist trusted structural extraction through the same case boundary.

        Media parsers and external-agent adapters may already have structured
        text, but they still cannot write formal memory directly. This method
        records a case, evidence and decision before the same governed commit.
        """
        memory_ids: list[str] = []
        for proposal in proposals:
            case = await self.cases.route_case(
                user_id=event.user_id,
                memory_type=proposal.get("memory_type", "insight"),
                title=str(proposal.get("title") or _fallback_title(event.content)),
                content=str(proposal.get("content") or event.content),
                sensitivity=proposal.get("sensitivity", event.sensitivity),
                confidence=_score(proposal.get("confidence", 0.4)),
                proposition_key=proposal.get("proposition_key"),
                metadata={"source": origin, "source_event_id": event.id, "preclassified": True},
            )
            await self.cases.attach_evidence(
                case=case,
                event=event,
                relationship=_evidence_relationship(event),
            )
            decision = await self.cases.record_decision(
                case=case,
                user_id=event.user_id,
                event_id=event.id,
                state=WorkingBusinessState.MEMORY_READY.value,
                run_id=None,
                proposal=proposal,
                rationale=str(proposal.get("reason") or f"Structured evidence from {origin}"),
                model=None,
                prompt_id=f"{origin}-adapter",
                prompt_version="v2.4",
                rationale_codes=("preclassified_adapter",),
                policy_result={
                    "governance": "working-agent-v2.4",
                    "commit_allowed": False,
                    "memory_proposal": _bounded_proposal(proposal),
                    "adapter": origin,
                },
            )
            committed = await self.commits.materialize(
                case=case,
                decision=decision,
                proposal=proposal,
            )
            if committed.memory_id:
                memory_ids.append(committed.memory_id)
        await self.db.flush()
        return tuple(memory_ids)

    async def _linked_handoff(self, raw_event: Mapping[str, Any]) -> AgentHandoff | None:
        handoff_id = _metadata(raw_event).get("handoff_id")
        if not isinstance(handoff_id, str) or not handoff_id:
            return None
        return await self.db.scalar(
            select(AgentHandoff).where(
                AgentHandoff.id == handoff_id,
                AgentHandoff.user_id == str(raw_event["user_id"]),
                AgentHandoff.mode == "active",
                AgentHandoff.status == AgentHandoffStatus.ACTIVE,
            )
        )

    async def _preferred_case_id(self, raw_event: Mapping[str, Any]) -> str | None:
        metadata = _metadata(raw_event)
        explicit = metadata.get("memory_case_id")
        if isinstance(explicit, str) and explicit:
            owned = await self.db.scalar(
                select(MemoryWorkCase.id).where(
                    MemoryWorkCase.id == explicit,
                    MemoryWorkCase.user_id == str(raw_event["user_id"]),
                )
            )
            if owned:
                return owned
        corrected_event_id = metadata.get("correction_of_event_id")
        if isinstance(corrected_event_id, str) and corrected_event_id:
            return await self.db.scalar(
                select(MemoryWorkEvidence.case_id)
                .where(
                    MemoryWorkEvidence.user_id == str(raw_event["user_id"]),
                    MemoryWorkEvidence.raw_event_id == corrected_event_id,
                )
                .order_by(MemoryWorkEvidence.created_at.desc())
                .limit(1)
            )
        return None

    async def _attach_source_evidence(
        self,
        *,
        case: MemoryWorkCase,
        raw_event: Mapping[str, Any],
        source_event_ids: Sequence[str],
    ) -> None:
        current_id = str(raw_event["id"])
        for source_event_id in source_event_ids:
            if source_event_id == current_id:
                evidence_event: RawEvent | Mapping[str, Any] = raw_event
            else:
                loaded = await self.db.scalar(
                    select(RawEvent).where(
                        RawEvent.id == source_event_id,
                        RawEvent.user_id == case.user_id,
                    )
                )
                if loaded is None:
                    continue
                evidence_event = loaded
            await self.cases.attach_evidence(
                case=case,
                event=evidence_event,
                relationship=_evidence_relationship(evidence_event),
            )

    async def _request_evidence(
        self,
        *,
        case: MemoryWorkCase,
        run_id: str,
        raw_event: Mapping[str, Any],
        state: WorkingBusinessState,
        question: str,
    ) -> str:
        existing = await self.db.scalar(
            select(AgentHandoff)
            .where(
                AgentHandoff.case_id == case.id,
                AgentHandoff.user_id == case.user_id,
                AgentHandoff.mode == "active",
                AgentHandoff.status == AgentHandoffStatus.ACTIVE,
            )
            .order_by(AgentHandoff.created_at.desc())
            .limit(1)
        )
        if existing is not None:
            return existing.id
        handoff = AgentHandoff(
            id=generate_id("ahf"),
            user_id=case.user_id,
            source_run_id=run_id,
            source_event_id=str(raw_event["id"]),
            case_id=case.id,
            handoff_type=state.value.lower(),
            mode="active",
            priority=1 if state == WorkingBusinessState.USER_CONFIRMATION_REQUIRED else 0,
            question=question[:500],
            evidence_payload={
                "source_event_id": str(raw_event["id"]),
                "business_state": state.value,
                "case_id": case.id,
            },
            evidence_requirements=["direct_user_answer"],
            resolution_condition="A new user-authored RawEvent answers the evidence question.",
            sensitivity_limit=case.sensitivity,
            attempt_count=0,
            next_eligible_at=datetime.now(timezone.utc),
            status=AgentHandoffStatus.ACTIVE,
            expires_at=_handoff_expiry(),
        )
        self.db.add(handoff)
        conversation_session = await self.db.scalar(
            select(AgentSession)
            .where(
                AgentSession.user_id == case.user_id,
                AgentSession.agent_role == AgentRole.CONVERSATIONAL,
                AgentSession.status == AgentSessionStatus.ACTIVE,
            )
            .order_by(AgentSession.updated_at.desc())
            .limit(1)
        )
        if conversation_session is not None:
            self.db.add(
                ConversationAttentionCandidate(
                    id=generate_id("cac"),
                    user_id=case.user_id,
                    session_id=conversation_session.id,
                    episode_id=_metadata(raw_event).get("episode_id"),
                    kind="evidence_follow_up",
                    prompt=question[:500],
                    value_score=0.9,
                    source="working_handoff",
                    sensitivity=case.sensitivity,
                    status="pending",
                    due_at=datetime.now(timezone.utc),
                    expires_at=_handoff_expiry(),
                    source_turn_ids=list(_metadata(raw_event).get("source_turn_ids") or [])[:20],
                    proactive_allowed=case.sensitivity not in {"private", "sensitive"},
                    candidate_metadata={
                        "handoff_id": handoff.id,
                        "case_id": case.id,
                        "source_event_id": str(raw_event["id"]),
                    },
                )
            )
        return handoff.id


def _event_mapping(event: RawEvent) -> dict[str, Any]:
    return {
        "id": event.id,
        "content": event.content,
        "user_id": event.user_id,
        "visibility_scope": event.visibility_scope,
        "project_id": event.project_id,
        "repo_id": event.repo_id,
        "workspace_id": event.workspace_id,
        "source_type": event.source_type,
        "sensitivity": event.sensitivity,
        "occurred_at": event.occurred_at,
        "metadata": event.event_metadata or {},
        "event_metadata": event.event_metadata or {},
    }


def _metadata(raw_event: Mapping[str, Any]) -> dict[str, Any]:
    value = raw_event.get("metadata", raw_event.get("event_metadata"))
    return dict(value or {}) if isinstance(value, Mapping) else {}


def _fallback_title(content: object) -> str:
    text = " ".join(str(content or "").split())
    return (text[:80] or "待治理记忆线索")


def _score(value: object) -> float:
    if not isinstance(value, (str, int, float)):
        return 0.0
    try:
        return max(0.0, min(float(value), 1.0))
    except ValueError:
        return 0.0


def _list_of_strings(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(str(item)[:128] for item in value if str(item).strip())[:50]


def _evidence_relationship(event: RawEvent | Mapping[str, Any]) -> str:
    if isinstance(event, Mapping):
        metadata = _metadata(event)
    else:
        metadata = dict(event.event_metadata or {})
    if metadata.get("correction_of_event_id") or metadata.get("event_kind") == "correction":
        return "corrects"
    if metadata.get("contradicts_event_id"):
        return "contradicts"
    if metadata.get("runtime_handoff_response"):
        return "supports"
    return "supports"


def _bounded_proposal(proposal: Mapping[str, Any]) -> dict[str, Any]:
    values = proposal.get("entities", proposal.get("tags", []))
    return {
        "memory_type": str(proposal.get("memory_type") or "fact")[:32],
        "title": str(proposal.get("title") or "")[:240],
        "content": str(proposal.get("content") or proposal.get("body") or "")[:8000],
        "importance": _score(proposal.get("importance", 0.5)),
        "confidence": _score(proposal.get("confidence", 0.5)),
        "sensitivity": str(proposal.get("sensitivity") or "normal")[:16],
        "entities": [str(item)[:100] for item in values if str(item).strip()][:50]
        if isinstance(values, list)
        else [],
    }
