from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.execution.models.memory_operations import EvidenceSeal, MemoryMaintenanceRun, UserMemoryBrief
from src.execution.runtime.conversation_coordinator import ConversationCoordinator
from src.execution.services.memory_operations import MemoryOperationsCoordinator, _memory_body_text
from src.memory.models.committed_memory import CommittedMemory, CommittedStatus
from src.memory.models.memory_source import MemorySource
from src.memory.models.memory_type import MemoryType
from src.memory.models.raw_event import ProcessingStatus, RawEvent, SensitivityLevel, SourceType, VisibilityScope
from src.shared.db.database import Base


def test_noise_event_is_completed_without_working_model() -> None:
    async def run() -> None:
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            factory = async_sessionmaker(engine, expire_on_commit=False)
            async with factory() as db:
                event = RawEvent(
                    id="evt-noise", user_id="u1", source_type=SourceType.CONVERSATION,
                    occurred_at=datetime.now(timezone.utc), content="测试", content_hash="noise",
                    sensitivity=SensitivityLevel.NORMAL, visibility_scope=VisibilityScope.PERSONAL,
                    processing_status=ProcessingStatus.PROCESSING,
                )
                db.add(event)
                result = await MemoryOperationsCoordinator(db).process_event(event)
                assert result.skipped is True
                assert result.state == "DISCARDED"
                assert event.retention_state == "purge_30d"
                assert event.purge_after is not None
        finally:
            await engine.dispose()
    asyncio.run(run())


def test_legacy_memory_body_shapes_are_safe_for_brief_projection() -> None:
    assert _memory_body_text(["大连", "河北高碑店"]) == "大连 河北高碑店"
    assert _memory_body_text({"city": "大连", "other": ["高碑店"]}) == "大连 高碑店"


def test_agent_api_explicit_words_still_use_microbatch() -> None:
    event = RawEvent(
        id="evt-agent-import",
        user_id="u1",
        source_type=SourceType.AGENT_API,
        occurred_at=datetime.now(timezone.utc),
        content="请记住这份外部 Agent 整理结果",
        content_hash="agent-import",
        sensitivity=SensitivityLevel.NORMAL,
        visibility_scope=VisibilityScope.PERSONAL,
        processing_status=ProcessingStatus.QUEUED,
    )
    assert MemoryOperationsCoordinator.classify_event(event) == "ordinary"


def test_budget_resets_at_shanghai_midnight(monkeypatch) -> None:
    from src.shared.config import settings

    monkeypatch.setattr(settings, "WORKING_AGENT_BUDGET_TIMEZONE", "Asia/Shanghai")
    start, end = MemoryOperationsCoordinator._budget_window(
        datetime(2026, 7, 22, 12, 0, tzinfo=timezone.utc)
    )
    assert start == datetime(2026, 7, 21, 16, 0, tzinfo=timezone.utc)
    assert end == datetime(2026, 7, 22, 16, 0, tzinfo=timezone.utc)


def test_ordinary_event_waits_for_microbatch_without_working_model() -> None:
    async def run() -> None:
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            factory = async_sessionmaker(engine, expire_on_commit=False)
            async with factory() as db:
                event = RawEvent(
                    id="evt-ordinary", user_id="u1", source_type=SourceType.MANUAL,
                    occurred_at=datetime.now(timezone.utc), content="今天读完了一本书，感觉很有收获。",
                    content_hash="ordinary", sensitivity=SensitivityLevel.NORMAL,
                    visibility_scope=VisibilityScope.PERSONAL,
                    processing_status=ProcessingStatus.PROCESSING,
                )
                db.add(event)
                await db.flush()
                result = await MemoryOperationsCoordinator(db).process_event(event)
                assert result.state == "DEFERRED"
                assert result.skipped is True
                assert result.deferred_until is not None
        finally:
            await engine.dispose()
    asyncio.run(run())


def test_brief_uses_only_active_non_sensitive_formal_memory() -> None:
    async def run() -> None:
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            factory = async_sessionmaker(engine, expire_on_commit=False)
            async with factory() as db:
                now = datetime.now(timezone.utc)
                db.add_all([
                    CommittedMemory(id="mem-visible", user_id="u1", memory_type=MemoryType.FACT, title="城市", body="居住在大连", confidence=.9, importance=.9, sensitivity=SensitivityLevel.NORMAL, visibility_scope=VisibilityScope.PERSONAL, epistemic_status="user_assertion", status=CommittedStatus.ACTIVE, valid_from=now),
                    CommittedMemory(id="mem-private", user_id="u1", memory_type=MemoryType.FACT, title="密码", body="不应进入摘要", confidence=.9, importance=1, sensitivity=SensitivityLevel.PRIVATE, visibility_scope=VisibilityScope.PERSONAL, epistemic_status="user_assertion", status=CommittedStatus.ACTIVE, valid_from=now),
                ])
                await db.flush()
                changed = await MemoryOperationsCoordinator(db).refresh_user_brief("u1")
                brief = await db.scalar(select(UserMemoryBrief).where(UserMemoryBrief.user_id == "u1"))
                assert changed is True
                assert brief is not None
                assert "大连" in brief.content
                assert "不应进入摘要" not in brief.content
        finally:
            await engine.dispose()
    asyncio.run(run())


def test_conversation_context_includes_only_the_working_agent_brief() -> None:
    context = ConversationCoordinator._render_context(
        episodes=[], attention=[], memory_brief="# 当前正式记忆摘要\n- [mem-a] 城市：居住在大连",
    )
    assert "Working Agent 已治理的正式记忆" in context
    assert "居住在大连" in context


def test_old_active_memory_source_is_sealed_then_event_is_removed() -> None:
    async def run() -> None:
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            factory = async_sessionmaker(engine, expire_on_commit=False)
            async with factory() as db:
                now = datetime.now(timezone.utc)
                event = RawEvent(id="evt-old", user_id="u1", source_type=SourceType.MANUAL, occurred_at=now - timedelta(days=181), content="我定居在大连", content_hash="old", sensitivity=SensitivityLevel.NORMAL, visibility_scope=VisibilityScope.PERSONAL, processing_status=ProcessingStatus.COMPLETED)
                memory = CommittedMemory(id="mem-old", user_id="u1", memory_type=MemoryType.FACT, title="居住地", body="定居大连", confidence=.9, importance=.9, sensitivity=SensitivityLevel.NORMAL, visibility_scope=VisibilityScope.PERSONAL, epistemic_status="user_assertion", status=CommittedStatus.ACTIVE, valid_from=now - timedelta(days=181))
                db.add_all([event, memory])
                await db.flush()
                db.add(MemorySource(id="src-old", memory_id=memory.id, raw_event_id=event.id, quote=event.content, source_type=SourceType.MANUAL))
                await db.flush()
                run = MemoryMaintenanceRun(id="run-old", kind="retention", state="running", idempotency_key="test-old", cursor={}, counters={}, token_budget=0, token_used=0)
                db.add(run)
                await db.flush()
                changed = await MemoryOperationsCoordinator(db)._seal_and_delete(run, event)
                await db.flush()
                source = await db.get(MemorySource, "src-old")
                seal = await db.scalar(select(EvidenceSeal).where(EvidenceSeal.source_event_id == "evt-old"))
                assert changed is True
                assert await db.get(RawEvent, "evt-old") is None
                assert source.raw_event_id is None
                assert source.evidence_seal_id == seal.id
                assert seal.excerpt == "我定居在大连"
        finally:
            await engine.dispose()
    asyncio.run(run())
