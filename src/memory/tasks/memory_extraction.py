"""Event-driven dispatch into the V2.4 autonomous Working-Agent ledger."""

import asyncio
import hashlib
import logging
from datetime import datetime, timedelta, timezone
from typing import List
from src.shared.db.worker import celery_app
from src.shared.db.database import async_session
from src.shared.config import settings
from src.memory.models.raw_event import RawEvent, ProcessingStatus, SensitivityLevel, SourceType
from src.memory.models.committed_memory import CommittedMemory
from src.memory.models.memory_embedding import MemoryEmbedding
from src.shared.ids.id_generator import generate_embedding_id
from src.shared.llm.providers import get_llm_provider
from src.shared.async_runner import persistent_async_runner, schedule_coroutine
from sqlalchemy import and_, case, func, or_, select, update

from src.memory.services.retrieval_engine import deterministic_fallback_embedding, DEFAULT_EMBEDDING_DIM
from src.shared.utils.runtime_metrics import runtime_metrics


logger = logging.getLogger(__name__)
PROCESSING_LEASE_SECONDS = 15 * 60
MAX_PROCESSING_ATTEMPTS = 3
FAST_DRAIN_LEASE_SECONDS = 90
FAST_DRAIN_ACTIVE_STATES = ("queued", "running")

@celery_app.task
def process_memory_event(event_id: str):
    persistent_async_runner.run(_process_memory_event(event_id))


@celery_app.task
def fast_drain_queued_events(run_id: str):
    persistent_async_runner.run(_run_fast_drain(run_id))


def trigger_extraction(event_id: str):
    """Queue extraction for durable workers, with an in-process delivery fallback.

    The fallback preserves single-process/development behavior when Redis or a
    worker is unavailable.  In normal deployments the task is handed to Celery
    instead of tying the work to the API process lifetime.
    """
    try:
        process_memory_event.delay(event_id)
        return
    except Exception:
        logger.warning("Celery enqueue failed; using local extraction fallback", exc_info=True)

    if settings.TESTING:
        # Integration tests use one shared SQLite file; an unsupervised
        # background writer would create lock races unrelated to production.
        return
    schedule_coroutine(_process_memory_event(event_id))


def trigger_fast_drain(run_id: str) -> None:
    """Dispatch an operator-requested drain without blocking the API request."""
    try:
        fast_drain_queued_events.delay(run_id)
        return
    except Exception:
        logger.warning("Celery fast-drain enqueue failed; using local fallback", exc_info=True)

    if settings.TESTING:
        return
    schedule_coroutine(_run_fast_drain(run_id))


def _threaded_extraction(event_id: str):
    persistent_async_runner.run(_process_memory_event(event_id))


async def claim_event_for_extraction(
    session,
    event_id: str,
    *,
    now: datetime | None = None,
    ignore_retry_at: bool = False,
) -> RawEvent | None:
    """Atomically lease a queued or stale-processing event for one extractor.

    A completed governance decision and its status transition are committed together,
    so reclaiming an expired lease can safely retry work interrupted before that
    transaction completed.  The error field contains only an error type.
    """
    now = now or datetime.now(timezone.utc)
    stale_before = now - timedelta(seconds=PROCESSING_LEASE_SECONDS)
    queued_retry_window = (
        RawEvent.processing_status == ProcessingStatus.QUEUED
        if ignore_retry_at
        else and_(
            RawEvent.processing_status == ProcessingStatus.QUEUED,
            or_(
                RawEvent.processing_next_retry_at.is_(None),
                RawEvent.processing_next_retry_at <= now,
            ),
        )
    )
    claimable = or_(
        queued_retry_window,
        and_(
            RawEvent.processing_status == ProcessingStatus.FAILED,
            RawEvent.processing_attempts < MAX_PROCESSING_ATTEMPTS,
            RawEvent.processing_next_retry_at.is_not(None),
            RawEvent.processing_next_retry_at <= now,
        ),
        and_(
            RawEvent.processing_status == ProcessingStatus.PROCESSING,
            or_(
                RawEvent.processing_started_at.is_(None),
                RawEvent.processing_started_at < stale_before,
            ),
        ),
    )
    result = await session.execute(
        update(RawEvent)
        .where(RawEvent.id == event_id, claimable)
        .values(
            processing_status=ProcessingStatus.PROCESSING,
            processing_started_at=now,
            processing_heartbeat_at=now,
            processing_next_retry_at=None,
            processing_attempts=func.coalesce(RawEvent.processing_attempts, 0) + 1,
            processing_error=None,
            processing_result=None,
        )
    )
    if result.rowcount != 1:
        return None
    await session.commit()
    return await session.scalar(select(RawEvent).where(RawEvent.id == event_id))


