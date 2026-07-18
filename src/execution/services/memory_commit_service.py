"""Transactional, evidence-gated formal-memory writer for the Working Agent."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.execution.models.memory_work import MemoryWorkCase, MemoryWorkDecision, MemoryWorkEvidence
from src.memory.models.committed_memory import CommittedMemory, CommittedStatus
from src.memory.models.memory_source import MemorySource
from src.memory.models.memory_type import MemoryType
from src.memory.models.raw_event import (
    EpistemicStatus,
    RawEvent,
    SensitivityLevel,
    SourceType,
    VisibilityScope,
)
from src.memory.services.governance_policy import (
    FORMAL_MEMORY_BLOCKED_EPISTEMIC_STATUSES,
    derive_epistemic_status,
)
from src.memory.services.memory_lifecycle import record_memory_state_transition
from src.shared.ids.id_generator import generate_memory_id, generate_source_id


@dataclass(frozen=True, slots=True)
class MemoryCommitResult:
    action: str
    memory_id: str | None
    reason: str


class MemoryCommitService:
    """The only Working-Agent boundary allowed to create formal memories."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def materialize(
        self,
        *,
        case: MemoryWorkCase,
        decision: MemoryWorkDecision,
        proposal: Mapping[str, Any],
    ) -> MemoryCommitResult:
        await self._validate_ownership(case=case, decision=decision)
        # Serialize the final governance write. The database uniqueness rule on
        # source_work_decision_id remains the last line of idempotency defense.
        locked_decision = await self.db.scalar(
            select(MemoryWorkDecision)
            .where(MemoryWorkDecision.id == decision.id)
            .with_for_update()
        )
        locked_case = await self.db.scalar(
            select(MemoryWorkCase).where(MemoryWorkCase.id == case.id).with_for_update()
        )
        if locked_decision is None or locked_case is None:
            raise LookupError("memory_case_or_decision_missing")
        decision = locked_decision
        case = locked_case
        if decision.state != "MEMORY_READY":
            return await self._hold(case, decision, "decision_not_ready")

        existing_for_decision = await self.db.scalar(
            select(CommittedMemory).where(
                CommittedMemory.user_id == case.user_id,
                CommittedMemory.source_work_decision_id == decision.id,
            )
        )
        if existing_for_decision is not None:
            self._resolve(case, decision, existing_for_decision.id, "idempotent_replay")
            return MemoryCommitResult("existing", existing_for_decision.id, "idempotent_replay")

        title = str(proposal.get("title") or "").strip()
        body = str(proposal.get("content") or proposal.get("body") or "").strip()
        if not title or not body:
            return await self._hold(case, decision, "title_or_body_missing")

        evidence = list(
            (
                await self.db.execute(
                    select(MemoryWorkEvidence)
                    .where(
                        MemoryWorkEvidence.case_id == case.id,
                        MemoryWorkEvidence.user_id == case.user_id,
                    )
                    .order_by(MemoryWorkEvidence.created_at.asc())
                )
            ).scalars()
        )
        if not evidence:
            return await self._hold(case, decision, "evidence_missing")

        raw_events = await self._load_owned_events(case.user_id, evidence)
        if not raw_events:
            return await self._hold(case, decision, "owned_source_event_missing")

        memory_type = _memory_type(proposal.get("memory_type") or case.case_type)
        sensitivity = _sensitivity(proposal.get("sensitivity") or case.sensitivity)
        confidence = _score(proposal.get("confidence", case.confidence))
        importance = _score(proposal.get("importance", 0.5))
        source_type = _primary_source_type(raw_events)
        epistemic_status = derive_epistemic_status(source_type, memory_type=memory_type)

        evidence_error = _validate_evidence(
            evidence=evidence,
            raw_events=raw_events,
            memory_type=memory_type,
            epistemic_status=epistemic_status,
            confidence=confidence,
            importance=importance,
        )
        if evidence_error:
            return await self._hold(case, decision, evidence_error)

        duplicates = await self._owned_memories(case.user_id, decision.duplicate_refs)
        if duplicates:
            linked = duplicates[0]
            self._resolve(case, decision, linked.id, "linked_existing_duplicate")
            await self.db.flush()
            return MemoryCommitResult("linked_existing", linked.id, "linked_existing_duplicate")

        conflicts = await self._owned_memories(case.user_id, decision.conflict_refs)
        is_correction = memory_type == MemoryType.CORRECTION or any(
            item.relationship == "corrects" for item in evidence
        )
        if conflicts and not is_correction:
            case.status = "conflict_review"
            case.updated_at = datetime.now(timezone.utc)
            decision.policy_result = _policy_result(
                decision,
                commit_allowed=False,
                reason="unresolved_conflict",
                proposal=proposal,
            )
            await self.db.flush()
            return MemoryCommitResult("held", None, "unresolved_conflict")

        current = await self._current_case_memory(case)
        if current is not None and _same_memory(current, memory_type, title, body):
            self._resolve(case, decision, current.id, "case_content_unchanged")
            await self.db.flush()
            return MemoryCommitResult("existing", current.id, "case_content_unchanged")

        revision = int(current.revision or 1) + 1 if current is not None else 1
        primary_event = next(iter(raw_events.values()))
        memory = CommittedMemory(
            id=generate_memory_id(),
            source_work_case_id=case.id,
            source_work_decision_id=decision.id,
            origin_kind="working_agent",
            revision=revision,
            automation_metadata={
                "governance": "working-agent-v2.4",
                "rationale_codes": list(decision.rationale_codes or [])[:30],
                "duplicate_refs": [item.id for item in duplicates],
                "conflict_refs": [item.id for item in conflicts],
            },
            user_id=case.user_id,
            project_id=primary_event.project_id,
            repo_id=primary_event.repo_id,
            workspace_id=primary_event.workspace_id,
            memory_type=memory_type,
            title=title[:240],
            body=body[:8000],
            confidence=confidence,
            importance=importance,
            sensitivity=sensitivity,
            epistemic_status=epistemic_status,
            visibility_scope=_strictest_visibility(raw_events),
            status=CommittedStatus.ACTIVE,
            valid_from=datetime.now(timezone.utc),
            tags=_tags(proposal),
            content_hash=_content_hash(case.user_id, memory_type, title, body),
        )
        self.db.add(memory)
        await self.db.flush()
        # Derived graph work is committed in the same source transaction, but
        # its delivery is asynchronous and cannot block formal-memory governance.
        from src.memory.services.graph_projection import queue_memory_projection

        await queue_memory_projection(self.db, memory)

        for item in evidence:
            event = raw_events.get(item.raw_event_id)
            if event is None:
                continue
            self.db.add(
                MemorySource(
                    id=generate_source_id(),
                    memory_id=memory.id,
                    raw_event_id=event.id,
                    quote=item.quote,
                    location=item.source_turn_id or item.episode_id,
                    source_type=event.source_type,
                )
            )

        superseded_ids: list[str] = []
        for previous in [item for item in [current, *conflicts] if item is not None]:
            if previous.id == memory.id or previous.status != CommittedStatus.ACTIVE:
                continue
            previous.status = CommittedStatus.SUPERSEDED
            previous.valid_until = datetime.now(timezone.utc)
            from src.memory.services.graph_projection import queue_source_deletion

            await queue_source_deletion(
                self.db,
                user_id=previous.user_id,
                project_id=previous.project_id,
                source_kind="committed_memory",
                source_id=previous.id,
                # Delete targets the exact prior Graphiti episode ID; lifecycle
                # is carried by the operation, not by changing its revision.
                source_revision=previous.content_hash or str(previous.revision or 1),
            )
            superseded_ids.append(previous.id)
            await record_memory_state_transition(
                self.db,
                user_id=case.user_id,
                subject_type="committed_memory",
                subject_id=previous.id,
                from_state=CommittedStatus.ACTIVE,
                to_state=CommittedStatus.SUPERSEDED,
                actor_type="working_agent",
                actor_id=decision.source_run_id,
                reason="working_agent_revision",
                evidence_refs=[item.raw_event_id for item in evidence],
            )

        if superseded_ids:
            memory.automation_metadata = {
                **dict(memory.automation_metadata or {}),
                "superseded_memory_ids": list(dict.fromkeys(superseded_ids)),
            }

        await record_memory_state_transition(
            self.db,
            user_id=case.user_id,
            subject_type="committed_memory",
            subject_id=memory.id,
            from_state=None,
            to_state=CommittedStatus.ACTIVE,
            actor_type="working_agent",
            actor_id=decision.source_run_id,
            reason="working_agent_auto_committed",
            evidence_refs=[item.raw_event_id for item in evidence],
        )
        self._resolve(case, decision, memory.id, "auto_committed")
        await self.db.flush()
        return MemoryCommitResult("created", memory.id, "auto_committed")

    async def _validate_ownership(
        self, *, case: MemoryWorkCase, decision: MemoryWorkDecision
    ) -> None:
        if decision.case_id != case.id or decision.user_id != case.user_id:
            raise ValueError("memory_case_decision_ownership_mismatch")

    async def _load_owned_events(
        self, user_id: str, evidence: Sequence[MemoryWorkEvidence]
    ) -> dict[str, RawEvent]:
        ids = list(dict.fromkeys(item.raw_event_id for item in evidence))
        rows = list(
            (
                await self.db.execute(
                    select(RawEvent).where(RawEvent.id.in_(ids), RawEvent.user_id == user_id)
                )
            ).scalars()
        )
        return {item.id: item for item in rows}

    async def _owned_memories(
        self, user_id: str, memory_ids: Sequence[str] | None
    ) -> list[CommittedMemory]:
        ids = list(dict.fromkeys(str(item) for item in (memory_ids or []) if item))
        if not ids:
            return []
        return list(
            (
                await self.db.execute(
                    select(CommittedMemory).where(
                        CommittedMemory.id.in_(ids),
                        CommittedMemory.user_id == user_id,
                        CommittedMemory.status == CommittedStatus.ACTIVE,
                    )
                )
            ).scalars()
        )

    async def _current_case_memory(self, case: MemoryWorkCase) -> CommittedMemory | None:
        if case.active_memory_id:
            current = await self.db.scalar(
                select(CommittedMemory).where(
                    CommittedMemory.id == case.active_memory_id,
                    CommittedMemory.user_id == case.user_id,
                )
            )
            if current is not None:
                return current
        return await self.db.scalar(
            select(CommittedMemory)
            .where(
                CommittedMemory.source_work_case_id == case.id,
                CommittedMemory.user_id == case.user_id,
                CommittedMemory.status == CommittedStatus.ACTIVE,
            )
            .order_by(CommittedMemory.revision.desc())
            .limit(1)
        )

    async def _hold(
        self, case: MemoryWorkCase, decision: MemoryWorkDecision, reason: str
    ) -> MemoryCommitResult:
        case.status = "awaiting_evidence"
        case.updated_at = datetime.now(timezone.utc)
        decision.policy_result = _policy_result(
            decision, commit_allowed=False, reason=reason, proposal=None
        )
        await self.db.flush()
        return MemoryCommitResult("held", None, reason)

    @staticmethod
    def _resolve(
        case: MemoryWorkCase, decision: MemoryWorkDecision, memory_id: str, reason: str
    ) -> None:
        case.active_memory_id = memory_id
        case.status = "resolved"
        case.resolved_at = datetime.now(timezone.utc)
        case.updated_at = datetime.now(timezone.utc)
        decision.memory_ids = list(dict.fromkeys([*(decision.memory_ids or []), memory_id]))
        decision.policy_result = _policy_result(
            decision, commit_allowed=True, reason=reason, proposal=None
        )


