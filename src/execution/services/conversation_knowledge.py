"""User-scoped, read-only cognition shared with the conversational Agent."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import re
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.execution.models.agent_runtime import AgentHandoff, AgentHandoffStatus
from src.execution.models.memory_work import MemoryWorkCase, MemoryWorkEvidence
from src.memory.models.committed_memory import CommittedMemory
from src.memory.models.memory_source import MemorySource
from src.memory.models.raw_event import (
    RawEvent,
    SensitivityLevel,
    SourceType,
    VisibilityScope,
)
from src.platform.models.media_artifact import MediaArtifact


_DOCUMENT_EVENT_TYPES = frozenset({SourceType.FILE_IMPORT, SourceType.OBSIDIAN})
_OPEN_CASE_STATUSES = frozenset({
    "open",
    "awaiting_evidence",
    "candidate_ready",
    "conflict_review",
})
_SAFE_CLUE_SENSITIVITY = frozenset({"public", "normal"})
_RECENT_DOCUMENT_PATTERNS = (
    "刚上传",
    "刚导入",
    "最新文档",
    "最近文档",
    "最近的文档",
    "上一个文档",
    "这份文档",
    "这个文档",
)
_QUERY_STOPWORDS = frozenset({
    "什么", "怎么", "如何", "是否", "这个", "那个", "里面", "关于", "之前",
    "上传", "文档", "文件", "内容", "记录", "告诉", "帮我", "看看", "一下",
})


@dataclass(frozen=True, slots=True)
class _RankedItem:
    score: float
    occurred_at: datetime | None
    value: Any


def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value) or "")


def _query_terms(query: str) -> tuple[str, ...]:
    normalized = re.sub(r"\s+", " ", query or "").strip().casefold()
    if not normalized:
        return ()
    terms: list[str] = []
    for token in re.findall(r"[a-z0-9_\-.]{2,}|[\u4e00-\u9fff]{2,}", normalized):
        if token not in _QUERY_STOPWORDS:
            terms.append(token)
        if re.fullmatch(r"[\u4e00-\u9fff]{4,}", token):
            terms.extend(
                token[index:index + 2]
                for index in range(min(len(token) - 1, 12))
                if token[index:index + 2] not in _QUERY_STOPWORDS
            )
    return tuple(dict.fromkeys(terms))[:20]


def _score_text(query: str, terms: Iterable[str], *, title: str, body: str) -> float:
    normalized_query = re.sub(r"\s+", " ", query or "").strip().casefold()
    title_text = (title or "").casefold()
    body_text = (body or "").casefold()
    score = 0.0
    if normalized_query and normalized_query in title_text:
        score += 12.0
    if normalized_query and normalized_query in body_text:
        score += 8.0
    for term in terms:
        if term in title_text:
            score += 4.0
        if term in body_text:
            score += min(3.0, 1.0 + body_text.count(term) * 0.25)
    return score


def _excerpt(content: str, query: str, terms: Iterable[str], *, limit: int = 1_000) -> str:
    cleaned = re.sub(r"\s+", " ", (content or "").replace("\x00", " ")).strip()
    if len(cleaned) <= limit:
        return cleaned
    lowered = cleaned.casefold()
    needles = [query.strip().casefold(), *terms]
    positions = [lowered.find(item) for item in needles if item and lowered.find(item) >= 0]
    center = min(positions) if positions else 0
    start = max(0, center - limit // 3)
    end = min(len(cleaned), start + limit)
    prefix = "…" if start else ""
    suffix = "…" if end < len(cleaned) else ""
    return f"{prefix}{cleaned[start:end]}{suffix}"


class ConversationKnowledgeService:
    """Expose bounded shared cognition without granting filesystem access."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def search_source_documents(
        self,
        *,
        user_id: str,
        query: str,
        limit: int = 5,
    ) -> dict[str, Any]:
        query = (query or "").strip()
        limit = max(1, min(int(limit or 5), 10))
        if not query:
            return self._document_result(query=query, items=[])

        events = list((await self.db.execute(
            select(RawEvent)
            .where(
                RawEvent.user_id == user_id,
                RawEvent.source_type.in_((*_DOCUMENT_EVENT_TYPES, SourceType.MANUAL)),
                RawEvent.sensitivity.in_((
                    SensitivityLevel.PUBLIC,
                    SensitivityLevel.NORMAL,
                )),
                RawEvent.visibility_scope.in_((
                    VisibilityScope.PUBLIC,
                    VisibilityScope.PROJECT,
                    VisibilityScope.PERSONAL,
                )),
            )
            .order_by(RawEvent.occurred_at.desc())
            .limit(300)
        )).scalars())
        document_events = [event for event in events if self._is_document_event(event)]
        if not document_events:
            return self._document_result(query=query, items=[])

        artifact_ids = {
            str((event.event_metadata or {}).get("media_artifact_id"))
            for event in document_events
            if (event.event_metadata or {}).get("media_artifact_id")
        }
        artifacts = list((await self.db.execute(
            select(MediaArtifact).where(
                MediaArtifact.user_id == user_id,
                MediaArtifact.id.in_(artifact_ids),
            )
        )).scalars()) if artifact_ids else []
        artifact_by_id = {artifact.id: artifact for artifact in artifacts}

        event_ids = [event.id for event in document_events]
        source_rows = list((await self.db.execute(
            select(MemorySource.raw_event_id, MemorySource.memory_id, CommittedMemory.status)
            .join(CommittedMemory, CommittedMemory.id == MemorySource.memory_id)
            .where(
                CommittedMemory.user_id == user_id,
                MemorySource.raw_event_id.in_(event_ids),
            )
        )).all()) if event_ids else []
        memories_by_event: dict[str, list[str]] = {}
        for raw_event_id, memory_id, status in source_rows:
            if _enum_value(status) == "active":
                memories_by_event.setdefault(raw_event_id, []).append(memory_id)

        terms = _query_terms(query)
        ranked: list[_RankedItem] = []
        for event in document_events:
            metadata = event.event_metadata or {}
            artifact = artifact_by_id.get(str(metadata.get("media_artifact_id") or ""))
            title = str(
                metadata.get("note_title")
                or metadata.get("original_name")
                or metadata.get("obsidian_file")
                or (artifact.original_name if artifact else "")
                or event.source_id
                or "未命名来源"
            )
            content = (event.content or "").strip()
            if len(content) < 20:
                continue
            score = _score_text(query, terms, title=title, body=content)
            if score <= 0 and any(pattern in query for pattern in _RECENT_DOCUMENT_PATTERNS):
                score = 1.0
            if score <= 0:
                continue
            ranked.append(_RankedItem(score, event.occurred_at, (event, artifact, title)))

        ranked.sort(
            key=lambda item: (
                item.score,
                item.occurred_at.timestamp() if item.occurred_at else 0.0,
            ),
            reverse=True,
        )
        items: list[dict[str, Any]] = []
        for ranked_item in ranked[:limit]:
            event, artifact, title = ranked_item.value
            memory_ids = list(dict.fromkeys(memories_by_event.get(event.id, [])))
            items.append({
                "raw_event_id": event.id,
                "artifact_id": artifact.id if artifact else None,
                "title": title[:240],
                "excerpt": _excerpt(event.content, query, terms),
                "source_type": _enum_value(event.source_type),
                "occurred_at": event.occurred_at.isoformat() if event.occurred_at else None,
                "source_url": (
                    artifact.source_url
                    if artifact and artifact.source_url
                    else metadata.get("source_url") or metadata.get("obsidian_file")
                ),
                "related_memory_ids": memory_ids[:10],
                "review_state": "linked_to_active_memory" if memory_ids else "source_only",
                "epistemic_status": "document_statement",
                "allowed_use": ["answer_what_the_source_says", "quote_with_source_label"],
                "forbidden_use": ["assert_as_user_fact_without_confirmation"],
            })
        return self._document_result(query=query, items=items)

    async def get_unconfirmed_clues(
        self,
        *,
        user_id: str,
        query: str,
        limit: int = 5,
    ) -> dict[str, Any]:
        query = (query or "").strip()
        limit = max(1, min(int(limit or 5), 10))
        if not query:
            return self._clue_result(query=query, items=[])

        cases = list((await self.db.execute(
            select(MemoryWorkCase)
            .where(
                MemoryWorkCase.user_id == user_id,
                MemoryWorkCase.status.in_(_OPEN_CASE_STATUSES),
                MemoryWorkCase.sensitivity.in_(_SAFE_CLUE_SENSITIVITY),
            )
            .order_by(MemoryWorkCase.updated_at.desc())
            .limit(150)
        )).scalars())
        if not cases:
            return self._clue_result(query=query, items=[])

        case_ids = [case.id for case in cases]
        evidence_rows = list((await self.db.execute(
            select(MemoryWorkEvidence)
            .where(
                MemoryWorkEvidence.user_id == user_id,
                MemoryWorkEvidence.case_id.in_(case_ids),
                MemoryWorkEvidence.source_turn_id.is_not(None),
                MemoryWorkEvidence.quote.is_not(None),
                MemoryWorkEvidence.trust_class == "user_assertion",
            )
            .order_by(MemoryWorkEvidence.created_at.desc())
        )).scalars())
        evidence_by_case: dict[str, list[MemoryWorkEvidence]] = {}
        for evidence in evidence_rows:
            if (evidence.quote or "").strip():
                evidence_by_case.setdefault(evidence.case_id, []).append(evidence)

        handoffs = list((await self.db.execute(
            select(AgentHandoff)
            .where(
                AgentHandoff.user_id == user_id,
                AgentHandoff.case_id.in_(case_ids),
                AgentHandoff.mode == "active",
                AgentHandoff.status == AgentHandoffStatus.ACTIVE,
            )
            .order_by(AgentHandoff.created_at.desc())
        )).scalars())
        handoff_by_case: dict[str, AgentHandoff] = {}
        for handoff in handoffs:
            handoff_by_case.setdefault(str(handoff.case_id), handoff)

        terms = _query_terms(query)
        ranked: list[_RankedItem] = []
        for case in cases:
            evidence = evidence_by_case.get(case.id, [])
            if not evidence:
                continue
            evidence_text = " ".join((item.quote or "") for item in evidence[:5])
            score = _score_text(
                query,
                terms,
                title=case.title or "",
                body=f"{case.summary or ''} {evidence_text}",
            )
            if score <= 0:
                continue
            ranked.append(_RankedItem(score, case.updated_at, (case, evidence[0])))

        ranked.sort(
            key=lambda item: (
                item.score,
                item.occurred_at.timestamp() if item.occurred_at else 0.0,
            ),
            reverse=True,
        )
        items: list[dict[str, Any]] = []
        for ranked_item in ranked[:limit]:
            case, evidence = ranked_item.value
            handoff = handoff_by_case.get(case.id)
            items.append({
                "case_id": case.id,
                "status": "unconfirmed",
                "case_status": case.status,
                "case_type": case.case_type,
                "title": case.title[:240],
                "confidence": float(case.confidence or 0.0),
                "user_quote": (evidence.quote or "")[:1_000],
                "source_turn_id": evidence.source_turn_id,
                "source_event_id": evidence.raw_event_id,
                "occurred_at": evidence.occurred_at.isoformat() if evidence.occurred_at else None,
                "evidence_relationship": evidence.relationship,
                "missing_evidence": list(handoff.evidence_requirements or [])[:10] if handoff else [],
                "suggested_question": handoff.question[:500] if handoff else None,
                "why_ask": (handoff.evidence_payload or {}).get("why") if handoff else None,
                "allowed_use": ["clarify", "ask_one_confirmation_question"],
                "forbidden_use": [
                    "answer_as_fact",
                    "cite_as_committed_memory",
                    "write_formal_memory",
                    "proactive_sensitive_followup",
                ],
            })
        return self._clue_result(query=query, items=items)

    @staticmethod
    def _is_document_event(event: RawEvent) -> bool:
        if event.source_type in _DOCUMENT_EVENT_TYPES:
            return True
        metadata = event.event_metadata or {}
        return event.source_type == SourceType.MANUAL and (
            metadata.get("event_kind") == "media_note"
            or bool(metadata.get("media_artifact_id"))
        )

    @staticmethod
    def _document_result(*, query: str, items: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "query": query,
            "items": items,
            "result_kind": "document_sources",
            "usage_policy": (
                "These are source statements. Attribute them to the document; "
                "never silently convert them into facts about the user."
            ),
        }

    @staticmethod
    def _clue_result(*, query: str, items: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "query": query,
            "items": items,
            "result_kind": "unconfirmed_clues",
            "usage_policy": (
                "Every item is unconfirmed. Use it only to ask one natural clarification "
                "or confirmation question; never answer with it as fact."
            ),
        }
