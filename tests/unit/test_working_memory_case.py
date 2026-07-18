import asyncio
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.execution.models.memory_work import MemoryWorkCase, MemoryWorkDecision, MemoryWorkEvidence
from src.execution.runtime.model import RuntimeModelResponse
from src.execution.runtime.working_agent import run_working_active
from src.execution.services.memory_commit_service import recover_ready_memory_commits
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


class ScriptedModel:
    def __init__(self, final_text: str):
        self.final_text = final_text
        self.calls = 0

    async def complete(self, **_kwargs):
        self.calls += 1
        return RuntimeModelResponse(text=self.final_text)


def _event(event_id: str, content: str) -> RawEvent:
    return RawEvent(
        id=event_id,
        user_id="u-case",
        source_type=SourceType.MANUAL,
        source_id="test",
        occurred_at=datetime.now(timezone.utc),
        content=content,
        content_hash=compute_content_hash(content),
        event_metadata={},
        sensitivity=SensitivityLevel.NORMAL,
        visibility_scope=VisibilityScope.PERSONAL,
        processing_status=ProcessingStatus.PROCESSING,
    )


def _proposal(content: str) -> str:
    return (
        '{"business_state":"MEMORY_READY","memories":['
        '{"memory_type":"task","title":"搬家计划","content":"'
        + content
        + '","importance":0.8,"confidence":0.8,"sensitivity":"normal","entities":["搬家"]}'
        "]}"
    )


def _mapping(event: RawEvent) -> dict:
    return {
        "id": event.id,
        "user_id": event.user_id,
        "content": event.content,
        "source_type": event.source_type,
        "sensitivity": event.sensitivity,
        "metadata": event.event_metadata,
    }


def test_working_agent_is_the_only_writer_and_replay_is_idempotent(monkeypatch):
    from src.shared.config import settings

    async def run():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            factory = async_sessionmaker(engine, expire_on_commit=False)
            async with factory() as db:
                monkeypatch.setattr(settings, "AGENT_RUNTIME_ENABLED", True)
                monkeypatch.setattr(settings, "WORKING_AGENT_ACTIVE_ENABLED", True)
                event = _event("evt-idempotent", "我准备搬去杭州")
                db.add(event)
                await db.commit()
                model = ScriptedModel(_proposal("我准备搬去杭州"))

                first = await run_working_active(db, raw_event=_mapping(event), model=model)
                second = await run_working_active(db, raw_event=_mapping(event), model=model)
                await db.commit()

                assert first is not None and second is not None
                assert first.memory_ids == second.memory_ids
                assert len(first.memory_ids) == 1
                assert model.calls == 1
                assert await db.scalar(select(func.count()).select_from(MemoryWorkCase)) == 1
                assert await db.scalar(select(func.count()).select_from(MemoryWorkEvidence)) == 1
                assert await db.scalar(select(func.count()).select_from(MemoryWorkDecision)) == 1
                assert await db.scalar(select(func.count()).select_from(CommittedMemory)) == 1
                assert await db.scalar(select(func.count()).select_from(MemorySource)) == 1
                memory = await db.get(CommittedMemory, first.memory_ids[0])
                assert memory.origin_kind == "working_agent"
                assert memory.source_work_case_id
                assert memory.source_work_decision_id
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_same_proposition_creates_a_superseding_formal_memory_revision(monkeypatch):
    from src.shared.config import settings

    async def run():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            factory = async_sessionmaker(engine, expire_on_commit=False)
            async with factory() as db:
                monkeypatch.setattr(settings, "AGENT_RUNTIME_ENABLED", True)
                monkeypatch.setattr(settings, "WORKING_AGENT_ACTIVE_ENABLED", True)
                first_event = _event("evt-revision-1", "我准备搬去杭州")
                second_event = _event("evt-revision-2", "我下个月搬去杭州")
                db.add_all([first_event, second_event])
                await db.commit()

                first = await run_working_active(
                    db, raw_event=_mapping(first_event), model=ScriptedModel(_proposal("我准备搬去杭州"))
                )
                second = await run_working_active(
                    db, raw_event=_mapping(second_event), model=ScriptedModel(_proposal("我下个月搬去杭州"))
                )
                await db.commit()

                assert first is not None and second is not None
                assert first.memory_ids != second.memory_ids
                memories = list(
                    (await db.execute(select(CommittedMemory).order_by(CommittedMemory.revision))).scalars()
                )
                assert [item.revision for item in memories] == [1, 2]
                assert memories[0].status == CommittedStatus.SUPERSEDED
                assert memories[1].status == CommittedStatus.ACTIVE
                case = (await db.execute(select(MemoryWorkCase))).scalar_one()
                assert case.active_memory_id == memories[1].id
                assert case.status == "resolved"
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_compensation_finishes_a_persisted_ready_decision():
    async def run():
        from src.execution.services.memory_case_service import MemoryCaseService

        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            factory = async_sessionmaker(engine, expire_on_commit=False)
            async with factory() as db:
                event = _event("evt-compensation", "我准备搬去杭州")
                db.add(event)
                await db.flush()
                service = MemoryCaseService(db)
                proposal = {
                    "memory_type": "task",
                    "title": "搬家计划",
                    "content": "我准备搬去杭州",
                    "importance": 0.8,
                    "confidence": 0.8,
                    "sensitivity": "normal",
                }
                case = await service.route_case(
                    user_id=event.user_id,
                    memory_type="task",
                    title="搬家计划",
                    content=event.content,
                    sensitivity="normal",
                    confidence=0.8,
                )
                await service.attach_evidence(case=case, event=event)
                decision = await service.record_decision(
                    case=case,
                    user_id=event.user_id,
                    event_id=event.id,
                    state="MEMORY_READY",
                    run_id=None,
                    proposal=proposal,
                    rationale="test interruption",
                    model="test",
                    prompt_id="working-test",
                    prompt_version="v2.4",
                    policy_result={"memory_proposal": proposal},
                )
                service.apply_state(case, "MEMORY_READY")
                await db.commit()

                result = await recover_ready_memory_commits(db)
                await db.commit()

                assert result["created_memory_ids"]
                await db.refresh(decision)
                await db.refresh(case)
                assert decision.memory_ids == result["created_memory_ids"]
                assert case.status == "resolved"
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_model_inference_cannot_become_a_formal_persona_fact():
    async def run():
        from src.execution.runtime.working_coordinator import WorkingCoordinator

        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            factory = async_sessionmaker(engine, expire_on_commit=False)
            async with factory() as db:
                event = _event("evt-inference", "可能是个完美主义者")
                event.source_type = SourceType.AGENT_API
                db.add(event)
                await db.flush()
                ids = await WorkingCoordinator(db).materialize_preclassified(
                    event=event,
                    proposals=(
                        {
                            "memory_type": "persona_hypothesis",
                            "title": "人格推测",
                            "content": event.content,
                            "importance": 0.8,
                            "confidence": 0.9,
                            "sensitivity": "normal",
                        },
                    ),
                    origin="model_inference_test",
                )
                await db.commit()
                assert ids == ()
                assert await db.scalar(select(func.count()).select_from(CommittedMemory)) == 0
                case = (await db.execute(select(MemoryWorkCase))).scalar_one()
                assert case.status == "awaiting_evidence"
        finally:
            await engine.dispose()

    asyncio.run(run())
