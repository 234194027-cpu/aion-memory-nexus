import asyncio
from datetime import datetime, timedelta, timezone
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger
from src.shared.db.database import async_session, sync_engine
from src.shared.config import settings
from src.execution.models.agent_profile import AgentProfile
from src.memory.models.raw_event import RawEvent, ProcessingStatus, SourceType
from src.memory.models.committed_memory import CommittedMemory, CommittedStatus
from src.memory.models.memory_embedding import MemoryEmbedding
from sqlalchemy import and_, case, or_, select, text
import logging

logging.basicConfig()
logging.getLogger('apscheduler').setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler(timezone="Asia/Shanghai")
_scheduler_started = False
# PostgreSQL advisory locks are scoped to a database connection. Keeping this
# one connection open makes exactly one API process the scheduler leader; a
# crash or lost connection releases the lock at the database level.
SCHEDULER_LEADER_LOCK_KEY = 204118723
_leader_lock_connection = None
AGENT_CONTROLLED_JOB_IDS = (
    "scan_pending_events",
    "daily_memory_organize",
    "weekly_memory_maintenance",
    "backfill_embeddings_init",
    "backfill_embeddings_daily",
)

def acquire_scheduler_leader_lock() -> bool:
    """Acquire the cross-process scheduler leadership lock, failing closed."""
    global _leader_lock_connection
    if settings.POSTGRES_URL.startswith("sqlite"):
        # SQLite is supported for local development and isolated tests only.
        return True
    if _leader_lock_connection is not None:
        return True
    connection = None
    try:
        connection = sync_engine.connect()
        acquired = bool(connection.execute(
            text("SELECT pg_try_advisory_lock(:lock_key)"),
            {"lock_key": SCHEDULER_LEADER_LOCK_KEY},
        ).scalar())
        if not acquired:
            connection.close()
            logger.info("[Scheduler] Another process holds the PostgreSQL leader lock")
            return False
        _leader_lock_connection = connection
        logger.info("[Scheduler] Acquired PostgreSQL leader lock")
        return True
    except Exception as exc:
        if connection is not None:
            connection.close()
        logger.error("[Scheduler] Leader lock acquisition failed error_type=%s", type(exc).__name__)
        return False


def release_scheduler_leader_lock() -> None:
    """Release a held lock and its dedicated connection during graceful stop."""
    global _leader_lock_connection
    connection = _leader_lock_connection
    _leader_lock_connection = None
    if connection is None:
        return
    try:
        connection.execute(
            text("SELECT pg_advisory_unlock(:lock_key)"),
            {"lock_key": SCHEDULER_LEADER_LOCK_KEY},
        )
    except Exception as exc:
        logger.warning("[Scheduler] Leader lock release failed error_type=%s", type(exc).__name__)
    finally:
        connection.close()


def start_scheduler() -> bool:
    global _scheduler_started
    if _scheduler_started:
        return True
    if not acquire_scheduler_leader_lock():
        return False
    
    scheduler.add_job(
        scan_pending_events,
        IntervalTrigger(minutes=1),
        id="scan_pending_events",
        replace_existing=True,
        max_instances=1,
    )
    
    scheduler.add_job(
        daily_memory_organize,
        CronTrigger(hour=2, minute=10, timezone="Asia/Shanghai"),
        id="daily_memory_organize",
        replace_existing=True,
        max_instances=1,
    )
    
    scheduler.add_job(
        conversation_idle_reflection,
        IntervalTrigger(minutes=5),
        id="conversation_idle_reflection",
        replace_existing=True,
        max_instances=1,
    )

    scheduler.add_job(
        working_memory_commit_compensation,
        IntervalTrigger(minutes=5),
        id="working_memory_commit_compensation",
        replace_existing=True,
        max_instances=1,
    )

    scheduler.add_job(
        conversation_heartbeat,
        IntervalTrigger(minutes=30),
        id="conversation_heartbeat",
        replace_existing=True,
        max_instances=1,
    )

    scheduler.add_job(
        conversation_reflection_compensation,
        CronTrigger(hour=2, minute=30, timezone="Asia/Shanghai"),
        id="conversation_reflection_compensation",
        replace_existing=True,
        max_instances=1,
    )

    scheduler.add_job(
        conversation_memory_projection,
        IntervalTrigger(minutes=60),
        id="conversation_memory_projection",
        replace_existing=True,
        max_instances=1,
    )
    scheduler.add_job(
        weekly_memory_maintenance,
        CronTrigger(day_of_week="sun", hour=3, minute=10, timezone="Asia/Shanghai"),
        id="weekly_memory_maintenance",
        replace_existing=True,
        max_instances=1,
    )

    # Embedding 回填: 启动 30 秒后首次运行
    scheduler.add_job(
        backfill_embeddings,
        IntervalTrigger(seconds=30),
        id="backfill_embeddings_init",
        replace_existing=True,
        max_instances=1,
        next_run_time=datetime.now() + timedelta(seconds=30),
    )
    # Embedding 回填: 每天凌晨 2:00
    scheduler.add_job(
        backfill_embeddings,
        CronTrigger(hour=2, minute=0, timezone="Asia/Shanghai"),
        id="backfill_embeddings_daily",
        replace_existing=True,
        max_instances=1,
    )
    scheduler.add_job(
        recover_graph_projection_outbox,
        IntervalTrigger(minutes=2),
        id="graph_projection_recovery",
        replace_existing=True,
        max_instances=1,
    )

    try:
        scheduler.start()
        _scheduler_started = True
        logger.info("[Scheduler] Started with conversation reflection and heartbeat loops")
        return True
    except Exception:
        release_scheduler_leader_lock()
        raise

