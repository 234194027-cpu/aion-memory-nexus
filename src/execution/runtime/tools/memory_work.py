"""Bounded, case-aware analysis tools for the V2.4 Working Agent."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.execution.models.memory_work import MemoryWorkCase
from src.execution.services.memory_case_service import MemoryCaseService, stable_proposition_key
from src.memory.models.raw_event import RawEvent
from src.memory.services.conflict_checker import ConflictChecker
from src.memory.services.governance_policy import derive_epistemic_status, source_trust_class
from src.memory.services.retrieval_engine import RetrievalEngine

from .base import RuntimeTool


def build_memory_work_tools(db: AsyncSession, *, shadow: bool) -> list[RuntimeTool]:
    cases = MemoryCaseService(db)

    async def route_memory_case(user_id: str, params: Mapping[str, Any]) -> dict:
        key = stable_proposition_key(
            memory_type=params.get("memory_type", "fact"),
            title=params.get("title"),
            content=params.get("content"),
            explicit_key=params.get("proposition_key"),
        )
        if shadow:
            return {"mode": "shadow", "proposition_key": key, "would_route": True}
        case = await cases.route_case(
            user_id=user_id,
            memory_type=params.get("memory_type", "fact"),
            title=str(params.get("title") or "待治理记忆线索"),
            content=str(params.get("content") or ""),
            sensitivity=params.get("sensitivity", "normal"),
            confidence=_score(params.get("confidence")),
            proposition_key=params.get("proposition_key"),
        )
        return {"case_id": case.id, "status": case.status, "proposition_key": case.proposition_key}

    async def attach_case_evidence(user_id: str, params: Mapping[str, Any]) -> dict:
        case, event = await _owned_case_event(db, user_id, params)
        if shadow:
            return {"mode": "shadow", "case_id": case.id, "raw_event_id": event.id, "would_attach": True}
        evidence = await cases.attach_evidence(
            case=case,
            event=event,
            relationship=str(params.get("relationship") or "supports"),
        )
        return {"evidence_id": evidence.id, "case_id": case.id, "relationship": evidence.relationship}

    async def search_related_context(user_id: str, params: Mapping[str, Any]) -> dict:
        query = str(params["query"])
        context = await RetrievalEngine(db).reconstruct_context(
            user_id=user_id,
            question=query,
            recall_level="work_context",
            top_k=min(max(int(params.get("top_k", 5)), 1), 20),
        )
        related_cases = list(
            (
                await db.execute(
                    select(MemoryWorkCase)
                    .where(MemoryWorkCase.user_id == user_id)
                    .order_by(MemoryWorkCase.updated_at.desc())
                    .limit(10)
                )
            ).scalars()
        )
        return {
            "relevant_memories": context.get("relevant_memories", []),
            "conflicts": context.get("conflicts", []),
            "recent_cases": [
                {"case_id": item.id, "title": item.title, "status": item.status, "proposition_key": item.proposition_key}
                for item in related_cases
            ],
        }

    async def evaluate_duplicate(user_id: str, params: Mapping[str, Any]) -> dict:
        key = stable_proposition_key(
            memory_type=params.get("memory_type", "fact"),
            title=params.get("title"),
            content=params.get("content"),
            explicit_key=params.get("proposition_key"),
        )
        existing_case = await db.scalar(
            select(MemoryWorkCase).where(
                MemoryWorkCase.user_id == user_id,
                MemoryWorkCase.proposition_key == key,
            )
        )
        return {
            "proposition_key": key,
            "duplicate_case_id": existing_case.id if existing_case else None,
            "relationship": "same_proposition" if existing_case else "new_proposition",
        }

    async def evaluate_conflict(user_id: str, params: Mapping[str, Any]) -> dict:
        return await ConflictChecker(db).check(
            user_id,
            {
                "title": params.get("title"),
                "body": params.get("content"),
                "memory_type": params.get("memory_type"),
            },
        )

    async def evaluate_governance(_user_id: str, params: Mapping[str, Any]) -> dict:
        source_type = params.get("source_type", "manual")
        return {
            "trust_class": source_trust_class(source_type),
            "epistemic_status": derive_epistemic_status(
                source_type,
                memory_type=params.get("memory_type"),
                direct_user_confirmation=False,
            ),
            "working_agent_commit_service_required": True,
            "model_direct_mutation_allowed": False,
        }

    async def request_evidence(user_id: str, params: Mapping[str, Any]) -> dict:
        case = await _owned_case(db, user_id, str(params["case_id"]))
        if not shadow:
            case.status = "awaiting_evidence"
            case.updated_at = datetime.now(timezone.utc)
        return {
            "case_id": case.id,
            "question": str(params["question"])[:500],
            "requirements": list(params.get("requirements") or ["direct_user_answer"])[:10],
            "resolution_condition": str(params.get("resolution_condition") or "user-authored evidence resolves the gap")[:500],
            "mode": "shadow" if shadow else "coordinator_handoff_required",
        }

    async def close_memory_case(user_id: str, params: Mapping[str, Any]) -> dict:
        case = await _owned_case(db, user_id, str(params["case_id"]))
        status = str(params.get("status") or "discarded")
        if status not in {"discarded", "resolved"}:
            status = "discarded"
        if not shadow:
            case.status = status
            case.resolved_at = datetime.now(timezone.utc)
        return {"case_id": case.id, "status": status, "mode": "shadow" if shadow else "active"}

    return [
        RuntimeTool(
            "route_memory_case",
            "Create or match a durable user-scoped memory case.",
            _schema(
                required=("title", "content"),
                properties={
                    "memory_type": {"type": "string"},
                    "title": {"type": "string"},
                    "content": {"type": "string"},
                    "sensitivity": {"type": "string"},
                    "confidence": {"type": "number"},
                    "proposition_key": {"type": "string"},
                },
            ),
            route_memory_case,
            read_only=shadow,
        ),
        RuntimeTool(
            "attach_case_evidence",
            "Attach a RawEvent to a memory case as support, contradiction, correction, or context.",
            _schema(
                required=("case_id", "raw_event_id"),
                properties={
                    "case_id": {"type": "string"},
                    "raw_event_id": {"type": "string"},
                    "relationship": {"type": "string"},
                },
            ),
            attach_case_evidence,
            read_only=shadow,
        ),
        RuntimeTool(
            "search_related_context",
            "Search governed memories and recent memory cases.",
            _schema(required=("query",), properties={"query": {"type": "string"}, "top_k": {"type": "integer"}}),
            search_related_context,
        ),
        RuntimeTool(
            "evaluate_duplicate",
            "Evaluate whether a proposition already has a durable case.",
            _schema(
                properties={
                    "memory_type": {"type": "string"},
                    "title": {"type": "string"},
                    "content": {"type": "string"},
                    "proposition_key": {"type": "string"},
                }
            ),
            evaluate_duplicate,
        ),
        RuntimeTool(
            "evaluate_conflict",
            "Evaluate semantic conflict against governed memories.",
            _schema(
                required=("content",),
                properties={
                    "title": {"type": "string"},
                    "content": {"type": "string"},
                    "memory_type": {"type": "string"},
                },
            ),
            evaluate_conflict,
        ),
        RuntimeTool(
            "evaluate_governance",
            "Apply provenance and formal-memory permission policy.",
            _schema(
                properties={
                    "source_type": {"type": "string"},
                    "memory_type": {"type": "string"},
                    "sensitivity": {"type": "string"},
                }
            ),
            evaluate_governance,
        ),
        RuntimeTool(
            "request_evidence",
            "Put a case into evidence-waiting state and return a structured handoff specification.",
            _schema(
                required=("case_id", "question"),
                properties={
                    "case_id": {"type": "string"},
                    "question": {"type": "string"},
                    "requirements": {"type": "array", "items": {"type": "string"}},
                    "resolution_condition": {"type": "string"},
                },
            ),
            request_evidence,
            read_only=shadow,
        ),
        RuntimeTool(
            "close_memory_case",
            "Close a valueless or resolved case without mutating formal memory.",
            _schema(
                required=("case_id",),
                properties={"case_id": {"type": "string"}, "status": {"type": "string"}},
            ),
            close_memory_case,
            read_only=shadow,
        ),
    ]


async def _owned_case(db: AsyncSession, user_id: str, case_id: str) -> MemoryWorkCase:
    case = await db.scalar(
        select(MemoryWorkCase).where(
            MemoryWorkCase.id == case_id,
            MemoryWorkCase.user_id == user_id,
        )
    )
    if case is None:
        raise LookupError("memory_case_not_found")
    return case


async def _owned_case_event(
    db: AsyncSession,
    user_id: str,
    params: Mapping[str, Any],
) -> tuple[MemoryWorkCase, RawEvent]:
    case = await _owned_case(db, user_id, str(params["case_id"]))
    event = await db.scalar(
        select(RawEvent).where(
            RawEvent.id == str(params["raw_event_id"]),
            RawEvent.user_id == user_id,
        )
    )
    if event is None:
        raise LookupError("raw_event_not_found")
    return case, event


def _schema(*, properties: Mapping[str, Any], required: tuple[str, ...] = ()) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": "object", "properties": dict(properties)}
    if required:
        schema["required"] = list(required)
    return schema


def _score(value: object) -> float:
    if not isinstance(value, (str, int, float)):
        return 0.0
    try:
        return max(0.0, min(float(value), 1.0))
    except ValueError:
        return 0.0