async def recover_stale_extraction_events(session, *, now: datetime | None = None) -> int:
    """Return expired processing leases to the existing queued backlog."""
    now = now or datetime.now(timezone.utc)
    stale_before = now - timedelta(seconds=PROCESSING_LEASE_SECONDS)
    result = await session.execute(
        update(RawEvent)
        .where(
            RawEvent.processing_status == ProcessingStatus.PROCESSING,
            or_(
                RawEvent.processing_started_at.is_(None),
                RawEvent.processing_started_at < stale_before,
            ),
        )
        .values(
            processing_status=ProcessingStatus.QUEUED,
            processing_started_at=None,
            processing_heartbeat_at=None,
            processing_next_retry_at=now,
            processing_error="lease_expired",
            processing_result="lease_expired",
        )
    )
    await session.commit()
    return int(result.rowcount or 0)


async def recover_known_loop_failures(session, *, now: datetime | None = None) -> int:
    """Retry each pre-2.5.2 event-loop failure exactly once after the fix."""
    now = now or datetime.now(timezone.utc)
    rows = list(
        (
            await session.execute(
                select(RawEvent)
                .where(
                    RawEvent.processing_status == ProcessingStatus.FAILED,
                    RawEvent.processing_error == "RuntimeError",
                    or_(
                        RawEvent.processing_attempts >= MAX_PROCESSING_ATTEMPTS,
                        RawEvent.processing_next_retry_at.is_(None),
                    ),
                )
                .order_by(RawEvent.occurred_at.asc())
                .limit(100)
            )
        ).scalars()
    )
    recovered = 0
    for event in rows:
        metadata = dict(event.event_metadata or {})
        if metadata.get("runtime_recovery_version") == "2.5.2":
            continue
        metadata["runtime_recovery_version"] = "2.5.2"
        event.event_metadata = metadata
        event.processing_status = ProcessingStatus.QUEUED
        event.processing_attempts = 0
        event.processing_started_at = None
        event.processing_heartbeat_at = now
        event.processing_next_retry_at = now
        event.processing_error = None
        event.processing_result = "runtime_loop_recovered"
        recovered += 1
    if recovered:
        await session.commit()
    return recovered


