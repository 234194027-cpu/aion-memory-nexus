"""Source-backed graph, timeline, and deterministic Wiki aggregation service."""

from __future__ import annotations

import re
import json
from collections import defaultdict
from datetime import datetime, timezone
from typing import Iterable

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.cognition.models.knowledge_page import KnowledgePage, KnowledgePageMemory, KnowledgePageVersion
from src.execution.models.memory_relation import MemoryRelation
from src.memory.models.committed_memory import CommittedMemory, CommittedStatus
from src.memory.models.memory_source import MemorySource
from src.shared.ids.id_generator import (
    generate_knowledge_page_id,
    generate_knowledge_page_memory_id,
    generate_knowledge_page_version_id,
)


MAX_GRAPH_MEMORIES = 200
MAX_TIMELINE_MEMORIES = 300
_SLUG_PATTERN = re.compile(r"[^a-z0-9\u4e00-\u9fff]+")


def _enum_value(value: object) -> str:
    return str(getattr(value, "value", value) or "")


def _topic_from_tag(value: object) -> tuple[str, str] | None:
    if not isinstance(value, str):
        return None
    title = " ".join(value.strip().split())
    if not title or len(title) > 80:
        return None
    slug = _SLUG_PATTERN.sub("-", title.lower()).strip("-")
    return (slug[:160], title) if slug else None


def _safe_tags(memory: CommittedMemory) -> list[str]:
    tags = getattr(memory, "tags", None)
    return [tag for tag in tags if isinstance(tag, str)] if isinstance(tags, list) else []


def _summary(title: str, memories: Iterable[CommittedMemory]) -> str:
    items = list(memories)
    dates = [memory.valid_from for memory in items if memory.valid_from]
    if dates:
        start, end = min(dates).date().isoformat(), max(dates).date().isoformat()
        period = start if start == end else f"{start} 至 {end}"
        return f"自动聚合 {len(items)} 条与“{title}”相关的已提交记忆，时间范围为 {period}。"
    return f"自动聚合 {len(items)} 条与“{title}”相关的已提交记忆。"


def _confidence_state(value: float) -> str:
    if value < 0.5:
        return "low"
    if value < 0.75:
        return "review"
    return "supported"


