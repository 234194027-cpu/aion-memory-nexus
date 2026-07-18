"""Read-only domain tools reserved for the conversational Runtime profile."""
from __future__ import annotations

import json
from typing import Any, Mapping

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.cognition.models.persona_snapshot import PersonaSnapshot
from src.cognition.services.conflict_graph_engine import get_user_conflicts
from src.execution.models.life_timeline_entry import LifeTimelineEntry
from src.execution.services.task_system import TaskSystem, task_to_response
from src.memory.services.retrieval_engine import RetrievalEngine
from src.memory.services.retrieval_plan import build_retrieval_plan
from src.platform.services.attention_service import AttentionService
from src.execution.services.conversation_knowledge import ConversationKnowledgeService

from .base import RuntimeTool


def _bounded_int(value: Any, *, default: int, lower: int = 1, upper: int = 20) -> int:
    if isinstance(value, bool):
        return default
    try:
        return max(lower, min(int(value), upper))
    except (TypeError, ValueError):
        return default


def build_conversation_tools(db: AsyncSession, *, source_message: str | None = None, channel: str = "system") -> list[RuntimeTool]:
    async def retrieve_memories(user_id: str, params: Mapping[str, Any]) -> dict:
        engine = RetrievalEngine(db)
        plan = build_retrieval_plan(params["query"], requested_top_k=_bounded_int(params.get("top_k"), default=5))
        context = await engine.reconstruct_context(
            user_id=user_id,
            question=params["query"],
            recall_level=plan.recall_level,
            top_k=plan.top_k,
        )
        context["retrieval_plan"] = {"intent": plan.intent.value, "recall_level": plan.recall_level, "top_k": plan.top_k}
        return context

    async def get_persona(user_id: str, _params: Mapping[str, Any]) -> dict:
        row = (await db.execute(
            select(PersonaSnapshot)
            .where(PersonaSnapshot.user_id == user_id)
            .order_by(PersonaSnapshot.created_at.desc())
            .limit(1)
        )).scalar_one_or_none()
        if row is None:
            return {"available": False}
        return {
            "available": True,
            "snapshot_date": row.snapshot_date,
            "summary": row.summary[:2000],
            "traits": json.loads(row.traits_json or "[]"),
        }

    async def get_conflicts(user_id: str, params: Mapping[str, Any]) -> dict:
        rows = await get_user_conflicts(db, user_id, limit=_bounded_int(params.get("limit"), default=5))
        return {"conflicts": rows}

    async def get_tasks(user_id: str, params: Mapping[str, Any]) -> dict:
        service = TaskSystem(db)
        rows = await service.list_tasks(user_id, status=params.get("status"), limit=_bounded_int(params.get("limit"), default=10))
        return {"tasks": [task_to_response(row) for row in rows]}

    async def get_timeline(user_id: str, params: Mapping[str, Any]) -> dict:
        rows = (await db.execute(
            select(LifeTimelineEntry)
            .where(LifeTimelineEntry.user_id == user_id)
            .order_by(LifeTimelineEntry.entry_date.desc(), LifeTimelineEntry.created_at.desc())
            .limit(_bounded_int(params.get("limit"), default=10))
        )).scalars().all()
        return {"entries": [{"id": row.id, "date": row.entry_date, "kind": row.entry_kind, "title": row.title, "snippet": (row.snippet or "")[:500]} for row in rows]}

    async def get_attention(user_id: str, params: Mapping[str, Any]) -> dict:
        items = await AttentionService(db).list_candidates(user_id=user_id, limit=_bounded_int(params.get("limit"), default=3, upper=10))
        return {"items": [{"source_type": item.source_type, "source_id": item.source_id, "priority": item.priority, "prompt": item.prompt} for item in items]}

    async def search_source_documents(user_id: str, params: Mapping[str, Any]) -> dict:
        return await ConversationKnowledgeService(db).search_source_documents(
            user_id=user_id,
            query=str(params.get("query") or ""),
            limit=_bounded_int(params.get("limit"), default=5, upper=10),
        )

    async def get_unconfirmed_memory_clues(user_id: str, params: Mapping[str, Any]) -> dict:
        return await ConversationKnowledgeService(db).get_unconfirmed_clues(
            user_id=user_id,
            query=str(params.get("query") or ""),
            limit=_bounded_int(params.get("limit"), default=5, upper=10),
        )

    return [
        RuntimeTool("retrieve_memories", "Retrieve governed memory context for the current user.", {"type": "object", "properties": {"query": {"type": "string"}, "recall_level": {"type": "string"}, "top_k": {"type": "integer"}}, "required": ["query"]}, retrieve_memories),
        RuntimeTool("get_persona", "Read the latest user persona summary.", {"type": "object"}, get_persona),
        RuntimeTool("get_conflicts", "Read unresolved memory conflicts.", {"type": "object", "properties": {"limit": {"type": "integer"}}}, get_conflicts),
        RuntimeTool("get_tasks", "Read the current user's tasks.", {"type": "object", "properties": {"status": {"type": "string"}, "limit": {"type": "integer"}}}, get_tasks),
        RuntimeTool("get_timeline", "Read recent timeline entries.", {"type": "object", "properties": {"limit": {"type": "integer"}}}, get_timeline),
        RuntimeTool("get_attention", "Read pre-gated active evidence needs and overdue tasks; this never sends a message.", {"type": "object", "properties": {"limit": {"type": "integer"}}}, get_attention),
        RuntimeTool(
            "search_source_documents",
            "Search bounded excerpts from this user's imported files, Obsidian notes, and extracted media. Results describe what a source says and are not automatically facts about the user.",
            {"type": "object", "properties": {"query": {"type": "string"}, "limit": {"type": "integer"}}, "required": ["query"]},
            search_source_documents,
        ),
        RuntimeTool(
            "get_unconfirmed_memory_clues",
            "Read topic-matched, non-sensitive memory-case clues that contain a verified user quote. Every result is unconfirmed and may only support one clarification or confirmation question.",
            {"type": "object", "properties": {"query": {"type": "string"}, "limit": {"type": "integer"}}, "required": ["query"]},
            get_unconfirmed_memory_clues,
        ),
    ]