async def _process_memory_event(
    event_id: str,
    *,
    operator_drain: bool = False,
    operator_cutoff: datetime | None = None,
):
    """Process one RawEvent through the V2 Working Agent only.

    The Working Agent is the sole autonomous formal-memory writer. It may
    request evidence or discard noise, and never falls back to a removed writer.
    """
    async with async_session() as session:
        result = await session.execute(
            select(RawEvent).where(RawEvent.id == event_id)
        )
        event = result.scalar_one_or_none()

        if not event:
            return
        claimed_event = await claim_event_for_extraction(
            session,
            event_id,
            ignore_retry_at=operator_drain,
        )
        if not claimed_event:
            if event.processing_status == ProcessingStatus.PROCESSING:
                await _wait_for_existing_extraction(event_id)
            return
        event = claimed_event

        try:
            raw_event = {
                "id": event.id,
                "content": event.content,
                "user_id": event.user_id,
                "visibility_scope": event.visibility_scope,
                "project_id": event.project_id,
                "repo_id": event.repo_id,
                "workspace_id": event.workspace_id,
                "source_type": event.source_type,
                "sensitivity": event.sensitivity,
                "occurred_at": event.occurred_at,
                "metadata": event.event_metadata or {},
                "event_metadata": event.event_metadata or {},
            }

            from src.execution.services.memory_operations import MemoryOperationsCoordinator

            active_result = await MemoryOperationsCoordinator(session).process_event(
                event,
                operator_drain=operator_drain,
                operator_cutoff=operator_cutoff,
            )

            if active_result.state == "DEFERRED":
                event.processing_status = ProcessingStatus.QUEUED
                event.processing_started_at = None
                event.processing_heartbeat_at = datetime.now(timezone.utc)
                event.processing_next_retry_at = active_result.deferred_until
                event.processing_error = None
                event.processing_result = "waiting_microbatch"
                event.processing_attempts = max(0, int(event.processing_attempts or 1) - 1)
                await session.commit()
                return

            episode_id = (event.event_metadata or {}).get("episode_id")
            if isinstance(episode_id, str) and episode_id:
                from src.execution.models.conversation import ConversationEpisode

                episode = await session.get(ConversationEpisode, episode_id)
                if episode is not None and episode.user_id == event.user_id:
                    episode.working_state = active_result.state.lower()
                    if active_result.handoff_id:
                        episode.handoff_ids = list(
                            dict.fromkeys(
                                [*(episode.handoff_ids or []), active_result.handoff_id]
                            )
                        )
                    updated_signals = []
                    for signal in list(episode.memory_signals or []):
                        if not isinstance(signal, dict):
                            updated_signals.append(signal)
                            continue
                        item = dict(signal)
                        if item.get("raw_event_id") == event.id:
                            item["working_state"] = active_result.state.lower()
                            item["memory_ids"] = list(active_result.memory_ids)
                            item["handoff_id"] = active_result.handoff_id
                        updated_signals.append(item)
                    episode.memory_signals = updated_signals

            event.processing_status = ProcessingStatus.COMPLETED
            event.processing_started_at = None
            event.processing_heartbeat_at = datetime.now(timezone.utc)
            event.processing_error = None
            event.processing_result = active_result.state.lower()
            batch_event_ids = (event.event_metadata or {}).get("batch_source_event_ids")
            if isinstance(batch_event_ids, list):
                secondary_ids = [str(item) for item in batch_event_ids if isinstance(item, str) and item != event.id]
                if secondary_ids:
                    await session.execute(
                        RawEvent.__table__.update()
                        .where(
                            RawEvent.id.in_(secondary_ids),
                            RawEvent.user_id == event.user_id,
                            RawEvent.processing_status.in_((ProcessingStatus.QUEUED, ProcessingStatus.PROCESSING)),
                        )
                        .values(
                            processing_status=ProcessingStatus.COMPLETED,
                            processing_started_at=None,
                            processing_heartbeat_at=datetime.now(timezone.utc),
                            processing_error=None,
                            processing_result=f"batched_{active_result.state.lower()}",
                        )
                    )
            # External-agent events remain traceable in RawEvent/WorkCase but
            # must not become Graphiti extraction input.  Only a later governed
            # CommittedMemory may enter the derived graph.
            graph_projection = None
            if event.source_type.value not in {"agent_api", "codex", "openclaw", "chatgpt"}:
                from src.memory.services.graph_projection import queue_raw_event_projection

                graph_projection = await queue_raw_event_projection(session, event)
            await session.commit()
            from src.memory.models.graph_projection import GraphProjection, GraphProjectionStatus
            from src.memory.services.graph_projection import trigger_graph_projection

            projection_ids = (
                [graph_projection.projection_id]
                if graph_projection is not None and graph_projection.created
                else []
            )
            if active_result.memory_ids:
                projection_ids.extend(
                    list(
                        (
                            await session.execute(
                                select(GraphProjection.id).where(
                                    GraphProjection.source_kind == "committed_memory",
                                    GraphProjection.source_id.in_(list(active_result.memory_ids)),
                                    GraphProjection.status == GraphProjectionStatus.QUEUED,
                                )
                            )
                        ).scalars()
                    )
                )
            for projection_id in dict.fromkeys(projection_ids):
                trigger_graph_projection(projection_id)
            for memory_id in active_result.memory_ids:
                schedule_embedding_generation(memory_id)
            if active_result.memory_ids:
                # A successful governed write must immediately refresh the
                # Conversation Agent's formal-memory brief and its workspace
                # projection.  The brief itself contains only formal IDs and
                # never turns an Agent assertion into a user fact.
                await MemoryOperationsCoordinator(session).refresh_user_brief(
                    event.user_id
                )
            runtime_metrics.record_task("memory_extraction")

        except Exception as e:
            runtime_metrics.record_task("memory_extraction", failed=True)
            logger.error("Extraction failed event_id=%s error_type=%s", event_id, type(e).__name__)
            await session.rollback()
            result = await session.execute(
                select(RawEvent).where(RawEvent.id == event_id)
            )
            failed_event = result.scalar_one_or_none()
            if not failed_event:
                return
            try:
                failed_event.processing_status = ProcessingStatus.FAILED
                failed_event.processing_started_at = None
                failed_event.processing_heartbeat_at = datetime.now(timezone.utc)
                failed_event.processing_error = type(e).__name__
                failed_event.processing_result = "failed"
                if int(failed_event.processing_attempts or 0) < MAX_PROCESSING_ATTEMPTS:
                    delay_seconds = min(300, 30 * (2 ** max(0, int(failed_event.processing_attempts or 1) - 1)))
                    failed_event.processing_next_retry_at = datetime.now(timezone.utc) + timedelta(seconds=delay_seconds)
                else:
                    failed_event.processing_next_retry_at = None
                await session.commit()
            except Exception:
                await session.rollback()
                logger.error("Failed to mark extraction event as failed event_id=%s", event_id)


