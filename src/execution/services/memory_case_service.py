"""Authoritative persistence boundary for Working-Agent memory cases."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.execution.models.memory_work import MemoryWorkCase, MemoryWorkDecision, MemoryWorkEvidence
from src.execution.models.conversation import ConversationTurn
from src.memory.models.raw_event import RawEvent
from src.memory.services.governance_policy import source_trust_class
from src.shared.ids.id_generator import generate_id


CASE_STATE_MAP = {
    "MEMORY_READY": "ready_to_commit",
    "DISCARDED": "discarded",
    "NEEDS_MORE_EVIDENCE": "awaiting_evidence",
    "CONFLICT_REVIEW": "conflict_review",
    "USER_CONFIRMATION_REQUIRED": "awaiting_evidence",
}


def stable_proposition_key(
    *,
    memory_type: object,
    title: object,
    content: object,
    explicit_key: object = None,
) -> str:
    explicit = str(explicit_key or "").strip().lower()
    if re.fullmatch(r"[a-z0-9:_-]{8,64}", explicit):
        return hashlib.sha256(explicit.encode("utf-8")).hexdigest()
    title_text = " ".join(str(title or "").lower().split())
    basis = title_text if title_text else " ".join(str(content or "").lower().split())[:500]
    normalized = f"{getattr(memory_type, 'value', memory_type) or 'fact'} {basis}"
    return hashlib.sha256(normalized[:4000].encode("utf-8")).hexdigest()


def stable_decision_key(*, event_id: str, case_id: str, state: str, proposal: Mapping[str, Any] | None) -> str:
    canonical = json.dumps(proposal or {}, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(f"work-v2.4:{event_id}:{case_id}:{state}:{canonical}".encode("utf-8")).hexdigest()


class MemoryCaseService:
    """Authoritative persistence boundary for cases, evidence and decisions."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def route_case(
        self,
        *,
        user_id: str,
        memory_type: object,
        title: str,
        content: str,
        sensitivity: object,
        confidence: float,
        proposition_key: object = None,
        preferred_case_id: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> MemoryWorkCase:
        if preferred_case_id:
            preferred = await self.db.scalar(
                select(MemoryWorkCase).where(
                    MemoryWorkCase.id == preferred_case_id,
                    MemoryWorkCase.user_id == user_id,
                )
            )
            if preferred is not None:
                return preferred

        key = stable_proposition_key(
            memory_type=memory_type,
            title=title,
            content=content,
            explicit_key=proposition_key,
        )
        existing = await self.db.scalar(
            select(MemoryWorkCase).where(
                MemoryWorkCase.user_id == user_id,
                MemoryWorkCase.proposition_key == key,
            )
        )
        if existing is not None:
            existing.title = title[:240] or existing.title
            existing.summary = content[:4000] or existing.summary
            existing.confidence = max(float(existing.confidence or 0.0), _bounded_score(confidence))
            existing.version = int(existing.version or 1) + 1
            existing.updated_at = datetime.now(timezone.utc)
            return existing

        sensitivity_value = getattr(sensitivity, "value", sensitivity) or "normal"
        memory_type_value = getattr(memory_type, "value", memory_type) or "fact"
        case = MemoryWorkCase(
            id=generate_id("mwc"),
            user_id=user_id,
            proposition_key=key,
            case_type=str(memory_type_value)[:32],
            title=(title or "未命名记忆案件")[:240],
            summary=(content or "")[:4000],
            status="open",
            sensitivity=str(sensitivity_value)[:16],
            confidence=_bounded_score(confidence),
            version=1,
            case_metadata=dict(metadata or {}),
        )
        self.db.add(case)
        await self.db.flush()
        return case

    async def attach_evidence(
        self,
        *,
        case: MemoryWorkCase,
        event: RawEvent | Mapping[str, Any],
        relationship: str = "supports",
    ) -> MemoryWorkEvidence:
        event_id = str(_value(event, "id") or "")
        if not event_id:
            raise ValueError("raw_event_id_required")
        relationship = relationship if relationship in {"supports", "contradicts", "corrects", "context"} else "context"
        existing = await self.db.scalar(
            select(MemoryWorkEvidence).where(
                MemoryWorkEvidence.case_id == case.id,
                MemoryWorkEvidence.raw_event_id == event_id,
                MemoryWorkEvidence.relationship == relationship,
            )
        )
        if existing is not None:
            return existing

        metadata = _metadata(event)
        source_type = getattr(_value(event, "source_type"), "value", _value(event, "source_type")) or "manual"
        if source_type == "conversation":
            source_turn_id = _string_or_none(metadata.get("source_turn_id"))
            if not source_turn_id:
                raise ValueError("conversation_source_turn_required")
            source_turn = await self.db.scalar(
                select(ConversationTurn).where(
                    ConversationTurn.id == source_turn_id,
                    ConversationTurn.user_id == case.user_id,
                    ConversationTurn.role == "user",
                )
            )
            if source_turn is None:
                raise ValueError("conversation_user_turn_evidence_required")
        quote = _grounded_quote(event, metadata)
        if source_type == "conversation" and quote and quote not in source_turn.content:
            raise ValueError("conversation_quote_must_match_user_turn")
        evidence = MemoryWorkEvidence(
            id=generate_id("mwe"),
            case_id=case.id,
            user_id=case.user_id,
            raw_event_id=event_id,
            source_turn_id=_string_or_none(metadata.get("source_turn_id")),
            episode_id=_string_or_none(metadata.get("episode_id")),
            quote=quote,
            relationship=relationship,
            source_type=str(source_type)[:32],
            trust_class=source_trust_class(source_type),
            occurred_at=_value(event, "occurred_at"),
            evidence_metadata={
                "source_turn_ids": list(metadata.get("source_turn_ids") or [])[:20],
                "runtime_handoff_response": bool(metadata.get("runtime_handoff_response")),
                "correction_of_event_id": metadata.get("correction_of_event_id"),
            },
        )
        self.db.add(evidence)
        await self.db.flush()
        return evidence

    async def record_decision(
        self,
        *,
        case: MemoryWorkCase,
        user_id: str,
        event_id: str,
        state: str,
        run_id: str | None,
        proposal: Mapping[str, Any] | None,
        rationale: str | None,
        model: str | None,
        prompt_id: str | None,
        prompt_version: str | None,
        duplicate_refs: Sequence[str] = (),
        conflict_refs: Sequence[str] = (),
        rationale_codes: Sequence[str] = (),
        policy_result: Mapping[str, Any] | None = None,
    ) -> MemoryWorkDecision:
        key = stable_decision_key(event_id=event_id, case_id=case.id, state=state, proposal=proposal)
        existing = await self.db.scalar(
            select(MemoryWorkDecision).where(MemoryWorkDecision.idempotency_key == key)
        )
        if existing is not None:
            return existing
        decision = MemoryWorkDecision(
            id=generate_id("mwd"),
            case_id=case.id,
            user_id=user_id,
            source_run_id=run_id,
            source_event_id=event_id,
            state=state,
            rationale=(rationale or "")[:4000],
            rationale_codes=[str(item)[:80] for item in rationale_codes][:30],
            duplicate_refs=[str(item)[:128] for item in duplicate_refs][:50],
            conflict_refs=[str(item)[:128] for item in conflict_refs][:50],
            memory_ids=[],
            policy_result=dict(policy_result or {"commit_allowed": False}),
            model=(model or "")[:128] or None,
            prompt_id=(prompt_id or "")[:96] or None,
            prompt_version=(prompt_version or "")[:32] or None,
            idempotency_key=key,
        )
        self.db.add(decision)
        await self.db.flush()
        return decision

    def apply_state(self, case: MemoryWorkCase, state: str) -> None:
        case.status = CASE_STATE_MAP.get(state, "failed")
        case.updated_at = datetime.now(timezone.utc)
        if case.status in {"discarded", "resolved"}:
            case.resolved_at = datetime.now(timezone.utc)

def _value(value: RawEvent | Mapping[str, Any], name: str) -> Any:
    return value.get(name) if isinstance(value, Mapping) else getattr(value, name, None)


def _metadata(value: RawEvent | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(value, Mapping):
        raw = value.get("metadata", value.get("event_metadata"))
    else:
        raw = value.event_metadata
    return dict(raw or {}) if isinstance(raw, Mapping) else {}


def _grounded_quote(event: RawEvent | Mapping[str, Any], metadata: Mapping[str, Any]) -> str | None:
    source_type = getattr(_value(event, "source_type"), "value", _value(event, "source_type"))
    content = str(_value(event, "content") or "").strip()
    explicit = metadata.get("user_quote") or metadata.get("quote")
    if source_type == "conversation":
        quote = str(explicit or content).strip()
        return quote[:2000] or None
    return str(explicit).strip()[:2000] if explicit else None


def _string_or_none(value: object) -> str | None:
    text = str(value or "").strip()
    return text[:64] or None


def _bounded_score(value: object) -> float:
    try:
        return max(0.0, min(float(value or 0.0), 1.0))
    except (TypeError, ValueError):
        return 0.0