def _validate_evidence(
    *,
    evidence: Sequence[MemoryWorkEvidence],
    raw_events: Mapping[str, RawEvent],
    memory_type: MemoryType,
    epistemic_status: str,
    confidence: float,
    importance: float,
) -> str | None:
    if epistemic_status in FORMAL_MEMORY_BLOCKED_EPISTEMIC_STATUSES:
        return "non_user_assertion_cannot_become_user_memory"
    if memory_type == MemoryType.PERSONA_HYPOTHESIS:
        return "model_inference_cannot_auto_commit"
    if confidence < 0.5:
        return "confidence_below_automatic_governance_threshold"
    if importance < 0.3:
        return "importance_below_memory_threshold"
    for item in evidence:
        event = raw_events.get(item.raw_event_id)
        if event is None:
            continue
        if event.source_type == SourceType.CONVERSATION and not item.quote:
            return "conversation_user_quote_required"
    return None


def _policy_result(
    decision: MemoryWorkDecision,
    *,
    commit_allowed: bool,
    reason: str,
    proposal: Mapping[str, Any] | None,
) -> dict[str, Any]:
    current = dict(decision.policy_result or {})
    current.update(
        {
            "governance": "working-agent-v2.4",
            "commit_allowed": commit_allowed,
            "reason": reason,
        }
    )
    if proposal is not None:
        current["memory_proposal"] = _bounded_proposal(proposal)
    return current