def stop_scheduler():
    global _scheduler_started
    if scheduler.running:
        scheduler.shutdown(wait=False)
        _scheduler_started = False
        logger.info("[Scheduler] Stopped")
    release_scheduler_leader_lock()


def update_scheduler_from_config():
    try:
        asyncio.get_running_loop().create_task(_update_scheduler_async())
    except RuntimeError:
        logger.error("[Scheduler] Update config requires the application event loop")
    except Exception as e:
        logger.error("[Scheduler] Update config error: %s", type(e).__name__)

async def _update_scheduler_async():
    async with async_session() as session:
        result = await session.execute(
            select(AgentProfile)
            .where(AgentProfile.status.is_(True))
            .where(AgentProfile.schedule_enabled.is_(True))
        )
        agent = result.scalars().first()
        
        if not agent:
            _pause_all_jobs()
            logger.info("[Scheduler] Agent-controlled jobs paused (no active schedule-enabled agent)")
            return
        
        if scheduler.get_job("scan_pending_events"):
            scheduler.resume_job("scan_pending_events")
            scheduler.reschedule_job(
                "scan_pending_events",
                trigger=IntervalTrigger(minutes=1)
            )
        
        if scheduler.get_job("daily_memory_organize"):
            scheduler.resume_job("daily_memory_organize")
            scheduler.reschedule_job(
                "daily_memory_organize",
                trigger=CronTrigger(hour=2, minute=10, timezone="Asia/Shanghai")
            )

        if scheduler.get_job("weekly_memory_maintenance"):
            scheduler.resume_job("weekly_memory_maintenance")
            scheduler.reschedule_job(
                "weekly_memory_maintenance",
                trigger=CronTrigger(day_of_week="sun", hour=3, minute=10, timezone="Asia/Shanghai"),
            )
        
        logger.info(
            "[Scheduler] Config updated: extraction=%smin, organize_hour=%s; "
            "weekly_summary/obsidian_sync are disabled until real idempotent jobs exist",
            agent.event_extraction_interval,
            agent.memory_organize_hour,
        )

def _pause_all_jobs():
    for job_id in AGENT_CONTROLLED_JOB_IDS:
        if scheduler.get_job(job_id):
            scheduler.pause_job(job_id)

async def has_enabled_scheduler_agent(session) -> bool:
    result = await session.execute(
        select(AgentProfile.id)
        .where(AgentProfile.status.is_(True))
        .where(AgentProfile.schedule_enabled.is_(True))
        .limit(1)
    )
    return result.scalar_one_or_none() is not None

def run_async(coro_func):
    async def wrapper():
        try:
            return await coro_func()
        except Exception as e:
            logger.error("[Scheduler] Job error: %s", e, exc_info=True)
    return wrapper


