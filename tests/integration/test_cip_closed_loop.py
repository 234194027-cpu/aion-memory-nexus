"""V2.4 autonomous Working-Agent closed-loop integration checks."""

import asyncio
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.execution.models.memory_work import MemoryWorkCase, MemoryWorkDecision, MemoryWorkEvidence
from src.execution.runtime.working_coordinator import WorkingCoordinator
from src.memory.models.committed_memory import CommittedMemory, CommittedStatus
from src.memory.models.memory_source import MemorySource
from src.memory.models.raw_event import (
    ProcessingStatus,
    RawEvent,
    SensitivityLevel,
    SourceType,
    VisibilityScope,
)
from src.shared.db.database import Base
from src.shared.utils.hash import compute_content_hash


def test_preclassified_user_evidence_traverses_case_decision_and_formal_memory():
    async def run():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            factory = async_sessionmaker(engine, expire_on_commit=False)
            async with factory() as db:
                event = RawEvent(
                    id="evt-v24-loop",
                    user_id="u-v24-loop",
                    source_type=SourceType.MANUAL,
                    source_id="integration-test",
                    occurred_at=datetime.now(timezone.utc),
                    content="我下个月搬到大连",
                    content_hash=compute_content_hash("我下个月搬到大连"),
                    sensitivity=SensitivityLevel.NORMAL,
                    visibility_scope=VisibilityScope.PERSONAL,
                    processing_status=ProcessingStatus.PROCESSING,
                )
                db.add(event)
                await db.flush()
                memory_ids = await WorkingCoordinator(db).materialize_preclassified(
                    event=event,
                    proposals=(
                        {
                            "memory_type": "task",
                            "title": "搬家计划",
                            "content": event.content,
                            "importance": 0.8,
                            "confidence": 0.9,
                            "sensitivity": "normal",
                            "entities": ["大连", "搬家"],
                        },
                    ),
                    origin="integration_test",
                )
                await db.commit()

                assert len(memory_ids) == 1
                assert await db.scalar(select(func.count()).select_from(MemoryWorkCase)) == 1
                assert await db.scalar(select(func.count()).select_from(MemoryWorkEvidence)) == 1
                assert await db.scalar(select(func.count()).select_from(MemoryWorkDecision)) == 1
                assert await db.scalar(select(func.count()).select_from(MemorySource)) == 1
                memory = await db.get(CommittedMemory, memory_ids[0])
                assert memory.status == CommittedStatus.ACTIVE
                assert memory.origin_kind == "working_agent"
                assert memory.source_work_case_id
                assert memory.source_work_decision_id
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_noise_can_close_without_creating_a_formal_memory():
    async def run():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            factory = async_sessionmaker(engine, expire_on_commit=False)
            async with factory() as db:
                event = RawEvent(
                    id="evt-v24-noise",
                    user_id="u-v24-loop",
                    source_type=SourceType.MANUAL,
                    occurred_at=datetime.now(timezone.utc),
                    content="测试",
                    content_hash=compute_content_hash("测试"),
                    sensitivity=SensitivityLevel.NORMAL,
                    visibility_scope=VisibilityScope.PERSONAL,
                    processing_status=ProcessingStatus.PROCESSING,
                )
                db.add(event)
                await db.flush()
                ids = await WorkingCoordinator(db).materialize_preclassified(
                    event=event,
                    proposals=(
                        {
                            "memory_type": "fact",
                            "title": "测试",
                            "content": "测试",
                            "importance": 0.1,
                            "confidence": 0.9,
                        },
                    ),
                    origin="noise_test",
                )
                await db.commit()
                assert ids == ()
                assert await db.scalar(select(func.count()).select_from(CommittedMemory)) == 0
                case = (await db.execute(select(MemoryWorkCase))).scalar_one()
                assert case.status == "awaiting_evidence"
        finally:
            await engine.dispose()

    asyncio.run(run())