class KnowledgeWorkspaceService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def graph(self, user_id: str, *, limit: int = 120) -> dict:
        limit = max(1, min(MAX_GRAPH_MEMORIES, limit))
        result = await self.db.execute(
            select(CommittedMemory)
            .where(CommittedMemory.user_id == user_id, CommittedMemory.status == CommittedStatus.ACTIVE)
            .order_by(CommittedMemory.importance.desc(), CommittedMemory.valid_from.desc())
            .limit(limit + 1)
        )
        memories = list(result.scalars().all())
        truncated = len(memories) > limit
        memories = memories[:limit]
        memory_ids = {memory.id for memory in memories}
        relations: list[MemoryRelation] = []
        if memory_ids:
            relation_result = await self.db.execute(
                select(MemoryRelation).where(
                    MemoryRelation.user_id == user_id,
                    MemoryRelation.source_memory_id.in_(memory_ids),
                    MemoryRelation.target_memory_id.in_(memory_ids),
                )
            )
            relations = list(relation_result.scalars().all())
        return {
            "nodes": [
                {
                    "id": memory.id,
                    "title": memory.title,
                    "memory_type": _enum_value(memory.memory_type),
                    "importance": float(memory.importance or 0.0),
                    "confidence": float(memory.confidence or 0.0),
                    "sensitivity": _enum_value(memory.sensitivity),
                    "occurred_at": memory.valid_from.isoformat() if memory.valid_from else None,
                }
                for memory in memories
            ],
            "edges": [
                {
                    "id": relation.id,
                    "source": relation.source_memory_id,
                    "target": relation.target_memory_id,
                    "relation_type": relation.relation_type,
                    "confidence": float(relation.confidence or 0.0),
                    "reason": relation.reason,
                    "valid_from": relation.valid_from.isoformat() if relation.valid_from else None,
                    "valid_until": relation.valid_until.isoformat() if relation.valid_until else None,
                    "created_at": relation.created_at.isoformat() if relation.created_at else None,
                }
                for relation in relations
            ],
            "truncated": truncated,
        }

    async def timeline(self, user_id: str, *, limit: int = 100) -> dict:
        limit = max(1, min(MAX_TIMELINE_MEMORIES, limit))
        result = await self.db.execute(
            select(CommittedMemory)
            .where(CommittedMemory.user_id == user_id, CommittedMemory.status == CommittedStatus.ACTIVE)
            .order_by(CommittedMemory.valid_from.desc(), CommittedMemory.created_at.desc())
            .limit(limit + 1)
        )
        memories = list(result.scalars().all())
        truncated = len(memories) > limit
        entries = []
        for memory in memories[:limit]:
            timestamp = memory.valid_from or memory.created_at
            if timestamp is None:
                continue
            entries.append(
                {
                    "memory_id": memory.id,
                    "title": memory.title,
                    "memory_type": _enum_value(memory.memory_type),
                    "occurred_at": timestamp.isoformat(),
                    "time_basis": "occurred_at" if memory.valid_from else "recorded_at",
                    "confidence": float(memory.confidence or 0.0),
                    "importance": float(memory.importance or 0.0),
                    "tags": _safe_tags(memory),
                    "epistemic_status": memory.epistemic_status,
                }
            )
        return {"entries": entries, "truncated": truncated}

    async def rebuild_wiki(self, user_id: str, *, commit: bool = True) -> dict:
        result = await self.db.execute(
            select(CommittedMemory).where(
                CommittedMemory.user_id == user_id,
                CommittedMemory.status == CommittedStatus.ACTIVE,
            )
        )
        memberships: dict[str, dict[str, str]] = defaultdict(dict)
        labels: dict[str, str] = {}
        memories_by_id: dict[str, CommittedMemory] = {}
        for memory in result.scalars().all():
            memories_by_id[memory.id] = memory
            for tag in _safe_tags(memory):
                topic = _topic_from_tag(tag)
                if topic is None:
                    continue
                slug, title = topic
                labels.setdefault(slug, title)
                memberships[slug][memory.id] = "tag"

        try:
            existing_result = await self.db.execute(
                select(KnowledgePage).where(KnowledgePage.user_id == user_id)
            )
            existing = {page.slug: page for page in existing_result.scalars().all()}
            existing_membership_result = await self.db.execute(
                select(KnowledgePageMemory).where(KnowledgePageMemory.user_id == user_id)
            )
            existing_memory_ids: dict[str, set[str]] = defaultdict(set)
            for membership in existing_membership_result.scalars().all():
                existing_memory_ids[membership.page_id].add(membership.memory_id)
            versioned_page_ids = set(
                (await self.db.execute(
                    select(KnowledgePageVersion.page_id).where(KnowledgePageVersion.user_id == user_id)
                )).scalars().all()
            )
            now = datetime.now(timezone.utc)
            active_slugs = set(memberships)
            for old_slug, old_page in existing.items():
                if old_slug in active_slugs:
                    continue
                self.db.add(
                    KnowledgePageVersion(
                        id=generate_knowledge_page_version_id(),
                        user_id=user_id,
                        page_id=old_page.id,
                        slug=old_page.slug,
                        title=old_page.title,
                        summary=old_page.summary,
                        confidence=float(old_page.confidence or 0.0),
                        source_count=int(old_page.source_count or 0),
                        memory_ids=json.dumps(sorted(existing_memory_ids[old_page.id])),
                        change_reason="no_active_members",
                        generated_at=now,
                    )
                )
            await self.db.execute(delete(KnowledgePageMemory).where(KnowledgePageMemory.user_id == user_id))
            if active_slugs:
                await self.db.execute(
                    delete(KnowledgePage).where(
                        KnowledgePage.user_id == user_id,
                        KnowledgePage.slug.not_in(active_slugs),
                    )
                )
            else:
                await self.db.execute(delete(KnowledgePage).where(KnowledgePage.user_id == user_id))

            association_count = 0
            for slug, memory_basis in memberships.items():
                topic_memories = [memories_by_id[memory_id] for memory_id in memory_basis]
                page = existing.get(slug)
                confidence = sum(float(memory.confidence or 0.0) for memory in topic_memories) / len(topic_memories)
                if page is None:
                    page = KnowledgePage(id=generate_knowledge_page_id(), user_id=user_id, slug=slug)
                    self.db.add(page)
                previous_memory_ids = existing_memory_ids[page.id]
                next_memory_ids = set(memory_basis)
                next_summary = _summary(labels[slug], topic_memories)
                page_was_new = page.id not in versioned_page_ids
                if page_was_new:
                    change_reason = "initial_aggregation"
                elif previous_memory_ids != next_memory_ids:
                    change_reason = "membership_changed"
                elif page.summary != next_summary or float(page.confidence or 0.0) != confidence:
                    change_reason = "derived_summary_changed"
                else:
                    change_reason = ""
                page.title = labels[slug]
                page.summary = next_summary
                page.confidence = confidence
                page.source_count = len(topic_memories)
                page.status = "active"
                page.generated_at = now
                if change_reason:
                    self.db.add(
                        KnowledgePageVersion(
                            id=generate_knowledge_page_version_id(),
                            user_id=user_id,
                            page_id=page.id,
                            slug=page.slug,
                            title=page.title,
                            summary=page.summary,
                            confidence=confidence,
                            source_count=len(topic_memories),
                            memory_ids=json.dumps(sorted(next_memory_ids)),
                            change_reason=change_reason,
                            generated_at=now,
                        )
                    )
                for memory_id, basis in memory_basis.items():
                    self.db.add(
                        KnowledgePageMemory(
                            id=generate_knowledge_page_memory_id(),
                            user_id=user_id,
                            page_id=page.id,
                            memory_id=memory_id,
                            relation_basis=basis,
                            confidence=float(memories_by_id[memory_id].confidence or 0.0),
                        )
                    )
                    association_count += 1
            if commit:
                await self.db.commit()
            else:
                await self.db.flush()
        except Exception:
            await self.db.rollback()
            raise
        return {"page_count": len(memberships), "association_count": association_count, "generated_at": now.isoformat()}

    async def list_wiki(self, user_id: str) -> list[dict]:
        page_result = await self.db.execute(
            select(KnowledgePage)
            .where(KnowledgePage.user_id == user_id, KnowledgePage.status == "active")
            .order_by(KnowledgePage.source_count.desc(), KnowledgePage.title.asc())
        )
        pages = list(page_result.scalars().all())
        if not pages:
            return []
        page_ids = [page.id for page in pages]
        association_result = await self.db.execute(
            select(KnowledgePageMemory).where(KnowledgePageMemory.page_id.in_(page_ids))
        )
        membership: dict[str, set[str]] = defaultdict(set)
        for item in association_result.scalars().all():
            membership[item.page_id].add(item.memory_id)
        version_result = await self.db.execute(
            select(KnowledgePageVersion)
            .where(KnowledgePageVersion.user_id == user_id, KnowledgePageVersion.page_id.in_(page_ids))
            .order_by(KnowledgePageVersion.created_at.desc())
        )
        latest_reason: dict[str, str] = {}
        for version in version_result.scalars().all():
            latest_reason.setdefault(version.page_id, version.change_reason)
        return [
            {
                "slug": page.slug,
                "title": page.title,
                "summary": page.summary,
                "confidence": float(page.confidence or 0.0),
                "confidence_state": _confidence_state(float(page.confidence or 0.0)),
                "source_count": page.source_count,
                "generated_at": page.generated_at.isoformat() if page.generated_at else "",
                "last_change_reason": latest_reason.get(page.id),
                "related_slugs": [
                    other.slug for other in pages
                    if other.id != page.id and membership[page.id] & membership[other.id]
                ][:12],
            }
            for page in pages
        ]

    async def wiki_detail(self, user_id: str, slug: str) -> dict | None:
        page_result = await self.db.execute(
            select(KnowledgePage).where(
                KnowledgePage.user_id == user_id,
                KnowledgePage.slug == slug,
                KnowledgePage.status == "active",
            )
        )
        page = page_result.scalar_one_or_none()
        if page is None:
            return None
        association_result = await self.db.execute(
            select(KnowledgePageMemory).where(KnowledgePageMemory.page_id == page.id)
        )
        associations = list(association_result.scalars().all())
        association_by_memory = {item.memory_id: item for item in associations}
        memory_ids = [item.memory_id for item in associations]
        memory_result = await self.db.execute(
            select(CommittedMemory).where(
                CommittedMemory.user_id == user_id,
                CommittedMemory.id.in_(memory_ids),
                CommittedMemory.status == CommittedStatus.ACTIVE,
            )
        ) if memory_ids else None
        memories = list(memory_result.scalars().all()) if memory_result else []
        source_result = await self.db.execute(
            select(MemorySource).where(MemorySource.memory_id.in_([memory.id for memory in memories]))
        ) if memories else None
        sources = list(source_result.scalars().all()) if source_result else []
        version_result = await self.db.execute(
            select(KnowledgePageVersion)
            .where(KnowledgePageVersion.user_id == user_id, KnowledgePageVersion.page_id == page.id)
            .order_by(KnowledgePageVersion.created_at.desc())
            .limit(20)
        )
        versions = list(version_result.scalars().all())
        listing = next(item for item in await self.list_wiki(user_id) if item["slug"] == slug)
        listing["memories"] = [
            {
                "id": memory.id,
                "title": memory.title,
                "memory_type": _enum_value(memory.memory_type),
                "confidence": float(memory.confidence or 0.0),
                "confidence_state": _confidence_state(float(memory.confidence or 0.0)),
                "epistemic_status": memory.epistemic_status,
                "relation_basis": association_by_memory[memory.id].relation_basis,
                "importance": float(memory.importance or 0.0),
                "occurred_at": memory.valid_from.isoformat() if memory.valid_from else None,
            }
            for memory in sorted(memories, key=lambda item: item.valid_from or item.created_at, reverse=True)
        ]
        listing["source_refs"] = [
            {
                "memory_id": source.memory_id,
                "raw_event_id": source.raw_event_id,
                "source_type": _enum_value(source.source_type),
                "quote": source.quote,
            }
            for source in sources
        ]
        listing["version_history"] = [
            {
                "generated_at": version.generated_at.isoformat(),
                "change_reason": version.change_reason,
                "source_count": version.source_count,
                "confidence": float(version.confidence or 0.0),
                "memory_count": len(json.loads(version.memory_ids or "[]")),
            }
            for version in versions
        ]
        return listing
