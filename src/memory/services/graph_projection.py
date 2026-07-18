"""Internal-only Graphiti projection pipeline.

The module never makes Graphiti a write authority.  It consumes a transactional
outbox and re-reads RawEvent/CommittedMemory rows under the owner scope before
handing a compact, provenance-tagged episode to the graph adapter.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.memory.models.committed_memory import CommittedMemory, CommittedStatus
from src.memory.models.graph_projection import (
    GraphProjection,
    GraphProjectionOperation,
    GraphProjectionStatus,
    GraphReplayCheckpoint,
    projection_key,
)
from src.memory.models.raw_event import RawEvent
from src.memory.services.governance_policy import SENSITIVITY_BY_RECALL, VISIBILITY_BY_RECALL, normalize_recall_level
from src.shared.config import settings
from src.shared.db.database import async_session
from src.shared.db.worker import celery_app
from src.shared.ids.id_generator import generate_id

logger = logging.getLogger(__name__)

# The initial ontology is intentionally closed.  Expansions require a migration
# and an explicit governance review rather than prompting arbitrary graph labels.
GRAPH_NODE_TYPES = {"person", "project", "repository", "task", "decision", "preference", "event"}
GRAPH_RELATION_TYPES = {
    "related_to", "occurred_in", "responsible_for", "depends_on",
    "supports", "contradicts", "corrects", "supersedes",
}
PROJECTION_LEASE_SECONDS = 15 * 60
MAX_PROJECTION_ATTEMPTS = 5
EXTERNAL_AGENT_SOURCE_VALUES = {"agent_api", "codex", "openclaw", "chatgpt"}
_worker_local = threading.local()


class GraphProjectionClient(Protocol):
    async def upsert_episode(self, payload: dict[str, Any]) -> None: ...

    async def delete_episode(self, payload: dict[str, Any]) -> None: ...

    async def search(self, question: str, *, group_id: str) -> list[Any]: ...


@dataclass(frozen=True, slots=True)
class ProjectionEnqueueResult:
    projection_id: str
    created: bool


def _enum_value(value: object) -> str:
    return str(getattr(value, "value", value) or "")


def _source_revision(source: object, fallback: str = "1") -> str:
    return str(getattr(source, "content_hash", None) or getattr(source, "revision", None) or fallback)


async def enqueue_projection(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str | None,
    source_kind: str,
    source_id: str,
    source_revision: str,
    operation: GraphProjectionOperation,
    metadata: dict[str, Any] | None = None,
) -> ProjectionEnqueueResult:
    """Add at most one projection operation per authoritative source revision."""
    key = projection_key(source_kind, source_id, source_revision, operation)
    existing = await db.scalar(select(GraphProjection).where(GraphProjection.projection_key == key))
    if existing is not None:
        return ProjectionEnqueueResult(existing.id, False)
    row = GraphProjection(
        id=generate_id("gpr"),
        projection_key=key,
        user_id=user_id,
        project_id=project_id,
        source_kind=source_kind,
        source_id=source_id,
        source_revision=source_revision,
        operation=operation,
        status=GraphProjectionStatus.QUEUED,
        projection_metadata=metadata or {},
    )
    db.add(row)
    await db.flush()
    return ProjectionEnqueueResult(row.id, True)


async def queue_raw_event_projection(db: AsyncSession, event: RawEvent) -> ProjectionEnqueueResult:
    return await enqueue_projection(
        db,
        user_id=event.user_id,
        project_id=event.project_id,
        source_kind="raw_event",
        source_id=event.id,
        source_revision=_source_revision(event, event.id),
        operation=GraphProjectionOperation.UPSERT,
        metadata={
            "visibility_scope": _enum_value(event.visibility_scope),
            "sensitivity": _enum_value(event.sensitivity),
            "occurred_at": event.occurred_at.isoformat() if event.occurred_at else None,
        },
    )


async def queue_memory_projection(db: AsyncSession, memory: CommittedMemory) -> ProjectionEnqueueResult:
    return await enqueue_projection(
        db,
        user_id=memory.user_id,
        project_id=memory.project_id,
        source_kind="committed_memory",
        source_id=memory.id,
        source_revision=_source_revision(memory, str(memory.revision or 1)),
        operation=GraphProjectionOperation.UPSERT,
        metadata={
            "visibility_scope": _enum_value(memory.visibility_scope),
            "sensitivity": _enum_value(memory.sensitivity),
            "lifecycle": _enum_value(memory.status),
        },
    )


async def queue_source_deletion(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str | None,
    source_kind: str,
    source_id: str,
    source_revision: str = "deleted",
) -> ProjectionEnqueueResult:
    return await enqueue_projection(
        db,
        user_id=user_id,
        project_id=project_id,
        source_kind=source_kind,
        source_id=source_id,
        source_revision=source_revision,
        operation=GraphProjectionOperation.DELETE,
        metadata={"lifecycle": "deleted"},
    )


def _episode_payload(row: GraphProjection, source: RawEvent | CommittedMemory | None) -> dict[str, Any]:
    metadata = dict(row.projection_metadata or {})
    payload: dict[str, Any] = {
        "episode_id": f"{row.source_kind}:{row.source_id}:{row.source_revision}",
        # In graphiti-core 0.29 a Neo4j group_id maps to its database name;
        # user isolation is therefore enforced by the single-user deployment
        # guard plus source verification, rather than a fake group namespace.
        "group_id": settings.GRAPHITI_NEO4J_DATABASE,
        "source_kind": row.source_kind,
        "source_id": row.source_id,
        "source_revision": row.source_revision,
        "user_id": row.user_id,
        "project_id": row.project_id,
        "visibility_scope": metadata.get("visibility_scope", "private"),
        "sensitivity": metadata.get("sensitivity", "normal"),
        "lifecycle": metadata.get("lifecycle", "active"),
        "ontology": {"node_types": sorted(GRAPH_NODE_TYPES), "relation_types": sorted(GRAPH_RELATION_TYPES)},
    }
    if isinstance(source, RawEvent):
        payload.update(
            {
                "occurred_at": source.occurred_at,
                "content": source.content,
                "source_type": _enum_value(source.source_type),
            }
        )
    elif isinstance(source, CommittedMemory):
        payload.update(
            {
                "occurred_at": source.valid_from,
                "valid_until": source.valid_until,
                "content": f"{source.title}\n{source.body}",
                "memory_type": _enum_value(source.memory_type),
                "lifecycle": _enum_value(source.status),
            }
        )
    return payload


async def _load_authoritative_source(db: AsyncSession, row: GraphProjection) -> RawEvent | CommittedMemory | None:
    if row.source_kind == "raw_event":
        return await db.scalar(select(RawEvent).where(RawEvent.id == row.source_id, RawEvent.user_id == row.user_id))
    if row.source_kind == "committed_memory":
        memory = await db.scalar(
            select(CommittedMemory).where(CommittedMemory.id == row.source_id, CommittedMemory.user_id == row.user_id)
        )
        if memory is not None and memory.status != CommittedStatus.ACTIVE:
            return None
        return memory
    return None


async def claim_projection(db: AsyncSession, projection_id: str) -> GraphProjection | None:
    now = datetime.now(timezone.utc)
    stale_before = now - timedelta(seconds=PROJECTION_LEASE_SECONDS)
    claimable = or_(
        GraphProjection.status == GraphProjectionStatus.QUEUED,
        and_(
            GraphProjection.status == GraphProjectionStatus.FAILED,
            GraphProjection.attempts < MAX_PROJECTION_ATTEMPTS,
            GraphProjection.next_retry_at.is_not(None),
            GraphProjection.next_retry_at <= now,
        ),
        and_(GraphProjection.status == GraphProjectionStatus.PROCESSING, GraphProjection.lease_started_at < stale_before),
    )
    result = await db.execute(
        update(GraphProjection)
        .where(GraphProjection.id == projection_id, claimable)
        .values(
            status=GraphProjectionStatus.PROCESSING,
            lease_started_at=now,
            next_retry_at=None,
            attempts=func.coalesce(GraphProjection.attempts, 0) + 1,
            error_code=None,
        )
    )
    if result.rowcount != 1:
        return None
    await db.commit()
    return await db.scalar(select(GraphProjection).where(GraphProjection.id == projection_id))


class LazyGraphitiClient:
    """Production adapter loaded only inside the worker after GRAPHITI_ENABLED.

    Importing the web application therefore never requires Neo4j or graphiti-core.
    Graphiti releases differ on deletion APIs; unsupported deletion is surfaced as
    a failed outbox item rather than being incorrectly recorded as projected.
    """

    def __init__(self) -> None:
        self._graph: Any | None = None

    async def _get_graph(self) -> Any:
        if self._graph is not None:
            return self._graph
        if not settings.GRAPHITI_ENABLED:
            raise RuntimeError("graphiti_disabled")
        if settings.GRAPHITI_REQUIRE_SOLO_MODE and not settings.SOLO_MODE:
            raise RuntimeError("graphiti_multitenant_isolation_unavailable")
        llm_api_key = settings.GRAPHITI_LLM_API_KEY or settings.DEEPSEEK_API_KEY
        llm_base_url = settings.GRAPHITI_LLM_BASE_URL or settings.DEEPSEEK_API_URL
        if not settings.GRAPHITI_NEO4J_PASSWORD or not settings.GRAPHITI_EMBEDDING_MODEL or not llm_api_key:
            raise RuntimeError("graphiti_configuration_incomplete")
        try:
            from graphiti_core import Graphiti
            from graphiti_core.cross_encoder.openai_reranker_client import OpenAIRerankerClient
            from graphiti_core.driver.neo4j_driver import Neo4jDriver
            from graphiti_core.embedder.openai import OpenAIEmbedder, OpenAIEmbedderConfig
            from graphiti_core.llm_client.config import LLMConfig
            from graphiti_core.llm_client.openai_generic_client import OpenAIGenericClient
        except ImportError as exc:
            raise RuntimeError("graphiti_dependency_missing") from exc
        llm_config = LLMConfig(
            api_key=llm_api_key,
            model=settings.GRAPHITI_LLM_MODEL,
            small_model=settings.GRAPHITI_LLM_MODEL,
            base_url=llm_base_url,
        )
        llm_client = OpenAIGenericClient(config=llm_config)
        driver = Neo4jDriver(
            uri=settings.GRAPHITI_NEO4J_URI,
            user=settings.GRAPHITI_NEO4J_USER,
            password=settings.GRAPHITI_NEO4J_PASSWORD,
            database=settings.GRAPHITI_NEO4J_DATABASE,
        )
        self._graph = Graphiti(
            graph_driver=driver,
            llm_client=llm_client,
            embedder=OpenAIEmbedder(
                config=OpenAIEmbedderConfig(
                    api_key=llm_api_key,
                    embedding_model=settings.GRAPHITI_EMBEDDING_MODEL,
                    base_url=llm_base_url,
                )
            ),
            cross_encoder=OpenAIRerankerClient(client=llm_client, config=llm_config),
            max_coroutines=max(1, settings.GRAPHITI_PROJECTION_CONCURRENCY),
        )
        await self._graph.build_indices_and_constraints()
        return self._graph

    async def upsert_episode(self, payload: dict[str, Any]) -> None:
        graph = await self._get_graph()
        from graphiti_core.nodes import EpisodeType

        # Structured episodes carry scope and authoritative-source coordinates
        # alongside content, allowing a later provenance verifier to reject any
        # graph edge not tied back to a permitted source.
        episode_body = dict(payload)
        episode_body["occurred_at"] = (
            payload["occurred_at"].isoformat() if payload.get("occurred_at") else None
        )
        await graph.add_episode(
            name=payload["episode_id"],
            episode_body=json.dumps(episode_body, ensure_ascii=False, default=str),
            source=EpisodeType.json,
            source_description=f"authoritative:{payload['source_kind']}:{payload['source_id']}",
            reference_time=payload.get("occurred_at"),
            group_id=payload["group_id"],
            uuid=payload["episode_id"],
            custom_extraction_instructions=(
                "Use only these entity types: Person, Project, Repository, Task, Decision, Preference, Event. "
                "Use only these relationship types: RELATED_TO, OCCURRED_IN, RESPONSIBLE_FOR, DEPENDS_ON, "
                "SUPPORTS, CONTRADICTS, CORRECTS, SUPERSEDES. Do not infer identities or facts not grounded "
                "in this episode."
            ),
        )

    async def delete_episode(self, payload: dict[str, Any]) -> None:
        graph = await self._get_graph()
        delete_method = getattr(graph, "remove_episode", None)
        if delete_method is None:
            raise RuntimeError("graphiti_remove_episode_unsupported")
        result = delete_method(payload["episode_id"])
        if asyncio.iscoroutine(result):
            await result

    async def search(self, question: str, *, group_id: str) -> list[Any]:
        graph = await self._get_graph()
        return list(await graph.search(question, group_ids=[group_id]))

    async def close(self) -> None:
        if self._graph is None:
            return
        close = getattr(self._graph, "close", None)
        if close is not None:
            result = close()
            if asyncio.iscoroutine(result):
                await result
        self._graph = None


async def process_projection(
    projection_id: str,
    *,
    client: GraphProjectionClient | None = None,
) -> bool:
    """Process one projection without affecting its authoritative transaction."""
    async with async_session() as db:
        row = await claim_projection(db, projection_id)
        if row is None:
            return False
        try:
            payload = _episode_payload(row, await _load_authoritative_source(db, row))
            if row.operation == GraphProjectionOperation.UPSERT and "content" not in payload:
                # A deleted or no-longer-authorized source must never be resurrected.
                raise RuntimeError("authoritative_source_unavailable")
            graph_client = client or LazyGraphitiClient()
            if row.operation == GraphProjectionOperation.DELETE:
                await graph_client.delete_episode(payload)
            else:
                await graph_client.upsert_episode(payload)
            row.status = GraphProjectionStatus.PROJECTED
            row.projected_at = datetime.now(timezone.utc)
            row.lease_started_at = None
            row.error_code = None
            await db.commit()
            return True
        except Exception as exc:
            await db.rollback()
            failed = await db.scalar(select(GraphProjection).where(GraphProjection.id == projection_id))
            if failed is None:
                return False
            failed.status = GraphProjectionStatus.FAILED
            failed.lease_started_at = None
            failed.error_code = type(exc).__name__ if str(exc) != "graphiti_disabled" else "graphiti_disabled"
            if int(failed.attempts or 0) < MAX_PROJECTION_ATTEMPTS:
                failed.next_retry_at = datetime.now(timezone.utc) + timedelta(
                    seconds=min(600, 30 * (2 ** max(0, int(failed.attempts or 1) - 1)))
                )
            await db.commit()
            logger.warning("Graph projection failed projection_id=%s error_type=%s", projection_id, type(exc).__name__)
            return False


@celery_app.task(name="memory.process_graph_projection")
def process_graph_projection_task(projection_id: str) -> None:
    """Reuse one Graphiti client/event loop per Celery worker thread."""
    runner = getattr(_worker_local, "runner", None)
    if runner is None:
        runner = asyncio.Runner()
        _worker_local.runner = runner
    client = getattr(_worker_local, "graphiti_client", None)
    if client is None:
        client = LazyGraphitiClient()
        _worker_local.graphiti_client = client
    runner.run(process_projection(projection_id, client=client))


def trigger_graph_projection(projection_id: str) -> None:
    """Best-effort delivery; outbox recovery keeps failures visible and retryable."""
    if not settings.GRAPHITI_ENABLED:
        return
    try:
        process_graph_projection_task.delay(projection_id)
    except Exception:
        logger.warning("Graph projection enqueue failed projection_id=%s", projection_id, exc_info=True)


async def enqueue_replay_batch(
    db: AsyncSession,
    *,
    user_id: str,
    batch_size: int = 50,
    dry_run: bool = False,
    reset: bool = False,
) -> dict[str, int | bool]:
    """Queue the next deterministic replay page using durable cursors."""
    limit = max(1, min(batch_size, settings.GRAPHITI_BACKFILL_BATCH_SIZE_MAX))
    event_checkpoint = await _replay_checkpoint(db, user_id=user_id, source_kind="raw_event", reset=reset, persist=not dry_run)
    memory_checkpoint = await _replay_checkpoint(db, user_id=user_id, source_kind="committed_memory", reset=reset, persist=not dry_run)
    events = await _replay_rows(
        db, RawEvent, RawEvent.occurred_at, event_checkpoint, user_id=user_id, limit=limit,
        extra_filters=(RawEvent.source_type.notin_(EXTERNAL_AGENT_SOURCE_VALUES),),
    )
    memories = await _replay_rows(
        db, CommittedMemory, CommittedMemory.valid_from, memory_checkpoint, user_id=user_id, limit=limit,
        extra_filters=(CommittedMemory.status == CommittedStatus.ACTIVE,),
    )
    if dry_run:
        return {
            "dry_run": True, "raw_events": len(events), "committed_memories": len(memories),
            "queued": 0, "has_more": len(events) == limit or len(memories) == limit,
        }
    event_queued = 0
    for event in events:
        event_queued += int((await queue_raw_event_projection(db, event)).created)
    memory_queued = 0
    for memory in memories:
        if memory.status == CommittedStatus.ACTIVE:
            memory_queued += int((await queue_memory_projection(db, memory)).created)
    queued = event_queued + memory_queued
    _advance_checkpoint(event_checkpoint, events, occurred_attr="occurred_at", source_id_attr="id", queued=event_queued)
    _advance_checkpoint(memory_checkpoint, memories, occurred_attr="valid_from", source_id_attr="id", queued=memory_queued)
    await db.flush()
    return {
        "dry_run": False, "raw_events": len(events), "committed_memories": len(memories),
        "queued": queued, "has_more": len(events) == limit or len(memories) == limit,
    }


async def _replay_checkpoint(
    db: AsyncSession, *, user_id: str, source_kind: str, reset: bool, persist: bool
) -> GraphReplayCheckpoint:
    row = await db.scalar(
        select(GraphReplayCheckpoint).where(
            GraphReplayCheckpoint.user_id == user_id,
            GraphReplayCheckpoint.source_kind == source_kind,
        )
    )
    if row is None:
        row = GraphReplayCheckpoint(id=generate_id("grc"), user_id=user_id, source_kind=source_kind)
        if persist:
            db.add(row)
            await db.flush()
    if reset:
        # Dry-run must not dirty an existing SQLAlchemy instance.  Return a
        # virtual blank cursor so it previews a fresh replay faithfully.
        if not persist:
            return GraphReplayCheckpoint(id=row.id, user_id=user_id, source_kind=source_kind)
        row.cursor_occurred_at = None
        row.cursor_source_id = None
        row.completed_at = None
        row.scanned_count = 0
        row.queued_count = 0
    return row


async def _replay_rows(
    db: AsyncSession,
    model: Any,
    occurred_column: Any,
    checkpoint: GraphReplayCheckpoint,
    *,
    user_id: str,
    limit: int,
    extra_filters: tuple[Any, ...],
) -> list[Any]:
    filters = [model.user_id == user_id, *extra_filters]
    if checkpoint.cursor_occurred_at is not None and checkpoint.cursor_source_id is not None:
        filters.append(
            or_(
                occurred_column > checkpoint.cursor_occurred_at,
                and_(occurred_column == checkpoint.cursor_occurred_at, model.id > checkpoint.cursor_source_id),
            )
        )
    return list((await db.execute(select(model).where(*filters).order_by(occurred_column, model.id).limit(limit))).scalars())


def _advance_checkpoint(
    checkpoint: GraphReplayCheckpoint,
    rows: list[Any],
    *,
    occurred_attr: str,
    source_id_attr: str,
    queued: int,
) -> None:
    if not rows:
        checkpoint.completed_at = datetime.now(timezone.utc)
        return
    last = rows[-1]
    checkpoint.cursor_occurred_at = getattr(last, occurred_attr)
    checkpoint.cursor_source_id = str(getattr(last, source_id_attr))
    checkpoint.completed_at = None
    checkpoint.scanned_count = int(checkpoint.scanned_count or 0) + len(rows)
    checkpoint.queued_count = int(checkpoint.queued_count or 0) + queued


async def graph_projection_status(db: AsyncSession, *, user_id: str) -> dict[str, Any]:
    rows = list((await db.execute(select(GraphProjection.status, func.count(GraphProjection.id)).where(GraphProjection.user_id == user_id).group_by(GraphProjection.status))).all())
    counts = {_enum_value(status): int(count) for status, count in rows}
    checkpoints = list(
        (await db.execute(
            select(GraphReplayCheckpoint).where(GraphReplayCheckpoint.user_id == user_id)
        )).scalars()
    )
    return {
        "enabled": settings.GRAPHITI_ENABLED,
        "shadow_mode": settings.GRAPHITI_SHADOW_MODE,
        "counts": {status.value: counts.get(status.value, 0) for status in GraphProjectionStatus},
        "replay": {
            row.source_kind: {
                "completed_at": row.completed_at,
                "scanned_count": int(row.scanned_count or 0),
                "queued_count": int(row.queued_count or 0),
                "has_cursor": row.cursor_occurred_at is not None,
            }
            for row in checkpoints
        },
    }


async def retrieve_verified_graph_context(
    db: AsyncSession,
    *,
    user_id: str,
    question: str,
    project_id: str | None,
    recall_level: str,
    limit: int = 8,
) -> dict[str, Any]:
    """Return graph facts only when their formal-memory provenance still passes policy.

    Raw events may be projected for temporal traceability, but an external event
    alone is never admitted into Agent recall.  A graph fact needs at least one
    active, in-scope CommittedMemory episode before it is returned.
    """
    base = {
        "mode": "disabled",
        "available": False,
        "fallback": True,
        "relations": [],
        "source_memory_ids": [],
    }
    if not settings.GRAPHITI_ENABLED:
        return base
    if settings.GRAPHITI_REQUIRE_SOLO_MODE and not settings.SOLO_MODE:
        return {**base, "mode": "blocked", "reason": "multitenant_isolation_unavailable"}
    level = normalize_recall_level(recall_level)
    try:
        results = await LazyGraphitiClient().search(
            question, group_id=settings.GRAPHITI_NEO4J_DATABASE
        )
    except Exception as exc:
        logger.warning("Graph recall fallback error_type=%s", type(exc).__name__)
        return {**base, "mode": "unavailable", "reason": type(exc).__name__}

    projected = list(
        (
            await db.execute(
                select(GraphProjection).where(
                    GraphProjection.user_id == user_id,
                    GraphProjection.operation == GraphProjectionOperation.UPSERT,
                    GraphProjection.status == GraphProjectionStatus.PROJECTED,
                    GraphProjection.source_kind == "committed_memory",
                )
            )
        ).scalars()
    )
    by_episode = {
        f"{row.source_kind}:{row.source_id}:{row.source_revision}": row for row in projected
    }
    allowed_sensitivity = SENSITIVITY_BY_RECALL[level]
    allowed_visibility = VISIBILITY_BY_RECALL[level]
    verified: list[dict[str, Any]] = []
    seen_memory_ids: set[str] = set()
    for result in results:
        episode_ids = [str(item) for item in (getattr(result, "episodes", None) or [])]
        source_rows = [by_episode[item] for item in episode_ids if item in by_episode]
        source_refs: list[dict[str, Any]] = []
        for row in source_rows:
            memory = await db.scalar(
                select(CommittedMemory).where(
                    CommittedMemory.id == row.source_id,
                    CommittedMemory.user_id == user_id,
                    CommittedMemory.status == CommittedStatus.ACTIVE,
                    CommittedMemory.sensitivity.in_(allowed_sensitivity),
                    CommittedMemory.visibility_scope.in_(allowed_visibility),
                )
            )
            if memory is None or (project_id and memory.project_id not in {None, project_id}):
                continue
            source_refs.append(
                {
                    "memory_id": memory.id,
                    "valid_from": memory.valid_from.isoformat() if memory.valid_from else None,
                    "valid_until": memory.valid_until.isoformat() if memory.valid_until else None,
                }
            )
            seen_memory_ids.add(memory.id)
        if not source_refs:
            continue
        verified.append(
            {
                "fact": str(getattr(result, "fact", ""))[:1000],
                "valid_at": _iso_value(getattr(result, "valid_at", None)),
                "invalid_at": _iso_value(getattr(result, "invalid_at", None)),
                "graph_path": {
                    "source_node_uuid": str(getattr(result, "source_node_uuid", "")),
                    "target_node_uuid": str(getattr(result, "target_node_uuid", "")),
                },
                "sources": source_refs,
            }
        )
        if len(verified) >= max(1, min(limit, 20)):
            break
    return {
        "mode": "shadow" if settings.GRAPHITI_SHADOW_MODE else "active",
        "available": True,
        "fallback": False,
        "relations": verified,
        "source_memory_ids": sorted(seen_memory_ids),
    }


def _iso_value(value: Any) -> str | None:
    return value.isoformat() if hasattr(value, "isoformat") else None