async def select_pending_events(session, *, now: datetime, limit: int) -> list[RawEvent]:
    """Select due events while keeping interactive work ahead of imports."""
    due_queued = and_(
        RawEvent.processing_status == ProcessingStatus.QUEUED,
        or_(
            RawEvent.processing_next_retry_at.is_(None),
            RawEvent.processing_next_retry_at <= now,
        ),
    )
    retryable_failed = and_(
        RawEvent.processing_status == ProcessingStatus.FAILED,
        RawEvent.processing_attempts < 3,
        RawEvent.processing_next_retry_at.is_not(None),
        RawEvent.processing_next_retry_at <= now,
    )
    source_priority = case(
        (RawEvent.source_type == SourceType.CONVERSATION, 0),
        (RawEvent.source_type == SourceType.MANUAL, 1),
        else_=2,
    )
    result = await session.execute(
        select(RawEvent)
        .where(or_(due_queued, retryable_failed))
        .order_by(source_priority.asc(), RawEvent.occurred_at.asc())
        .limit(limit)
    )
    return list(result.scalars())

@run_async
async def scan_pending_events():
    from src.memory.tasks.memory_extraction import (
        _process_memory_event,
        recover_known_loop_failures,
        recover_stale_extraction_events,
    )
    
    async with async_session() as session:
        recovered = await recover_stale_extraction_events(session)
        if recovered:
            logger.warning("[Scheduler] Recovered %s stale extraction lease(s)", recovered)
        now = datetime.now(timezone.utc)
        loop_recovered = await recover_known_loop_failures(session, now=now)
        if loop_recovered:
            logger.warning("[Scheduler] Recovered %s legacy event-loop failure(s)", loop_recovered)
        events = await select_pending_events(
            session,
            now=now,
            limit=max(1, settings.WORKING_AGENT_SCAN_BATCH_SIZE),
        )
        
        if not events:
            return

        logger.info(f"[Scheduler] Processing {len(events)} pending events")

        for event in events:
            try:
                await _process_memory_event(event.id)
            except Exception as e:
                logger.error(f"[Scheduler] Error processing event {event.id}: {e}")


@run_async
async def working_memory_commit_compensation():
    """Finish persisted Working-Agent decisions that lost their final write."""
    from src.execution.services.conversation_memory_projector import (
        try_refresh_conversation_memory_projection,
    )
    from src.execution.services.memory_commit_service import recover_ready_memory_commits
    from src.memory.tasks.memory_extraction import schedule_embedding_generation

    async with async_session() as session:
        result = await recover_ready_memory_commits(session)
        created_ids = result["created_memory_ids"]
        if created_ids:
            owner_ids = {
                row[0]
                for row in (
                    await session.execute(
                        select(CommittedMemory.user_id).where(
                            CommittedMemory.id.in_(created_ids)
                        )
                    )
                ).all()
                if row[0]
            }
            for user_id in owner_ids:
                await try_refresh_conversation_memory_projection(
                    session,
                    user_id=user_id,
                )
        await session.commit()
    for memory_id in result["created_memory_ids"]:
        try:
            schedule_embedding_generation(memory_id)
        except Exception as exc:
            logger.warning(
                "[Scheduler] Memory embedding enqueue failed memory_id=%s error_type=%s",
                memory_id,
                type(exc).__name__,
            )
    if result["scanned"]:
        logger.info("[Scheduler] Working memory compensation result=%s", result)


@run_async
async def recover_graph_projection_outbox():
    """Redeliver durable graph outbox rows; no source write occurs here."""
    from src.memory.models.graph_projection import GraphProjection, GraphProjectionStatus
    from src.memory.services.graph_projection import trigger_graph_projection

    if not settings.GRAPHITI_ENABLED:
        return
    now = datetime.now(timezone.utc)
    async with async_session() as session:
        rows = list(
            (
                await session.execute(
                    select(GraphProjection.id)
                    .where(
                        or_(
                            GraphProjection.status == GraphProjectionStatus.QUEUED,
                            and_(
                                GraphProjection.status == GraphProjectionStatus.FAILED,
                                GraphProjection.next_retry_at.is_not(None),
                                GraphProjection.next_retry_at <= now,
                            ),
                        )
                    )
                    .order_by(GraphProjection.created_at.asc())
                    .limit(50)
                )
            ).scalars()
        )
    for projection_id in rows:
        trigger_graph_projection(projection_id)
    if rows:
        logger.info("[Scheduler] Requeued %s graph projection(s)", len(rows))

