"""User-scoped, content-free evidence metadata for conversational citations."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.memory.models.committed_memory import CommittedMemory
from src.memory.models.memory_source import MemorySource


@dataclass(frozen=True, slots=True)
class CitationEvidence:
    memory_id: str
    source_event_ids: tuple[str, ...]
    epistemic_status: str
    valid_from: datetime | None
    valid_until: datetime | None


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=timezone.utc)


async def resolve_citation_evidence(
    db: AsyncSession,
    *,
    user_id: str,
    citation_ids: Iterable[str],
) -> tuple[CitationEvidence, ...]:
    """Resolve only current user's cited memory metadata in requested order.

    Citations are already constrained to tool-observed IDs by the runtime.  This
    second guard prevents a caller from turning an arbitrary ID into a source
    lookup, and intentionally excludes memory/source text from user-facing
    traces.
    """
    requested_ids = tuple(dict.fromkeys(
        item for item in citation_ids if isinstance(item, str) and item
    ))
    if not requested_ids:
        return ()
    memories = list((await db.execute(
        select(CommittedMemory).where(
            CommittedMemory.user_id == user_id,
            CommittedMemory.id.in_(requested_ids),
        )
    )).scalars())
    by_id = {memory.id: memory for memory in memories}
    source_rows = list((await db.execute(
        select(MemorySource.memory_id, MemorySource.raw_event_id)
        .where(MemorySource.memory_id.in_(by_id))
        .order_by(MemorySource.created_at.asc())
    )).all()) if by_id else []
    source_ids_by_memory: dict[str, list[str]] = {memory_id: [] for memory_id in by_id}
    for memory_id, raw_event_id in source_rows:
        if raw_event_id not in source_ids_by_memory[memory_id]:
            source_ids_by_memory[memory_id].append(raw_event_id)
    return tuple(
        CitationEvidence(
            memory_id=memory.id,
            source_event_ids=tuple(source_ids_by_memory[memory.id]),
            epistemic_status=memory.epistemic_status,
            valid_from=_as_utc(memory.valid_from),
            valid_until=_as_utc(memory.valid_until),
        )
        for memory_id in requested_ids
        if (memory := by_id.get(memory_id)) is not None
    )
