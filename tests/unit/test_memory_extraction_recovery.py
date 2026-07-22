import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.memory.models.raw_event import (
    ProcessingStatus,
    RawEvent,
    SensitivityLevel,
    SourceType,
    VisibilityScope,
)
from src.shared.db.database import async_session, init_db
from src.shared.db.database import Base
from src.shared.utils.hash import compute_content_hash


def test_stale_processing_event_can_be_reclaimed_without_reclaiming_a_fresh_lease() -> None:
    from src.memory.tasks.memory_extraction import claim_event_for_extraction

    async def run() -> None:
        await init_db()
        user_id = f"recovery-user-{uuid4().hex}"
        stale_id = f"stale-{uuid4().hex}"
        fresh_id = f"fresh-{uuid4().hex}"
        now = datetime.now(timezone.utc)
        async with async_session() as session:
            session.add_all([
                RawEvent(
                    id=stale_id, user_id=user_id, source_type=SourceType.MANUAL, source_id="test",
                    occurred_at=now, content="stale", content_hash=compute_content_hash("stale"),
                    sensitivity=SensitivityLevel.NORMAL, visibility_scope=VisibilityScope.PROJECT,
                    processing_status=ProcessingStatus.PROCESSING,
                    processing_started_at=now - timedelta(minutes=30),
                ),
                RawEvent(
                    id=fresh_id, user_id=user_id, source_type=SourceType.MANUAL, source_id="test",
                    occurred_at=now, content="fresh", content_hash=compute_content_hash("fresh"),
                    sensitivity=SensitivityLevel.NORMAL, visibility_scope=VisibilityScope.PROJECT,
                    processing_status=ProcessingStatus.PROCESSING,
                    processing_started_at=now,
                ),
            ])
            await session.commit()
            assert await claim_event_for_extraction(session, stale_id, now=now) is not None
            assert await claim_event_for_extraction(session, fresh_id, now=now) is None
            claimed = await session.get(RawEvent, stale_id)
            # SQLite returns naive datetimes even for timezone-aware columns.
            assert claimed.processing_heartbeat_at.replace(tzinfo=timezone.utc) == now
            assert claimed.processing_result is None
            await session.execute(delete(RawEvent).where(RawEvent.user_id == user_id))
            await session.commit()

    asyncio.run(run())


def test_trigger_extraction_prefers_celery_enqueue(monkeypatch) -> None:
    from src.memory.tasks import memory_extraction

    enqueued: list[str] = []

    monkeypatch.setattr(
        memory_extraction.process_memory_event,
        "delay",
        lambda event_id: enqueued.append(event_id),
    )

    monkeypatch.setattr(
        memory_extraction,
        "schedule_coroutine",
        lambda _coroutine: (_ for _ in ()).throw(
            AssertionError("local fallback should not start when Celery accepts the task")
        ),
    )

    memory_extraction.trigger_extraction("event-celery")

    assert enqueued == ["event-celery"]


def test_legacy_runtime_failure_without_retry_timestamp_is_recovered_once() -> None:
    from src.memory.tasks.memory_extraction import recover_known_loop_failures

    async def run() -> None:
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            factory = async_sessionmaker(engine, expire_on_commit=False)
            now = datetime.now(timezone.utc)
            async with factory() as session:
                session.add(RawEvent(
                    id="legacy-runtime-no-retry",
                    user_id="u1",
                    source_type=SourceType.MANUAL,
                    occurred_at=now,
                    content="旧失败事件",
                    content_hash="legacy-runtime-no-retry",
                    sensitivity=SensitivityLevel.NORMAL,
                    visibility_scope=VisibilityScope.PERSONAL,
                    processing_status=ProcessingStatus.FAILED,
                    processing_attempts=1,
                    processing_error="RuntimeError",
                    processing_result="failed",
                    processing_next_retry_at=None,
                ))
                await session.commit()
                assert await recover_known_loop_failures(session, now=now) == 1
                assert await recover_known_loop_failures(session, now=now) == 0
                event = await session.get(RawEvent, "legacy-runtime-no-retry")
                assert event.processing_status is ProcessingStatus.QUEUED
                assert event.event_metadata["runtime_recovery_version"] == "2.5.2"
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_trigger_extraction_falls_back_to_persistent_loop_when_enqueue_fails(monkeypatch) -> None:
    from src.memory.tasks import memory_extraction
    from src.shared.config import settings

    def fail_enqueue(event_id: str) -> None:
        raise ConnectionError(f"broker unavailable for {event_id}")

    scheduled: list[object] = []

    def record_coroutine(coroutine) -> None:
        scheduled.append(coroutine)
        coroutine.close()

    monkeypatch.setattr(memory_extraction.process_memory_event, "delay", fail_enqueue)
    monkeypatch.setattr(memory_extraction, "schedule_coroutine", record_coroutine)
    monkeypatch.setattr(settings, "TESTING", False)

    memory_extraction.trigger_extraction("event-fallback")

    assert len(scheduled) == 1