def _fast_drain_scope(cutoff: datetime):
    return and_(
        RawEvent.processing_status == ProcessingStatus.QUEUED,
        or_(RawEvent.ingested_at.is_(None), RawEvent.ingested_at <= cutoff),
    )


async def create_fast_drain_run(session, *, user_id: str):
    """Create one durable user-scoped drain, or return the active run."""
    from src.execution.models.memory_operations import MemoryMaintenanceRun
    from src.shared.ids.id_generator import generate_id

    active = await session.scalar(
        select(MemoryMaintenanceRun)
        .where(
            MemoryMaintenanceRun.user_id == user_id,
            MemoryMaintenanceRun.kind == "fast_drain",
            MemoryMaintenanceRun.state.in_(FAST_DRAIN_ACTIVE_STATES),
        )
        .order_by(MemoryMaintenanceRun.started_at.desc())
        .limit(1)
    )
    if active is not None:
        cursor = dict(active.cursor or {})
        heartbeat_raw = cursor.get("heartbeat_at")
        try:
            heartbeat = datetime.fromisoformat(str(heartbeat_raw))
            if heartbeat.tzinfo is None:
                heartbeat = heartbeat.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            heartbeat = active.started_at
            if heartbeat is not None and heartbeat.tzinfo is None:
                heartbeat = heartbeat.replace(tzinfo=timezone.utc)
        if heartbeat is not None and datetime.now(timezone.utc) - heartbeat <= timedelta(
            seconds=FAST_DRAIN_LEASE_SECONDS
        ):
            return active, False
        active.state = "failed"
        active.error = "fast_drain_lease_expired"
        active.finished_at = datetime.now(timezone.utc)
        await session.flush()

    cutoff = datetime.now(timezone.utc)
    backlog = await session.scalar(
        select(func.count(RawEvent.id)).where(
            RawEvent.user_id == user_id,
            _fast_drain_scope(cutoff),
        )
    )
    run_id = generate_id("mmr")
    idempotency_key = "fast-drain:" + hashlib.sha256(
        f"{user_id}:{run_id}".encode("utf-8")
    ).hexdigest()
    run = MemoryMaintenanceRun(
        id=run_id,
        user_id=user_id,
        kind="fast_drain",
        state="queued" if backlog else "completed",
        idempotency_key=idempotency_key,
        cursor={"cutoff_at": cutoff.isoformat(), "heartbeat_at": cutoff.isoformat()},
        counters={
            "starting_backlog": int(backlog or 0),
            "processed_events": 0,
            "remaining_events": int(backlog or 0),
            "model_calls": 0,
            "failed_events": 0,
        },
        token_budget=0,
        token_used=0,
        finished_at=cutoff if not backlog else None,
    )
    session.add(run)
    await session.flush()
    return run, True


