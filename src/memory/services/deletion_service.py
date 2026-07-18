"""Consistent deletion of derived memory data and vector indexes."""

from __future__ import annotations

import json

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.cognition.models.knowledge_page import KnowledgePageMemory, KnowledgePageVersion
from src.execution.models.memory_relation import MemoryRelation
from src.memory.models.committed_memory import CommittedMemory, CommittedStatus
from src.memory.models.data_lifecycle_audit import DataLifecycleAudit
from src.memory.models.memory_embedding import MemoryEmbedding
from src.memory.models.memory_source import MemorySource
from src.shared.ids.id_generator import generate_lifecycle_audit_id


async def record_lifecycle_audit(
    db: AsyncSession,
    *,
    user_id: str,
    action: str,
    target_type: str,
    target_id: str,
    affected_counts: dict[str, int] | None = None,
) -> None:
    """Record lifecycle metadata without retaining any user content."""
    db.add(
        DataLifecycleAudit(
            id=generate_lifecycle_audit_id(),
            user_id=user_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            affected_counts=affected_counts or {},
        )
    )


async def rebuild_wiki_derivatives(db: AsyncSession, user_id: str) -> dict[str, int]:
    """Keep derived Wiki mappings aligned with the current ACTIVE memory set."""
    from src.cognition.services.knowledge_workspace import KnowledgeWorkspaceService

    result = await KnowledgeWorkspaceService(db).rebuild_wiki(user_id, commit=False)
    return {
        "wiki_pages": int(result["page_count"]),
        "wiki_associations": int(result["association_count"]),
    }


async def tombstone_memory(db: AsyncSession, memory: CommittedMemory) -> dict[str, int]:
    """Remove user content and derived indexes while preserving row identity."""
    graph_source_revision = memory.content_hash or str(memory.revision or 1)
    embedding_count = int(
        await db.scalar(select(func.count()).select_from(MemoryEmbedding).where(MemoryEmbedding.memory_id == memory.id))
        or 0
    )
    source_count = int(
        await db.scalar(select(func.count()).select_from(MemorySource).where(MemorySource.memory_id == memory.id))
        or 0
    )
    relation_filter = or_(
        MemoryRelation.source_memory_id == memory.id,
        MemoryRelation.target_memory_id == memory.id,
    )
    relation_count = int(
        await db.scalar(
            select(func.count()).select_from(MemoryRelation).where(
                MemoryRelation.user_id == memory.user_id,
                relation_filter,
            )
        )
        or 0
    )
    wiki_mapping_count = int(
        await db.scalar(
            select(func.count()).select_from(KnowledgePageMemory).where(
                KnowledgePageMemory.user_id == memory.user_id,
                KnowledgePageMemory.memory_id == memory.id,
            )
        )
        or 0
    )
    version_result = await db.execute(
        select(KnowledgePageVersion).where(KnowledgePageVersion.user_id == memory.user_id)
    )
    version_ids_to_remove = []
    for version in version_result.scalars().all():
        try:
            version_memory_ids = json.loads(version.memory_ids or "[]")
        except (TypeError, ValueError, json.JSONDecodeError):
            version_memory_ids = []
        if memory.id in version_memory_ids:
            version_ids_to_remove.append(version.id)
    await db.execute(delete(MemoryEmbedding).where(MemoryEmbedding.memory_id == memory.id))
    await db.execute(delete(MemorySource).where(MemorySource.memory_id == memory.id))
    await db.execute(
        delete(MemoryRelation).where(MemoryRelation.user_id == memory.user_id, relation_filter)
    )
    if version_ids_to_remove:
        await db.execute(delete(KnowledgePageVersion).where(KnowledgePageVersion.id.in_(version_ids_to_remove)))
    memory.title = "已删除记忆"
    memory.body = ""
    memory.tags = []
    memory.content_hash = None
    memory.embedding = None
    memory.status = CommittedStatus.DELETED
    from src.memory.services.graph_projection import queue_source_deletion

    await queue_source_deletion(
        db,
        user_id=memory.user_id,
        project_id=memory.project_id,
        source_kind="committed_memory",
        source_id=memory.id,
        source_revision=graph_source_revision,
    )
    wiki_counts = await rebuild_wiki_derivatives(db, memory.user_id)
    # A rebuild may add a retired-page snapshot from the former mapping; hard deletion
    # must remove that derived historical reference too.
    post_rebuild_versions = await db.execute(
        select(KnowledgePageVersion).where(KnowledgePageVersion.user_id == memory.user_id)
    )
    post_rebuild_ids = []
    for version in post_rebuild_versions.scalars().all():
        try:
            version_memory_ids = json.loads(version.memory_ids or "[]")
        except (TypeError, ValueError, json.JSONDecodeError):
            version_memory_ids = []
        if memory.id in version_memory_ids:
            post_rebuild_ids.append(version.id)
    if post_rebuild_ids:
        await db.execute(delete(KnowledgePageVersion).where(KnowledgePageVersion.id.in_(post_rebuild_ids)))
    return {
        "embeddings": embedding_count,
        "sources": source_count,
        "relations": relation_count,
        "wiki_mappings_removed": wiki_mapping_count,
        "wiki_versions_removed": len(set(version_ids_to_remove) | set(post_rebuild_ids)),
        "vector_index_cleanup_requested": 1,
        **wiki_counts,
    }


def delete_from_vector_index(memory_id: str) -> None:
    """Best-effort cleanup of the rebuildable external vector index."""
    from src.memory.services.vector_index_backend import get_vector_index_backend

    backend = get_vector_index_backend()
    if backend.is_available():
        backend.delete(memory_id)
