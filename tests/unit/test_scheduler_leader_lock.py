from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone


class _ScalarResult:
    def __init__(self, value: bool) -> None:
        self._value = value

    def scalar(self) -> bool:
        return self._value


class _Connection:
    def __init__(self, acquired: bool) -> None:
        self.acquired = acquired
        self.closed = False
        self.calls: list[str] = []

    def execute(self, statement, _params=None):
        self.calls.append(str(statement))
        return _ScalarResult(self.acquired)

    def close(self) -> None:
        self.closed = True


class _Engine:
    def __init__(self, connection: _Connection) -> None:
        self.connection = connection
        self.connect_calls = 0

    def connect(self) -> _Connection:
        self.connect_calls += 1
        return self.connection


def test_postgres_scheduler_lock_blocks_follower_without_starting_jobs(monkeypatch) -> None:
    from src.shared.config import settings
    from src.shared.db import scheduler

    connection = _Connection(acquired=False)
    monkeypatch.setattr(settings, "POSTGRES_URL", "postgresql://leader-lock-test")
    monkeypatch.setattr(scheduler, "sync_engine", _Engine(connection))
    monkeypatch.setattr(scheduler, "_leader_lock_connection", None)

    assert scheduler.acquire_scheduler_leader_lock() is False
    assert connection.closed is True
    assert scheduler._leader_lock_connection is None


def test_postgres_scheduler_lock_is_released_on_shutdown(monkeypatch) -> None:
    from src.shared.config import settings
    from src.shared.db import scheduler

    connection = _Connection(acquired=True)
    monkeypatch.setattr(settings, "POSTGRES_URL", "postgresql://leader-lock-test")
    monkeypatch.setattr(scheduler, "sync_engine", _Engine(connection))
    monkeypatch.setattr(scheduler, "_leader_lock_connection", None)

    assert scheduler.acquire_scheduler_leader_lock() is True
    assert scheduler._leader_lock_connection is connection

    scheduler.release_scheduler_leader_lock()

    assert connection.closed is True
    assert scheduler._leader_lock_connection is None
    assert any("pg_advisory_unlock" in statement for statement in connection.calls)


def test_follower_does_not_register_or_start_scheduler_jobs(monkeypatch) -> None:
    from src.shared.db import scheduler

    class _Scheduler:
        def __init__(self) -> None:
            self.add_job_calls = 0
            self.running = False

        def add_job(self, *_args, **_kwargs) -> None:
            self.add_job_calls += 1

    fake_scheduler = _Scheduler()
    monkeypatch.setattr(scheduler, "scheduler", fake_scheduler)
    monkeypatch.setattr(scheduler, "_scheduler_started", False)
    monkeypatch.setattr(scheduler, "acquire_scheduler_leader_lock", lambda: False)

    assert scheduler.start_scheduler() is False
    assert fake_scheduler.add_job_calls == 0


def test_scheduler_jobs_run_on_the_calling_event_loop() -> None:
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from src.shared.db import scheduler

    seen = []

    @scheduler.run_async
    async def job():
        seen.append(asyncio.get_running_loop())

    async def run() -> None:
        loop = asyncio.get_running_loop()
        await job()
        assert seen == [loop]

    assert isinstance(scheduler.scheduler, AsyncIOScheduler)
    asyncio.run(run())


def test_pending_queue_prioritizes_conversation_and_skips_future_retry() -> None:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from src.memory.models.raw_event import (
        ProcessingStatus,
        RawEvent,
        SensitivityLevel,
        SourceType,
        VisibilityScope,
    )
    from src.shared.db.database import Base
    from src.shared.db.scheduler import select_pending_events

    async def run() -> None:
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            factory = async_sessionmaker(engine, expire_on_commit=False)
            now = datetime.now(timezone.utc)
            common = dict(
                user_id="u1",
                sensitivity=SensitivityLevel.NORMAL,
                visibility_scope=VisibilityScope.PERSONAL,
                processing_status=ProcessingStatus.QUEUED,
            )
            async with factory() as db:
                db.add_all([
                    RawEvent(id="old-import", source_type=SourceType.AGENT_API, occurred_at=now - timedelta(days=2), content="old", content_hash="1", **common),
                    RawEvent(id="fresh-chat", source_type=SourceType.CONVERSATION, occurred_at=now, content="new", content_hash="2", **common),
                    RawEvent(id="future", source_type=SourceType.CONVERSATION, occurred_at=now, content="later", content_hash="3", processing_next_retry_at=now + timedelta(hours=1), **common),
                ])
                await db.commit()
                rows = await select_pending_events(db, now=now, limit=10)
                assert [row.id for row in rows] == ["fresh-chat", "old-import"]
        finally:
            await engine.dispose()

    asyncio.run(run())