async def fast_drain_status(session, *, user_id: str) -> dict:
    """Return bounded operator progress without exposing source content."""
    from src.execution.models.memory_operations import MemoryMaintenanceRun

    run = await session.scalar(
        select(MemoryMaintenanceRun)
        .where(
            MemoryMaintenanceRun.user_id == user_id,
            MemoryMaintenanceRun.kind == "fast_drain",
        )
        .order_by(MemoryMaintenanceRun.started_at.desc())
        .limit(1)
    )
    overall_backlog = await session.scalar(
        select(func.count(RawEvent.id)).where(
            RawEvent.user_id == user_id,
            RawEvent.processing_status == ProcessingStatus.QUEUED,
        )
    )
    if run is None:
        return {
            "run_id": None,
            "state": "idle",
            "active": False,
            "counters": {},
            "overall_backlog": int(overall_backlog or 0),
            "started_at": None,
            "finished_at": None,
            "error": None,
        }
    active = run.state in FAST_DRAIN_ACTIVE_STATES
    public_state = run.state
    if active:
        heartbeat_raw = dict(run.cursor or {}).get("heartbeat_at")
        try:
            heartbeat = datetime.fromisoformat(str(heartbeat_raw))
            if heartbeat.tzinfo is None:
                heartbeat = heartbeat.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            heartbeat = run.started_at
            if heartbeat is not None and heartbeat.tzinfo is None:
                heartbeat = heartbeat.replace(tzinfo=timezone.utc)
        if heartbeat is None or datetime.now(timezone.utc) - heartbeat > timedelta(
            seconds=FAST_DRAIN_LEASE_SECONDS
        ):
            active = False
            public_state = "stalled"
    return {
        "run_id": run.id,
        "state": public_state,
        "active": active,
        "counters": dict(run.counters or {}),
        "overall_backlog": int(overall_backlog or 0),
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "error": run.error,
    }


