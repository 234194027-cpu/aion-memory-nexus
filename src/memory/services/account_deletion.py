"""Transactional account erasure across the database truth and rebuildable mirrors."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.execution.models.agent_runtime import AgentRun, AgentStep
from src.execution.models.memory_operations import EvidenceSeal
from src.execution.runtime.workspace import AgentWorkspaceService
from src.memory.models.committed_memory import CommittedMemory
from src.memory.models.memory_embedding import MemoryEmbedding
from src.memory.models.memory_source import MemorySource
from src.memory.models.raw_event import RawEvent
from src.platform.models.media_artifact import MediaArtifact
from src.shared.config import MEDIA_STORAGE_DIR
from src.shared.db.database import Base, _import_all_models


def _safe_media_path(relative: str | None) -> Path | None:
    if not relative:
        return None
    root = MEDIA_STORAGE_DIR.resolve()
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate


class AccountDeletionService:
    """Delete one user's data without touching global runtime configuration."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # Child-first and intentionally explicit.  This is safer than relying on
    # MetaData.sorted_tables, because the work-case/current-memory pointers form
    # a legitimate SET NULL cycle in the schema.
    _USER_TABLE_DELETE_ORDER = (
        "agent_handoffs",
        "conversation_attention_candidates",
        "conversation_reflection_cursors",
        "conversation_episodes",
        "conversation_turns",
        "memory_maintenance_actions",
        "memory_work_evidence",
        "knowledge_page_memories",
        "decision_reviews",
        "media_artifacts",
        "memory_relations",
        "memory_state_transitions",
        "memory_work_decisions",
        "memory_work_cases",
        "committed_memories",
        "raw_events",
        "evidence_seals",
        "memory_maintenance_runs",
        "agent_runs",
        "agent_sessions",
        "knowledge_page_versions",
        "knowledge_pages",
        "decision_records",
        "advisor_sessions",
        "agent_permissions",
        "agent_profiles",
        "audit_logs",
        "belief_systems",
        "conflict_graph_edges",
        "conflict_records",
        "custom_llm_providers",
        "data_lifecycle_audits",
        "graph_projections",
        "graph_replay_checkpoints",
        "graph_shadow_observations",
        "insight_proposals",
        "life_tasks",
        "life_timeline_entries",
        "memory_maintenance_controls",
        "obsidian_sync_records",
        "persona_snapshots",
        "simulation_runs",
        "user_memory_briefs",
        "wecom_contacts",
        "weekly_reviews",
    )

    async def delete_for_user(self, user_id: str) -> dict[str, Any]:
        _import_all_models()
        memory_ids = list(
            (await self.db.execute(select(CommittedMemory.id).where(CommittedMemory.user_id == user_id))).scalars()
        )
        event_ids = list(
            (await self.db.execute(select(RawEvent.id).where(RawEvent.user_id == user_id))).scalars()
        )
        seal_ids = list(
            (await self.db.execute(select(EvidenceSeal.id).where(EvidenceSeal.user_id == user_id))).scalars()
        )
        run_ids = list(
            (await self.db.execute(select(AgentRun.id).where(AgentRun.user_id == user_id))).scalars()
        )
        media_rows = list(
            (await self.db.execute(select(MediaArtifact).where(MediaArtifact.user_id == user_id))).scalars()
        )
        media_paths = {
            path
            for artifact in media_rows
            for path in (
                _safe_media_path(artifact.storage_path),
                _safe_media_path(artifact.extracted_text_path),
                _safe_media_path(artifact.extracted_json_path),
            )
            if path is not None
        }

        counts: dict[str, int] = {}
        if run_ids:
            result = await self.db.execute(delete(AgentStep).where(AgentStep.run_id.in_(run_ids)))
            counts["agent_steps"] = int(result.rowcount or 0)
        if memory_ids:
            result = await self.db.execute(delete(MemoryEmbedding).where(MemoryEmbedding.memory_id.in_(memory_ids)))
            counts["memory_embeddings"] = int(result.rowcount or 0)
        source_filters = []
        if memory_ids:
            source_filters.append(MemorySource.memory_id.in_(memory_ids))
        if event_ids:
            source_filters.append(MemorySource.raw_event_id.in_(event_ids))
        if seal_ids:
            source_filters.append(MemorySource.evidence_seal_id.in_(seal_ids))
        if source_filters:
            from sqlalchemy import or_

            result = await self.db.execute(delete(MemorySource).where(or_(*source_filters)))
            counts["memory_sources"] = int(result.rowcount or 0)

        # Break the three SET NULL work-case pointers before deleting either
        # side. This makes the operation portable to PostgreSQL with FK checks.
        committed = Base.metadata.tables["committed_memories"]
        work_cases = Base.metadata.tables["memory_work_cases"]
        await self.db.execute(
            update(committed)
            .where(committed.c.user_id == user_id)
            .values(source_work_case_id=None, source_work_decision_id=None)
        )
        await self.db.execute(
            update(work_cases)
            .where(work_cases.c.user_id == user_id)
            .values(active_memory_id=None)
        )

        for table_name in self._USER_TABLE_DELETE_ORDER:
            table = Base.metadata.tables[table_name]
            result = await self.db.execute(delete(table).where(table.c.user_id == user_id))
            counts[table.name] = counts.get(table.name, 0) + int(result.rowcount or 0)

        users = Base.metadata.tables["users"]
        result = await self.db.execute(delete(users).where(users.c.id == user_id))
        counts["users"] = int(result.rowcount or 0)
        await self.db.commit()

        from src.memory.services.vector_index_backend import get_vector_index_backend

        vector_delete_failures = 0
        try:
            backend = get_vector_index_backend()
            if backend.is_available():
                for memory_id in memory_ids:
                    try:
                        backend.delete(memory_id)
                    except Exception:
                        vector_delete_failures += 1
        except Exception:
            vector_delete_failures = len(memory_ids)

        deleted_files = 0
        for path in sorted(media_paths, key=lambda item: len(item.parts), reverse=True):
            if path.is_file():
                try:
                    path.unlink()
                    deleted_files += 1
                except OSError:
                    continue
        try:
            workspace_deleted = AgentWorkspaceService().delete_user_workspace(user_id=user_id)
        except OSError:
            workspace_deleted = False
        from src.shared.llm.providers import clear_llm_runtime_caches

        clear_llm_runtime_caches()
        return {
            "status": "deleted",
            "counts": counts,
            "media_files_deleted": deleted_files,
            "vector_delete_failures": vector_delete_failures,
            "workspace_deleted": workspace_deleted,
        }