def _bounded_proposal(proposal: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "memory_type": str(proposal.get("memory_type") or "fact")[:32],
        "title": str(proposal.get("title") or "")[:240],
        "content": str(proposal.get("content") or proposal.get("body") or "")[:8000],
        "importance": _score(proposal.get("importance", 0.5)),
        "confidence": _score(proposal.get("confidence", 0.5)),
        "sensitivity": str(proposal.get("sensitivity") or "normal")[:16],
        "entities": _tags(proposal),
    }


def _memory_type(value: object) -> MemoryType:
    try:
        return MemoryType(str(getattr(value, "value", value) or "fact"))
    except ValueError:
        return MemoryType.FACT


def _sensitivity(value: object) -> SensitivityLevel:
    try:
        return SensitivityLevel(str(getattr(value, "value", value) or "normal"))
    except ValueError:
        return SensitivityLevel.NORMAL


def _score(value: object) -> float:
    try:
        return max(0.0, min(float(value or 0.0), 1.0))
    except (TypeError, ValueError):
        return 0.0


def _tags(proposal: Mapping[str, Any]) -> list[str]:
    values = proposal.get("entities", proposal.get("tags", []))
    if not isinstance(values, list):
        return []
    return [str(item)[:100] for item in values if str(item).strip()][:50]