@run_async
async def daily_memory_organize():
    from src.execution.services.memory_operations import MemoryOperationsCoordinator

    logger.info("[Scheduler] Running Working-Agent daily maintenance...")
    async with async_session() as session:
        if not await has_enabled_scheduler_agent(session):
            return
        result = await MemoryOperationsCoordinator(session).run_maintenance(kind="daily")
        await session.commit()
    logger.info("[Scheduler] Working-Agent daily maintenance completed: %s", result)


@run_async
async def weekly_memory_maintenance():
    """Deep but budgeted consolidation; it never deletes formal memory."""
    from src.execution.services.memory_operations import MemoryOperationsCoordinator

    async with async_session() as session:
        if not await has_enabled_scheduler_agent(session):
            return
        result = await MemoryOperationsCoordinator(session).run_maintenance(kind="weekly", limit_per_user=100)
        await session.commit()
    logger.info("[Scheduler] Working-Agent weekly maintenance completed: %s", result)

@run_async
async def weekly_summary():
    logger.info("[Scheduler] Running weekly summary...")
    async with async_session() as session:
        if not await has_enabled_scheduler_agent(session):
            return

    logger.info("[Scheduler] Weekly summary completed")

@run_async
async def obsidian_sync():
    logger.info("[Scheduler] Running Obsidian sync...")
    async with async_session() as session:
        if not await has_enabled_scheduler_agent(session):
            return

    logger.info("[Scheduler] Obsidian sync completed")


@run_async
async def conversation_idle_reflection():
    from src.execution.runtime.conversation_reflector import reflect_due_conversations

    completed = await reflect_due_conversations(limit=100)
    if completed:
        logger.info("[Scheduler] Reflected %s due conversation session(s)", completed)


@run_async
async def conversation_reflection_compensation():
    from src.execution.runtime.conversation_reflector import reflect_due_conversations

    completed = await reflect_due_conversations(force_overdue=True, limit=500)
    logger.info("[Scheduler] Nightly conversation reflection completed=%s", completed)


@run_async
async def conversation_heartbeat():
    from src.platform.services.conversation_heartbeat import run_conversation_heartbeat

    async with async_session() as session:
        result = await run_conversation_heartbeat(session)
    logger.info("[Scheduler] Conversation heartbeat result: %s", result.get("status"))


@run_async
async def conversation_memory_projection():
    from src.execution.services.conversation_memory_projector import (
        refresh_all_conversation_memory_projections,
    )

    async with async_session() as session:
        result = await refresh_all_conversation_memory_projections(session)
    logger.info(
        "[Scheduler] Conversation memory projection users=%s succeeded=%s failed=%s",
        result["users"],
        result["succeeded"],
        result["failed"],
    )


@run_async
async def backfill_embeddings():
    """回填缺失 embedding 的 ACTIVE committed memories。

    批量 20 条，延迟 1 秒；失败跳过单条。
    """
    from src.memory.tasks.memory_extraction import generate_embedding_for_memory

    logger.info("[Scheduler] Running embedding backfill...")
    batch_size = 20
    delay_seconds = 1.0
    processed = 0
    success = 0
    failed = 0

    while True:
        async with async_session() as session:
            # 查找 ACTIVE 且没有对应 MemoryEmbedding 记录的 memory
            embedded_subq = select(MemoryEmbedding.memory_id)
            result = await session.execute(
                select(CommittedMemory.id)
                .where(CommittedMemory.status == CommittedStatus.ACTIVE)
                .where(CommittedMemory.id.notin_(embedded_subq))
                .limit(batch_size)
            )
            ids = [row[0] for row in result.all()]

            if not ids:
                break

            for mid in ids:
                try:
                    ok = await generate_embedding_for_memory(session, mid)
                    if ok:
                        success += 1
                    else:
                        failed += 1
                except Exception as e:
                    failed += 1
                    logger.error(f"[Scheduler] Backfill error for {mid}: {e}")
                processed += 1

        if delay_seconds > 0:
            await asyncio.sleep(delay_seconds)

    logger.info(f"[Scheduler] Embedding backfill done: processed={processed}, success={success}, failed={failed}")