async def _run_fast_drain(run_id: str) -> None:
    """Immediately drain the queue snapshot using the governed Working Agent."""
    from src.execution.models.agent_runtime import AgentRun
    from src.execution.models.memory_operations import MemoryMaintenanceRun

    async with async_session() as session:
        claimed = await session.execute(
            update(MemoryMaintenanceRun)
            .where(
                MemoryMaintenanceRun.id == run_id,
                MemoryMaintenanceRun.kind == "fast_drain",
                MemoryMaintenanceRun.state == "queued",
            )
            .values(state="running", error=None)
        )
        await session.commit()
        if claimed.rowcount != 1:
            return

    try:
        while True:
            async with async_session() as session:
                run = await session.get(MemoryMaintenanceRun, run_id)
                if run is None or run.state != "running" or not run.user_id:
                    return
                cursor = dict(run.cursor or {})
                cutoff = datetime.fromisoformat(str(cursor["cutoff_at"]))
                if cutoff.tzinfo is None:
                    cutoff = cutoff.replace(tzinfo=timezone.utc)
                counters = dict(run.counters or {})
                max_calls = max(1, settings.WORKING_AGENT_FAST_DRAIN_MAX_BATCHES)
                if int(counters.get("model_calls") or 0) >= max_calls:
                    run.state = "budget_exhausted"
                    run.finished_at = datetime.now(timezone.utc)
                    run.error = "fast_drain_batch_budget_exhausted"
                    await session.commit()
                    return

                source_priority = case(
                    (RawEvent.source_type == SourceType.CONVERSATION, 0),
                    (RawEvent.source_type == SourceType.MANUAL, 1),
                    else_=2,
                )
                event = await session.scalar(
                    select(RawEvent)
                    .where(
                        RawEvent.user_id == run.user_id,
                        _fast_drain_scope(cutoff),
                    )
                    .order_by(source_priority.asc(), RawEvent.occurred_at.asc())
                    .limit(1)
                )
                if event is None:
                    run.state = (
                        "completed_with_errors"
                        if int(counters.get("failed_events") or 0)
                        else "completed"
                    )
                    counters["remaining_events"] = 0
                    run.counters = counters
                    run.finished_at = datetime.now(timezone.utc)
                    await session.commit()
                    return
                event_id = event.id
                user_id = run.user_id
                run_started_at = run.started_at

            await _process_memory_event(
                event_id,
                operator_drain=True,
                operator_cutoff=cutoff,
            )

            async with async_session() as session:
                run = await session.get(MemoryMaintenanceRun, run_id)
                if run is None or run.state != "running":
                    return
                event = await session.get(RawEvent, event_id)
                cutoff = datetime.fromisoformat(str((run.cursor or {})["cutoff_at"]))
                if cutoff.tzinfo is None:
                    cutoff = cutoff.replace(tzinfo=timezone.utc)
                remaining = await session.scalar(
                    select(func.count(RawEvent.id)).where(
                        RawEvent.user_id == user_id,
                        _fast_drain_scope(cutoff),
                    )
                )
                call_row = (
                    await session.execute(
                        select(
                            func.coalesce(func.sum(AgentRun.model_call_count), 0),
                            func.coalesce(
                                func.sum(
                                    func.coalesce(AgentRun.input_tokens, 0)
                                    + func.coalesce(AgentRun.output_tokens, 0)
                                ),
                                0,
                            ),
                        ).where(
                            AgentRun.user_id == user_id,
                            AgentRun.trigger_type == "raw_event",
                            AgentRun.trigger_id == event_id,
                            AgentRun.started_at >= run_started_at,
                        )
                    )
                ).one()
                counters = dict(run.counters or {})
                starting = int(counters.get("starting_backlog") or 0)
                counters["remaining_events"] = int(remaining or 0)
                counters["processed_events"] = max(0, starting - int(remaining or 0))
                counters["model_calls"] = int(counters.get("model_calls") or 0) + int(call_row[0] or 0)
                if event is not None and event.processing_status == ProcessingStatus.FAILED:
                    counters["failed_events"] = int(counters.get("failed_events") or 0) + 1
                cursor = dict(run.cursor or {})
                cursor["heartbeat_at"] = datetime.now(timezone.utc).isoformat()
                cursor["last_event_id"] = event_id
                run.cursor = cursor
                run.counters = counters
                run.token_used = int(run.token_used or 0) + int(call_row[1] or 0)
                await session.commit()
    except Exception as exc:
        logger.error("Fast drain failed run_id=%s error_type=%s", run_id, type(exc).__name__)
        async with async_session() as session:
            run = await session.get(MemoryMaintenanceRun, run_id)
            if run is not None:
                run.state = "failed"
                run.error = type(exc).__name__[:256]
                run.finished_at = datetime.now(timezone.utc)
                await session.commit()


async def _wait_for_existing_extraction(
    event_id: str,
    *,
    timeout_seconds: float = 10.0,
    poll_interval_seconds: float = 0.05,
) -> ProcessingStatus | None:
    """Let duplicate extraction triggers observe the worker already processing.

    API ingestion can enqueue a daemon extraction while tests or callers also
    invoke extraction synchronously. Returning immediately on PROCESSING creates
    a race where the caller reads stale in-progress state. Waiting briefly keeps
    duplicate triggers idempotent without taking over another worker's job.
    """
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while True:
        async with async_session() as session:
            result = await session.execute(select(RawEvent).where(RawEvent.id == event_id))
            event = result.scalar_one_or_none()
            if not event:
                return None
            if event.processing_status != ProcessingStatus.PROCESSING:
                return event.processing_status

        if asyncio.get_running_loop().time() >= deadline:
            return ProcessingStatus.PROCESSING
        await asyncio.sleep(poll_interval_seconds)


