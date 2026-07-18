"""The one internal constructor for externally supplied RawEvent records.

Routes and channels may preserve compatibility-specific validation, but they do
not get their own memory-write implementation.  This service deliberately has
no API for CommittedMemory.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from src.memory.models.raw_event import (
    ProcessingStatus,
    RawEvent,
    SensitivityLevel,
    SourceType,
    VisibilityScope,
)
from src.shared.ids.id_generator import generate_event_id
from src.shared.utils.hash import compute_content_hash


@dataclass(frozen=True, slots=True)
class IngestedEvent:
    event: RawEvent
    created: bool = True


class EventIngestionService:
    """Append-only event ingress; extraction is the only downstream action."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def append(
        self,
        *,
        user_id: str,
        content: str,
        source_type: SourceType,
        source_id: str | None = None,
        agent_id: str | None = None,
        project_id: str | None = None,
        repo_id: str | None = None,
        workspace_id: str | None = None,
        occurred_at: datetime | None = None,
        content_hash: str | None = None,
        event_metadata: dict[str, Any] | None = None,
        sensitivity: SensitivityLevel = SensitivityLevel.NORMAL,
        visibility_scope: VisibilityScope = VisibilityScope.PROJECT,
        processing_status: ProcessingStatus = ProcessingStatus.QUEUED,
    ) -> IngestedEvent:
        text = str(content or "").strip()
        if not text:
            raise ValueError("event_content_required")
        event = RawEvent(
            id=generate_event_id(),
            source_type=source_type,
            source_id=source_id,
            agent_id=agent_id,
            user_id=user_id,
            project_id=project_id,
            repo_id=repo_id,
            workspace_id=workspace_id,
            occurred_at=occurred_at or datetime.now(timezone.utc),
            content=text,
            # Some file/link channels need their immutable upstream digest in
            # the event identity.  They still use this single append path.
            content_hash=content_hash or compute_content_hash(text),
            event_metadata=dict(event_metadata or {}),
            sensitivity=sensitivity,
            visibility_scope=visibility_scope,
            processing_status=processing_status,
        )
        self.db.add(event)
        await self.db.flush()
        return IngestedEvent(event)


def trigger_ingested_event(event_id: str) -> None:
    """Dispatch only after the caller has committed the RawEvent transaction."""
    from src.memory.tasks.memory_extraction import trigger_extraction

    trigger_extraction(event_id)