def _primary_source_type(raw_events: Mapping[str, RawEvent]) -> SourceType:
    priority = {
        SourceType.CONVERSATION: 0,
        SourceType.MANUAL: 1,
        SourceType.OBSIDIAN: 2,
        SourceType.FILE_IMPORT: 3,
    }
    return min((item.source_type for item in raw_events.values()), key=lambda item: priority.get(item, 99))


def _strictest_visibility(raw_events: Mapping[str, RawEvent]) -> VisibilityScope:
    rank = {
        VisibilityScope.PUBLIC: 0,
        VisibilityScope.PROJECT: 1,
        VisibilityScope.PERSONAL: 2,
        VisibilityScope.PRIVATE: 3,
    }
    return max((item.visibility_scope for item in raw_events.values()), key=rank.__getitem__)


def _same_memory(memory: CommittedMemory, memory_type: MemoryType, title: str, body: str) -> bool:
    return (
        memory.memory_type == memory_type
        and " ".join(memory.title.split()) == " ".join(title[:240].split())
        and " ".join(memory.body.split()) == " ".join(body[:8000].split())
    )


def _content_hash(user_id: str, memory_type: MemoryType, title: str, body: str) -> str:
    payload = f"{user_id}\n{memory_type.value}\n{' '.join(title.split())}\n{' '.join(body.split())}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


async def recover_ready_memory_commits(
    db: AsyncSession, *, limit: int = 100
) -> dict[str, Any]:
    """Resume durable MEMORY_READY decisions after a process or broker failure."""
    rows = (
        await db.execute(
            select(MemoryWorkDecision, MemoryWorkCase)
            .join(MemoryWorkCase, MemoryWorkCase.id == MemoryWorkDecision.case_id)
            .where(
                MemoryWorkDecision.state == "MEMORY_READY",
                MemoryWorkCase.status == "ready_to_commit",
            )
            .order_by(MemoryWorkDecision.created_at.asc())
            .limit(max(1, min(limit, 500)))
            .with_for_update(skip_locked=True)
        )
    ).all()
    service = MemoryCommitService(db)
    created: list[str] = []
    linked: list[str] = []
    skipped = 0
    for decision, case in rows:
        if decision.memory_ids:
            skipped += 1
            continue
        policy = decision.policy_result if isinstance(decision.policy_result, dict) else {}
        proposal = policy.get("memory_proposal")
        if not isinstance(proposal, dict):
            skipped += 1
            continue
        result = await service.materialize(case=case, decision=decision, proposal=proposal)
        if result.memory_id:
            if result.action == "created":
                created.append(result.memory_id)
            else:
                linked.append(result.memory_id)
    await db.flush()
    return {
        "scanned": len(rows),
        "created_memory_ids": created,
        "linked_memory_ids": linked,
        "skipped": skipped,
    }