def schedule_embedding_generation(memory_id: str) -> None:
    """Trigger embedding generation asynchronously — must not block the pipeline."""
    async def _run() -> None:
        try:
            ok = await generate_embedding_for_memory_with_retry(memory_id)
            if ok:
                runtime_metrics.record_task("embedding_generation")
                logger.info(f"Embedding generated for memory {memory_id}")
            else:
                runtime_metrics.record_task("embedding_generation", failed=True)
                logger.warning(f"Embedding generation failed for memory {memory_id}")
        except Exception as e:
            runtime_metrics.record_task("embedding_generation", failed=True)
            logger.error("Embedding async failed memory_id=%s error_type=%s", memory_id, type(e).__name__)

    schedule_coroutine(_run())


async def generate_embedding_for_memory_with_retry(
    memory_id: str,
    max_retries: int = 3,
    retry_delay: float = 1.0,
) -> bool:
    last_error = None
    for attempt in range(max_retries):
        try:
            async with async_session() as session:
                return await generate_embedding_for_memory(session, memory_id)
        except Exception as e:
            last_error = e
            if attempt < max_retries - 1:
                await asyncio.sleep(retry_delay * (2 ** attempt))
    logger.error(f"Embedding failed after {max_retries} retries for {memory_id}: {last_error}")
    return False


async def generate_embedding_for_memory(session, memory_id: str) -> bool:
    """Generate embedding for a single committed memory.

    Uses LLM provider if available; falls back to deterministic hash embedding.
    Embedding failure MUST NOT block the system.
    """
    from src.memory.models.committed_memory import CommittedMemory

    result = await session.execute(
        select(CommittedMemory).where(CommittedMemory.id == memory_id)
    )
    memory = result.scalar_one_or_none()
    if not memory:
        return False

    existing = await session.execute(
        select(MemoryEmbedding).where(MemoryEmbedding.memory_id == memory_id).limit(1)
    )
    if existing.scalar_one_or_none():
        return True

    text = f"{memory.title}\n{memory.body}"
    if len(text) > 8000:
        text = text[:8000]

    vector = None
    used_fallback = memory.sensitivity == SensitivityLevel.SENSITIVE
    if not used_fallback:
        try:
            provider = get_llm_provider()
            vector = await provider.embed(text)
        except Exception:
            vector = None

    if not vector or not isinstance(vector, list) or len(vector) == 0:
        vector = deterministic_fallback_embedding(text, DEFAULT_EMBEDDING_DIM)
        used_fallback = True

    # 维度标准化双保险：provider 层已标准化，这里再次确保与 EMBEDDING_DIMENSION 一致，
    # 避免任何路径写入与 memory_embeddings.embedding_vector 列定义不匹配的向量。
    target_dim = settings.EMBEDDING_DIMENSION
    if isinstance(vector, list) and len(vector) != target_dim:
        if len(vector) < target_dim:
            vector = vector + [0.0] * (target_dim - len(vector))
        else:
            vector = vector[:target_dim]

    dimension = len(vector)

    embedding = MemoryEmbedding(
        id=generate_embedding_id(),
        memory_id=memory_id,
        embedding_model="fallback" if used_fallback else "default",
        embedding_vector=vector,
        content_snapshot=text[:2000],
        dimension=dimension,
    )
    session.add(embedding)
    await session.commit()

    await _try_upsert_zvec(memory_id, vector, memory)

    return True


async def _try_upsert_zvec(memory_id: str, vector: List[float], memory) -> None:
    """尝试将 embedding upsert 到 Zvec 索引（尽力而为，失败不影响主流程）。"""
    try:
        from src.memory.services.vector_index_backend import get_vector_index_backend

        backend = get_vector_index_backend()
        if not backend.is_available():
            return

        metadata = {
            "memory_type": memory.memory_type.value,
            "sensitivity": memory.sensitivity.value,
            "importance": float(memory.importance or 0.0),
            "user_id": memory.user_id,
            "project_id": memory.project_id or "",
        }

        if not backend.upsert(memory_id, vector, metadata):
            logger.warning(f"Zvec upsert failed for memory {memory_id}")
    except Exception as e:
        logger.warning(f"Zvec upsert exception for memory {memory_id}: {e}")
