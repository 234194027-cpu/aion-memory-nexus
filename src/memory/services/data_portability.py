"""Account-scoped, privacy-safe data portability export."""

from __future__ import annotations

from datetime import date, datetime, timezone
from enum import Enum
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.cognition.models.knowledge_page import KnowledgePage, KnowledgePageMemory, KnowledgePageVersion
from src.execution.models.memory_relation import MemoryRelation
from src.execution.models.user import User
from src.execution.models.agent_runtime import AgentRole, AgentSession
from src.execution.models.conversation import (
    ConversationAttentionCandidate,
    ConversationEpisode,
    ConversationReflectionCursor,
    ConversationTurn,
)
from src.execution.models.memory_work import MemoryWorkCase, MemoryWorkDecision, MemoryWorkEvidence
from src.execution.runtime.workspace import AgentWorkspaceService
from src.memory.models.committed_memory import CommittedMemory
from src.memory.models.data_lifecycle_audit import DataLifecycleAudit
from src.memory.models.memory_source import MemorySource
from src.memory.models.obsidian_sync_record import ObsidianSyncRecord
from src.memory.models.raw_event import RawEvent
from src.platform.models.media_artifact import MediaArtifact


EXPORT_FORMAT = "life-memory-export/v3"


def _json_safe(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _serialize(model: Any, fields: Iterable[str]) -> dict[str, Any]:
    return {field: _json_safe(getattr(model, field)) for field in fields}


RAW_EVENT_FIELDS = (
    "id", "source_type", "source_id", "agent_id", "user_id", "project_id", "repo_id", "workspace_id",
    "occurred_at", "ingested_at", "content", "content_hash", "event_metadata", "sensitivity",
    "visibility_scope", "processing_status",
)
MEMORY_FIELDS = (
    "id", "source_work_case_id", "source_work_decision_id", "origin_kind", "revision",
    "automation_metadata", "user_id", "project_id", "repo_id", "workspace_id", "memory_type", "title",
    "body", "confidence", "importance", "sensitivity", "epistemic_status", "visibility_scope", "status", "valid_from",
    "valid_until", "tags", "content_hash", "created_at", "updated_at", "last_accessed_at",
)
SOURCE_FIELDS = ("id", "memory_id", "raw_event_id", "quote", "location", "source_type", "created_at")
RELATION_FIELDS = ("id", "user_id", "source_memory_id", "target_memory_id", "relation_type", "reason", "confidence", "created_at")
WIKI_PAGE_FIELDS = ("id", "user_id", "slug", "title", "summary", "confidence", "source_count", "status", "generated_at", "created_at", "updated_at")
WIKI_MEMBERSHIP_FIELDS = ("id", "user_id", "page_id", "memory_id", "relation_basis", "confidence", "created_at")
WIKI_VERSION_FIELDS = ("id", "user_id", "page_id", "slug", "title", "summary", "confidence", "source_count", "memory_ids", "change_reason", "generated_at", "created_at")
AUDIT_FIELDS = ("id", "user_id", "action", "target_type", "target_id", "affected_counts", "policy_version", "created_at")
MEDIA_METADATA_FIELDS = (
    "id", "user_id", "raw_event_id", "source_channel", "message_id", "media_type", "original_name",
    "mime_type", "size_bytes", "sha256", "status", "extractor_name", "extractor_version", "artifact_metadata",
    "created_at", "updated_at",
)
OBSIDIAN_METADATA_FIELDS = ("id", "user_id", "memory_id", "last_exported_at", "last_imported_at", "content_hash", "sync_status")
CONVERSATION_SESSION_FIELDS = (
    "id", "user_id", "agent_role", "channel", "channel_session_key", "status",
    "goal", "context_version", "started_at", "updated_at", "ended_at",
)
CONVERSATION_TURN_FIELDS = (
    "id", "session_id", "user_id", "channel", "channel_message_id", "role",
    "content", "reply_to_turn_id", "sensitivity", "reflection_state",
    "turn_metadata", "created_at",
)
CONVERSATION_EPISODE_FIELDS = (
    "id", "session_id", "user_id", "start_turn_id", "end_turn_id", "summary",
    "topics", "emotional_context", "open_loops", "asked_questions",
    "declined_questions", "memory_signals", "source_turn_ids", "status",
    "reflection_version", "working_state", "handoff_ids", "created_at", "updated_at",
)
CONVERSATION_CURSOR_FIELDS = (
    "id", "session_id", "user_id", "last_reflected_turn_id", "pending_user_turns",
    "next_reflection_at", "last_reflected_at", "attempts", "error", "running", "updated_at",
)
CONVERSATION_ATTENTION_FIELDS = (
    "id", "user_id", "session_id", "episode_id", "kind", "prompt", "value_score",
    "source", "sensitivity", "status", "due_at", "expires_at", "sent_at",
    "responded_at", "cooldown_until", "source_turn_ids", "proactive_allowed",
    "candidate_metadata", "created_at", "updated_at",
)
MEMORY_WORK_CASE_FIELDS = (
    "id", "user_id", "proposition_key", "case_type", "title", "summary", "status",
    "sensitivity", "confidence", "active_memory_id", "version", "case_metadata",
    "created_at", "updated_at", "resolved_at",
)
MEMORY_WORK_EVIDENCE_FIELDS = (
    "id", "case_id", "user_id", "raw_event_id", "source_turn_id", "episode_id",
    "quote", "relationship", "source_type", "trust_class", "occurred_at",
    "evidence_metadata", "created_at",
)
MEMORY_WORK_DECISION_FIELDS = (
    "id", "case_id", "user_id", "source_run_id", "source_event_id", "state",
    "rationale", "rationale_codes", "duplicate_refs", "conflict_refs",
    "memory_ids", "policy_result", "model", "prompt_id", "prompt_version",
    "idempotency_key", "created_at",
)


class DataPortabilityService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def export_for_user(self, user_id: str) -> dict[str, Any]:
        account = await self.db.scalar(select(User).where(User.id == user_id))
        raw_events = list((await self.db.execute(select(RawEvent).where(RawEvent.user_id == user_id))).scalars())
        memories = list((await self.db.execute(select(CommittedMemory).where(CommittedMemory.user_id == user_id))).scalars())
        memory_ids = [memory.id for memory in memories]

        async def scoped_rows(model: Any, fields: Iterable[str], statement) -> list[dict[str, Any]]:
            return [_serialize(row, fields) for row in (await self.db.execute(statement)).scalars().all()]

        sources = await scoped_rows(
            MemorySource,
            SOURCE_FIELDS,
            select(MemorySource).where(MemorySource.memory_id.in_(memory_ids)) if memory_ids else select(MemorySource).where(False),
        )
        relations = await scoped_rows(MemoryRelation, RELATION_FIELDS, select(MemoryRelation).where(MemoryRelation.user_id == user_id))
        wiki_pages = await scoped_rows(KnowledgePage, WIKI_PAGE_FIELDS, select(KnowledgePage).where(KnowledgePage.user_id == user_id))
        wiki_memberships = await scoped_rows(
            KnowledgePageMemory,
            WIKI_MEMBERSHIP_FIELDS,
            select(KnowledgePageMemory).where(KnowledgePageMemory.user_id == user_id),
        )
        wiki_versions = await scoped_rows(
            KnowledgePageVersion,
            WIKI_VERSION_FIELDS,
            select(KnowledgePageVersion).where(KnowledgePageVersion.user_id == user_id),
        )
        lifecycle_audits = await scoped_rows(
            DataLifecycleAudit,
            AUDIT_FIELDS,
            select(DataLifecycleAudit).where(DataLifecycleAudit.user_id == user_id),
        )
        media_artifacts = await scoped_rows(
            MediaArtifact,
            MEDIA_METADATA_FIELDS,
            select(MediaArtifact).where(MediaArtifact.user_id == user_id),
        )
        obsidian_records = await scoped_rows(
            ObsidianSyncRecord,
            OBSIDIAN_METADATA_FIELDS,
            select(ObsidianSyncRecord).where(ObsidianSyncRecord.user_id == user_id),
        )
        conversation_sessions = await scoped_rows(
            AgentSession,
            CONVERSATION_SESSION_FIELDS,
            select(AgentSession).where(
                AgentSession.user_id == user_id,
                AgentSession.agent_role == AgentRole.CONVERSATIONAL,
            ),
        )
        conversation_turns = await scoped_rows(
            ConversationTurn,
            CONVERSATION_TURN_FIELDS,
            select(ConversationTurn).where(ConversationTurn.user_id == user_id),
        )
        conversation_episodes = await scoped_rows(
            ConversationEpisode,
            CONVERSATION_EPISODE_FIELDS,
            select(ConversationEpisode).where(ConversationEpisode.user_id == user_id),
        )
        conversation_cursors = await scoped_rows(
            ConversationReflectionCursor,
            CONVERSATION_CURSOR_FIELDS,
            select(ConversationReflectionCursor).where(
                ConversationReflectionCursor.user_id == user_id
            ),
        )
        conversation_attention = await scoped_rows(
            ConversationAttentionCandidate,
            CONVERSATION_ATTENTION_FIELDS,
            select(ConversationAttentionCandidate).where(
                ConversationAttentionCandidate.user_id == user_id
            ),
        )
        memory_work_cases = await scoped_rows(
            MemoryWorkCase,
            MEMORY_WORK_CASE_FIELDS,
            select(MemoryWorkCase).where(MemoryWorkCase.user_id == user_id),
        )
        memory_work_evidence = await scoped_rows(
            MemoryWorkEvidence,
            MEMORY_WORK_EVIDENCE_FIELDS,
            select(MemoryWorkEvidence).where(MemoryWorkEvidence.user_id == user_id),
        )
        memory_work_decisions = await scoped_rows(
            MemoryWorkDecision,
            MEMORY_WORK_DECISION_FIELDS,
            select(MemoryWorkDecision).where(MemoryWorkDecision.user_id == user_id),
        )
        collections = {
            "raw_events": [_serialize(row, RAW_EVENT_FIELDS) for row in raw_events],
            "committed_memories": [_serialize(row, MEMORY_FIELDS) for row in memories],
            "memory_sources": sources,
            "memory_relations": relations,
            "knowledge_pages": wiki_pages,
            "knowledge_page_memories": wiki_memberships,
            "knowledge_page_versions": wiki_versions,
            "lifecycle_audits": lifecycle_audits,
            "media_artifacts": media_artifacts,
            "obsidian_sync_records": obsidian_records,
            "conversation_sessions": conversation_sessions,
            "conversation_turns": conversation_turns,
            "conversation_episodes": conversation_episodes,
            "conversation_reflection_cursors": conversation_cursors,
            "conversation_attention_candidates": conversation_attention,
            "memory_work_cases": memory_work_cases,
            "memory_work_evidence": memory_work_evidence,
            "memory_work_decisions": memory_work_decisions,
            "agent_workspace_projection": [
                AgentWorkspaceService().export_user_projection(user_id=user_id)
            ],
        }
        return {
            "format": EXPORT_FORMAT,
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "account": {
                "id": user_id,
                "email": account.email if account is not None else None,
                "created_at": _json_safe(account.created_at) if account is not None else None,
            },
            "manifest": {
                "collections": {name: len(rows) for name, rows in collections.items()},
                "excluded": [
                    "password hashes and authentication tokens",
                    "LLM/provider/WeCom configuration and secrets",
                    "memory embedding vectors and embedding content snapshots",
                    "media binaries, local storage paths, remote source URLs, and WeCom media IDs",
                    "Obsidian local vault/file paths",
                ],
                "restore": "No automatic restore endpoint is provided. Validate this export in an isolated database before any manual recovery.",
            },
            "data": collections,
        }