def test_fast_drain_is_user_scoped_idempotent_and_completes(monkeypatch) -> None:
    from src.memory.tasks import memory_extraction
    from src.execution.models.memory_operations import MemoryMaintenanceRun

    async def run() -> None:
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            factory = async_sessionmaker(engine, expire_on_commit=False)
            now = datetime.now(timezone.utc)
            async with factory() as session:
                session.add_all([
                    RawEvent(
                        id="drain-u1", user_id="u1", source_type=SourceType.MANUAL,
                        occurred_at=now, content="u1", content_hash="drain-u1",
                        sensitivity=SensitivityLevel.NORMAL, visibility_scope=VisibilityScope.PERSONAL,
                        processing_status=ProcessingStatus.QUEUED,
                        processing_next_retry_at=now + timedelta(days=1),
                    ),
                    RawEvent(
                        id="drain-u2", user_id="u2", source_type=SourceType.MANUAL,
                        occurred_at=now, content="u2", content_hash="drain-u2",
                        sensitivity=SensitivityLevel.NORMAL, visibility_scope=VisibilityScope.PERSONAL,
                        processing_status=ProcessingStatus.QUEUED,
                    ),
                ])
                await session.commit()
                first, created = await memory_extraction.create_fast_drain_run(session, user_id="u1")
                second, duplicate_created = await memory_extraction.create_fast_drain_run(session, user_id="u1")
                assert created is True
                assert duplicate_created is False
                assert first.id == second.id
                await session.commit()

            async def complete_event(
                event_id: str,
                *,
                operator_drain: bool = False,
                operator_cutoff=None,
            ):
                assert operator_drain is True
                assert operator_cutoff is not None
                async with factory() as session:
                    await session.execute(
                        update(RawEvent)
                        .where(RawEvent.id == event_id)
                        .values(
                            processing_status=ProcessingStatus.COMPLETED,
                            processing_next_retry_at=None,
                            processing_result="discarded",
                        )
                    )
                    await session.commit()

            monkeypatch.setattr(memory_extraction, "async_session", factory)
            monkeypatch.setattr(memory_extraction, "_process_memory_event", complete_event)
            await memory_extraction._run_fast_drain(first.id)

            async with factory() as session:
                run_row = await session.get(MemoryMaintenanceRun, first.id)
                u1_queued = await session.scalar(select(func.count(RawEvent.id)).where(
                    RawEvent.user_id == "u1", RawEvent.processing_status == ProcessingStatus.QUEUED,
                ))
                u2_queued = await session.scalar(select(func.count(RawEvent.id)).where(
                    RawEvent.user_id == "u2", RawEvent.processing_status == ProcessingStatus.QUEUED,
                ))
                assert run_row.state == "completed"
                assert run_row.counters["processed_events"] == 1
                assert u1_queued == 0
                assert u2_queued == 1
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_active_working_failure_never_bypasses_the_working_agent(monkeypatch) -> None:
    """A runtime outage is retryable; no formal-memory bypass may be used."""
    from src.memory.models.committed_memory import CommittedMemory
    from src.memory.tasks import memory_extraction
    from src.shared.config import settings

    async def run() -> None:
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            session_factory = async_sessionmaker(engine, expire_on_commit=False)
            event_id = "active-runtime-fallback-event"
            async with session_factory() as session:
                session.add(RawEvent(
                    id=event_id,
                    user_id="u-active-fallback",
                    source_type=SourceType.MANUAL,
                    source_id="test",
                    occurred_at=datetime.now(timezone.utc),
                    content="我要搬去杭州",
                    content_hash=compute_content_hash("我要搬去杭州"),
                    sensitivity=SensitivityLevel.NORMAL,
                    visibility_scope=VisibilityScope.PROJECT,
                    processing_status=ProcessingStatus.QUEUED,
                ))
                await session.commit()

            async def broken_active(*_args, **_kwargs):
                raise TimeoutError("simulated runtime timeout")

            monkeypatch.setattr(settings, "AGENT_RUNTIME_ENABLED", True)
            monkeypatch.setattr(settings, "WORKING_AGENT_ACTIVE_ENABLED", True)
            monkeypatch.setattr(memory_extraction, "async_session", session_factory)
            monkeypatch.setattr("src.execution.runtime.working_agent.run_working_active", broken_active)

            await memory_extraction._process_memory_event(event_id)

            async with session_factory() as session:
                event = await session.get(RawEvent, event_id)
                committed = list((await session.execute(select(CommittedMemory))).scalars())
                assert event.processing_status is ProcessingStatus.FAILED
                assert event.processing_result == "failed"
                assert committed == []
        finally:
            await engine.dispose()

    asyncio.run(run())
