import asyncio
from datetime import datetime, timedelta, timezone
import importlib.util
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.execution.models.agent_runtime import AgentRole, AgentSession, AgentSessionStatus
from src.execution.models.conversation import (
    ConversationAttentionCandidate,
    ConversationEpisode,
    ConversationReflectionCursor,
    ConversationTurn,
)
from src.platform.models.wecom_contact import WeComContact
from src.shared.db.database import Base


def test_conversation_preferences_have_no_mode_and_quiet_hours_wrap_midnight():
    from src.platform.services.conversation_preferences import (
        get_conversation_preferences,
        is_quiet_hour,
    )

    contact = SimpleNamespace(
        contact_metadata={
            "conversation_proactivity": {
                "enabled": False,
                "quiet_hours_start": 22,
                "quiet_hours_end": 8,
                "intensity": "high",
                "mode": "legacy-value",
            }
        }
    )
    preferences = get_conversation_preferences(contact)
    assert preferences["enabled"] is False
    assert preferences["intensity"] == "high"
    assert "mode" not in preferences
    assert is_quiet_hour(preferences, hour=23)
    assert is_quiet_hour(preferences, hour=7)
    assert not is_quiet_hour(preferences, hour=12)


def test_updating_proactivity_removes_legacy_mode_metadata():
    from src.platform.services.conversation_preferences import (
        update_conversation_preferences,
    )

    contact = SimpleNamespace(
        contact_metadata={
            "agent_interaction_mode": "question",
            "memory_question_preferences": {"mode": "review"},
        },
        updated_at=None,
    )

    async def commit():
        return None

    result = asyncio.run(
        update_conversation_preferences(
            SimpleNamespace(commit=commit),
            contact=contact,
            enabled=True,
            quiet_hours_start=21,
            quiet_hours_end=7,
            intensity="low",
        )
    )
    assert result["daily_limit"] == 2
    assert result["min_interval_hours"] == 6
    assert result["intensity"] == "low"
    assert "agent_interaction_mode" not in contact.contact_metadata
    assert "memory_question_preferences" not in contact.contact_metadata


def test_reflection_accepts_only_exact_user_grounded_memory_signals():
    from src.execution.runtime.conversation_reflector import _validated_signals

    user = ConversationTurn(id="user-1", role="user", content="我计划下个月搬到大连")
    assistant = ConversationTurn(id="assistant-1", role="assistant", content="你会搬到北京")
    payload = {
        "memory_signals": [
            {
                "kind": "plan",
                "quote": "我计划下个月搬到大连",
                "source_turn_id": user.id,
                "durable": True,
                "confidence": 0.9,
                "sensitivity": "normal",
            },
            {
                "kind": "fact",
                "quote": "你会搬到北京",
                "source_turn_id": assistant.id,
                "durable": True,
                "confidence": 0.9,
                "sensitivity": "normal",
            },
            {
                "kind": "fact",
                "quote": "我计划搬到北京",
                "source_turn_id": user.id,
                "durable": True,
                "confidence": 0.9,
                "sensitivity": "normal",
            },
        ]
    }
    signals = _validated_signals(payload, user_turns={user.id: user})
    assert signals == [
        {
            "kind": "plan",
            "quote": "我计划下个月搬到大连",
            "source_turn_id": "user-1",
            "confidence": 0.9,
            "sensitivity": "normal",
        }
    ]


def test_idle_chat_can_form_episode_without_memory_signal():
    from src.execution.runtime.conversation_reflector import _fallback_reflection

    payload = _fallback_reflection(
        [
            ConversationTurn(id="u1", role="user", content="你好"),
            ConversationTurn(id="a1", role="assistant", content="你好呀"),
        ]
    )
    assert payload["summary"]
    assert payload["memory_signals"] == []
    assert payload["attention_candidates"] == []


def test_fallback_reflection_does_not_promote_transient_activity():
    from src.execution.runtime.conversation_reflector import _fallback_reflection

    payload = _fallback_reflection(
        [ConversationTurn(id="u1", role="user", content="我在吃饭，等会再聊")]
    )
    assert payload["memory_signals"] == []
    assert payload["attention_candidates"] == []


def test_refusal_suppresses_proactive_candidates_for_reflection_window():
    from src.execution.runtime.conversation_reflector import _validated_attention

    turn = ConversationTurn(id="u1", role="user", content="我计划下个月搬家")
    payload = {
        "attention_candidates": [
            {
                "kind": "plan_follow_up",
                "prompt": "搬家计划进展怎么样？",
                "value_score": 0.9,
                "source_turn_id": turn.id,
                "quote": turn.content,
                "sensitivity": "normal",
            }
        ]
    }
    assert (
        _validated_attention(
            payload,
            user_turns={turn.id: turn},
            declined=["这个问题我不想回答"],
        )
        == []
    )


def test_workspace_never_duplicates_raw_transcript(tmp_path):
    from src.execution.runtime.workspace import AgentWorkspaceService

    workspace = AgentWorkspaceService(base_dir=tmp_path)
    workspace.record_turn(
        user_id="u1",
        agent="conversational",
        intent="chat",
        user_text="这是不能写进 Markdown 的用户原话",
        assistant_text="这是不能写进 Markdown 的回复",
    )
    root = workspace.load(user_id="u1", agent="conversational").root
    assert list((root / "memory").glob("*.md")) == []

    workspace.project_conversation_episode(
        user_id="u1",
        episode_id="episode-1",
        summary="用户讨论了搬家计划。",
        topics=["搬家"],
        open_loops=[{"text": "确认具体日期"}],
        asked_questions=["计划什么时候开始？"],
        declined_questions=[],
        reflected_at=datetime.now(timezone.utc),
    )
    rendered = "\n".join(
        path.read_text(encoding="utf-8")
        for path in root.rglob("*.md")
    )
    assert "用户讨论了搬家计划" in rendered
    assert "这是不能写进 Markdown 的用户原话" not in rendered
    assert "这是不能写进 Markdown 的回复" not in rendered


def test_heartbeat_sends_once_then_waits_for_response(monkeypatch):
    from src.platform.services.conversation_heartbeat import run_conversation_heartbeat

    sent: list[str] = []

    class Bot:
        async def send_text_message(self, _recipient, content):
            sent.append(content)
            return {"errcode": 0}

    monkeypatch.setattr("src.platform.channels.wecom.get_wecom_bot", lambda: Bot())

    async def run():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            factory = async_sessionmaker(engine, expire_on_commit=False)
            async with factory() as db:
                now = datetime.now(timezone.utc)
                session = AgentSession(
                    id="ases-heartbeat",
                    user_id="u-heartbeat",
                    agent_role=AgentRole.CONVERSATIONAL,
                    channel="wecom",
                    channel_session_key="direct:user",
                    status=AgentSessionStatus.ACTIVE,
                    context_version="conv-ledger-v1",
                )
                contact = WeComContact(
                    id="contact-heartbeat",
                    user_id="u-heartbeat",
                    wecom_user_id="recipient",
                    is_default=True,
                    contact_metadata={
                        "conversation_proactivity": {
                            "enabled": True,
                            "quiet_hours_start": None,
                            "quiet_hours_end": None,
                            "intensity": "high",
                        }
                    },
                )
                candidate = ConversationAttentionCandidate(
                    id="candidate-heartbeat",
                    user_id="u-heartbeat",
                    session_id=session.id,
                    kind="plan_follow_up",
                    prompt="搬家计划进展怎么样？",
                    value_score=0.9,
                    source="reflection",
                    sensitivity="normal",
                    status="pending",
                    due_at=now - timedelta(minutes=1),
                    source_turn_ids=["turn-1"],
                    proactive_allowed=True,
                    candidate_metadata={},
                )
                db.add_all([session, contact, candidate])
                await db.commit()

                first = await run_conversation_heartbeat(db, user_id="u-heartbeat")
                second = await run_conversation_heartbeat(db, user_id="u-heartbeat")
                assert first["status"] == "sent"
                assert second["status"] == "awaiting_response"
                assert sent == ["搬家计划进展怎么样？"]
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_coordinator_message_id_is_idempotent(monkeypatch, tmp_path):
    from sqlalchemy import func, select

    from src.execution.runtime import conversation_agent
    from src.execution.runtime.model import RuntimeModelResponse
    from src.execution.runtime.workspace import AgentWorkspaceService
    from src.shared.config import settings

    class CountingModel:
        def __init__(self):
            self.calls = 0

        async def complete(self, **_kwargs):
            self.calls += 1
            return RuntimeModelResponse(text="你好，我在。")

    async def run():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            factory = async_sessionmaker(engine, expire_on_commit=False)
            monkeypatch.setattr(settings, "AGENT_RUNTIME_ENABLED", True)
            monkeypatch.setattr(settings, "CONVERSATIONAL_AGENT_ENABLED", True)
            monkeypatch.setattr(
                conversation_agent,
                "AgentWorkspaceService",
                lambda: AgentWorkspaceService(base_dir=tmp_path),
            )
            model = CountingModel()
            async with factory() as db:
                first = await conversation_agent.run_conversational_turn(
                    db,
                    user_id="u-idempotent",
                    channel="wecom",
                    channel_session_key="direct:person",
                    message="你好",
                    message_id="channel-message-1",
                    model=model,
                )
                second = await conversation_agent.run_conversational_turn(
                    db,
                    user_id="u-idempotent",
                    channel="wecom",
                    channel_session_key="direct:person",
                    message="你好",
                    message_id="channel-message-1",
                    model=model,
                )
                assert first.turn_id == second.turn_id
                assert first.session_id == second.session_id
                assert model.calls == 1
                assert await db.scalar(
                    select(func.count()).select_from(ConversationTurn)
                ) == 2
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_coordinator_persists_user_turn_and_safe_reply_when_model_fails(
    monkeypatch, tmp_path
):
    from sqlalchemy import select

    from src.execution.runtime import conversation_agent
    from src.execution.runtime.conversation_coordinator import (
        SAFE_CONVERSATION_FALLBACK,
    )
    from src.execution.runtime.workspace import AgentWorkspaceService
    from src.shared.config import settings

    class BrokenModel:
        async def complete(self, **_kwargs):
            raise RuntimeError("provider unavailable")

    async def run():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            factory = async_sessionmaker(engine, expire_on_commit=False)
            monkeypatch.setattr(settings, "AGENT_RUNTIME_ENABLED", True)
            monkeypatch.setattr(settings, "CONVERSATIONAL_AGENT_ENABLED", True)
            monkeypatch.setattr(
                conversation_agent,
                "AgentWorkspaceService",
                lambda: AgentWorkspaceService(base_dir=tmp_path),
            )
            async with factory() as db:
                answer = await conversation_agent.run_conversational_turn(
                    db,
                    user_id="u-failure",
                    channel="wecom",
                    channel_session_key="direct:failure",
                    message="这句话不能丢",
                    message_id="failure-message-1",
                    model=BrokenModel(),
                )
                assert answer.text == SAFE_CONVERSATION_FALLBACK
                turns = list(
                    (
                        await db.execute(
                            select(ConversationTurn)
                        )
                    ).scalars()
                )
                by_role = {turn.role: turn for turn in turns}
                assert by_role["user"].content == "这句话不能丢"
                assert by_role["assistant"].content == SAFE_CONVERSATION_FALLBACK
                assert by_role["assistant"].reply_to_turn_id == by_role["user"].id
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_idle_reflection_cursor_is_due_in_ten_minutes():
    from src.execution.runtime.conversation_ledger import (
        ConversationLedger,
        IDLE_REFLECTION_MINUTES,
    )

    async def run():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            factory = async_sessionmaker(engine, expire_on_commit=False)
            async with factory() as db:
                ledger = ConversationLedger(db)
                session = await ledger.get_or_create_session(
                    user_id="u-idle",
                    channel="wecom",
                    channel_session_key="direct:idle",
                )
                before = datetime.now(timezone.utc)
                cursor = await ledger.advance_reflection_cursor(
                    session=session,
                    immediate=False,
                )
                after = datetime.now(timezone.utc)
                assert cursor.pending_user_turns == 1
                assert cursor.next_reflection_at is not None
                assert before + timedelta(
                    minutes=IDLE_REFLECTION_MINUTES
                ) <= cursor.next_reflection_at <= after + timedelta(
                    minutes=IDLE_REFLECTION_MINUTES
                )
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_reflection_is_idempotent_and_raw_event_quotes_only_user_turn(
    monkeypatch, tmp_path
):
    from sqlalchemy import func, select

    from src.execution.runtime import conversation_reflector
    from src.execution.runtime.workspace import AgentWorkspaceService
    from src.memory.models.raw_event import RawEvent, SourceType

    dispatched: list[str] = []

    async def reflected(_turns):
        return {
            "summary": "用户计划下个月搬到大连。",
            "topics": ["搬家"],
            "emotional_context": None,
            "open_loops": [
                {
                    "text": "确认具体搬家日期",
                    "source_turn_id": "turn-user",
                    "kind": "plan",
                }
            ],
            "memory_signals": [
                {
                    "kind": "plan",
                    "quote": "我计划下个月搬到大连",
                    "source_turn_id": "turn-user",
                    "durable": True,
                    "confidence": 0.95,
                    "sensitivity": "normal",
                },
                {
                    "kind": "fact",
                    "quote": "你已经搬到北京",
                    "source_turn_id": "turn-assistant",
                    "durable": True,
                    "confidence": 0.99,
                    "sensitivity": "normal",
                },
            ],
            "attention_candidates": [],
        }

    monkeypatch.setattr(conversation_reflector, "_model_reflection", reflected)
    monkeypatch.setattr(
        conversation_reflector,
        "AgentWorkspaceService",
        lambda: AgentWorkspaceService(base_dir=tmp_path),
    )
    monkeypatch.setattr(
        "src.memory.tasks.memory_extraction.trigger_extraction",
        lambda event_id: dispatched.append(event_id),
    )

    async def run():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            factory = async_sessionmaker(engine, expire_on_commit=False)
            monkeypatch.setattr(conversation_reflector, "async_session", factory)
            async with factory() as db:
                now = datetime.now(timezone.utc)
                db.add(
                    AgentSession(
                        id="session-reflect",
                        user_id="u-reflect",
                        agent_role=AgentRole.CONVERSATIONAL,
                        channel="wecom",
                        channel_session_key="direct:reflect",
                        status=AgentSessionStatus.ACTIVE,
                        context_version="conv-ledger-v1",
                    )
                )
                db.add_all(
                    [
                        ConversationTurn(
                            id="turn-user",
                            session_id="session-reflect",
                            user_id="u-reflect",
                            channel="wecom",
                            role="user",
                            content="我计划下个月搬到大连",
                            reflection_state="pending",
                            turn_metadata={},
                            created_at=now,
                        ),
                        ConversationTurn(
                            id="turn-assistant",
                            session_id="session-reflect",
                            user_id="u-reflect",
                            channel="wecom",
                            role="assistant",
                            content="你已经搬到北京",
                            reflection_state="pending",
                            turn_metadata={},
                            created_at=now + timedelta(seconds=1),
                        ),
                    ]
                )
                db.add(
                    ConversationReflectionCursor(
                        id="cursor-reflect",
                        session_id="session-reflect",
                        user_id="u-reflect",
                        pending_user_turns=4,
                        next_reflection_at=now,
                        running=False,
                    )
                )
                await db.commit()

            first = await conversation_reflector.reflect_session(
                "session-reflect", force=True
            )
            second = await conversation_reflector.reflect_session(
                "session-reflect", force=True
            )
            assert first is not None
            assert second is None

            async with factory() as db:
                assert await db.scalar(
                    select(func.count()).select_from(ConversationEpisode)
                ) == 1
                assert await db.scalar(select(func.count()).select_from(RawEvent)) == 1
                event = (await db.execute(select(RawEvent))).scalar_one()
                assert event.source_type is SourceType.CONVERSATION
                assert event.content == "我计划下个月搬到大连"
                assert event.event_metadata["source_turn_id"] == "turn-user"
                assert dispatched == [event.id]
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_delete_conversation_data_removes_ledger_and_only_conversation_evidence(
    monkeypatch, tmp_path
):
    from sqlalchemy import func, select

    from src.execution.runtime import conversation_deletion
    from src.execution.runtime.workspace import AgentWorkspaceService
    from src.memory.models.committed_memory import CommittedMemory, CommittedStatus
    from src.memory.models.memory_type import MemoryType
    from src.memory.models.memory_source import MemorySource
    from src.memory.models.raw_event import (
        ProcessingStatus,
        RawEvent,
        SensitivityLevel,
        SourceType,
        VisibilityScope,
    )

    monkeypatch.setattr(
        conversation_deletion,
        "AgentWorkspaceService",
        lambda: AgentWorkspaceService(base_dir=tmp_path),
    )

    async def run():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            factory = async_sessionmaker(engine, expire_on_commit=False)
            async with factory() as db:
                now = datetime.now(timezone.utc)
                session = AgentSession(
                    id="session-delete",
                    user_id="u-delete",
                    agent_role=AgentRole.CONVERSATIONAL,
                    channel="wecom",
                    channel_session_key="direct:delete",
                    status=AgentSessionStatus.ACTIVE,
                    context_version="conv-ledger-v1",
                )
                turn = ConversationTurn(
                    id="turn-delete",
                    session_id=session.id,
                    user_id="u-delete",
                    channel="wecom",
                    role="user",
                    content="我计划搬家",
                    reflection_state="reflected",
                    turn_metadata={},
                )
                episode = ConversationEpisode(
                    id="episode-delete",
                    session_id=session.id,
                    user_id="u-delete",
                    start_turn_id=turn.id,
                    end_turn_id=turn.id,
                    summary="搬家计划",
                    topics=["搬家"],
                    open_loops=[],
                    asked_questions=[],
                    declined_questions=[],
                    memory_signals=[],
                    source_turn_ids=[turn.id],
                    handoff_ids=[],
                )
                cursor = ConversationReflectionCursor(
                    id="cursor-delete",
                    session_id=session.id,
                    user_id="u-delete",
                    pending_user_turns=0,
                    running=False,
                )
                attention = ConversationAttentionCandidate(
                    id="attention-delete",
                    user_id="u-delete",
                    session_id=session.id,
                    episode_id=episode.id,
                    kind="follow_up",
                    prompt="进展如何？",
                    value_score=0.9,
                    source="reflection",
                    sensitivity="normal",
                    status="pending",
                    due_at=now,
                    source_turn_ids=[turn.id],
                    candidate_metadata={},
                )
                conversation_event = RawEvent(
                    id="event-conversation",
                    source_type=SourceType.CONVERSATION,
                    source_id="conversation:delete",
                    user_id="u-delete",
                    occurred_at=now,
                    content="我计划搬家",
                    content_hash="hash-conversation",
                    event_metadata={},
                    sensitivity=SensitivityLevel.NORMAL,
                    visibility_scope=VisibilityScope.PERSONAL,
                    processing_status=ProcessingStatus.COMPLETED,
                )
                independent_event = RawEvent(
                    id="event-independent",
                    source_type=SourceType.MANUAL,
                    source_id="manual:keep",
                    user_id="u-delete",
                    occurred_at=now,
                    content="独立来源",
                    content_hash="hash-independent",
                    event_metadata={},
                    sensitivity=SensitivityLevel.NORMAL,
                    visibility_scope=VisibilityScope.PERSONAL,
                    processing_status=ProcessingStatus.COMPLETED,
                )
                full_memory = CommittedMemory(
                    id="memory-full",
                    user_id="u-delete",
                    memory_type=MemoryType.FACT,
                    title="仅对话来源",
                    body="我计划搬家",
                    status=CommittedStatus.ACTIVE,
                    valid_from=now,
                    content_hash="memory-hash-full",
                )
                mixed_memory = CommittedMemory(
                    id="memory-mixed",
                    user_id="u-delete",
                    memory_type=MemoryType.FACT,
                    title="混合来源",
                    body="有独立证据",
                    status=CommittedStatus.ACTIVE,
                    valid_from=now,
                    content_hash="memory-hash-mixed",
                )
                db.add_all(
                    [
                        session,
                        turn,
                        episode,
                        cursor,
                        attention,
                        conversation_event,
                        independent_event,
                        full_memory,
                        mixed_memory,
                        MemorySource(
                            id="source-full-conversation",
                            memory_id=full_memory.id,
                            raw_event_id=conversation_event.id,
                            source_type=SourceType.CONVERSATION,
                        ),
                        MemorySource(
                            id="source-mixed-conversation",
                            memory_id=mixed_memory.id,
                            raw_event_id=conversation_event.id,
                            source_type=SourceType.CONVERSATION,
                        ),
                        MemorySource(
                            id="source-mixed-independent",
                            memory_id=mixed_memory.id,
                            raw_event_id=independent_event.id,
                            source_type=SourceType.MANUAL,
                        ),
                    ]
                )
                await db.commit()
                AgentWorkspaceService(base_dir=tmp_path).load(
                    user_id="u-delete", agent="conversational"
                )

                result = await conversation_deletion.delete_conversation_data(
                    db,
                    user_id="u-delete",
                )
                assert result["turns"] == 1
                assert result["episodes"] == 1
                assert result["workspace_deleted"] is True
                assert await db.get(RawEvent, conversation_event.id) is None
                assert await db.get(RawEvent, independent_event.id) is not None
                deleted_memory = await db.get(CommittedMemory, full_memory.id)
                assert deleted_memory is not None
                assert deleted_memory.status is CommittedStatus.DELETED
                assert deleted_memory.body == ""
                kept_memory = await db.get(CommittedMemory, mixed_memory.id)
                assert kept_memory is not None
                assert kept_memory.status is CommittedStatus.ACTIVE
                assert await db.scalar(
                    select(func.count())
                    .select_from(MemorySource)
                    .where(MemorySource.raw_event_id == conversation_event.id)
                ) == 0
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_scheduler_contains_reflection_and_heartbeat_without_legacy_daily_question():
    from src.shared.db import scheduler

    assert hasattr(scheduler, "conversation_idle_reflection")
    assert hasattr(scheduler, "conversation_reflection_compensation")
    assert hasattr(scheduler, "conversation_heartbeat")
    assert not hasattr(scheduler, "daily_wecom_questioning")


def test_postgres_migration_handles_varchar_or_native_enum_source_type(
    monkeypatch,
):
    migration_path = (
        Path(__file__).resolve().parents[2]
        / "migrations"
        / "versions"
        / "023_add_conversation_ledger.py"
    )
    spec = importlib.util.spec_from_file_location(
        "migration_023_conversation_test",
        migration_path,
    )
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    class Result:
        def __init__(self, row):
            self.row = row

        def mappings(self):
            return self

        def first(self):
            return self.row

    class Bind:
        dialect = SimpleNamespace(name="postgresql")

        def __init__(self, row):
            self.row = row

        def execute(self, _statement):
            return Result(self.row)

    executed: list[str] = []
    monkeypatch.setattr(migration.op, "execute", executed.append)
    monkeypatch.setattr(
        migration.op,
        "get_bind",
        lambda: Bind({"typname": "varchar", "typtype": "b"}),
    )
    migration._add_conversation_source_type()
    assert executed == []

    monkeypatch.setattr(
        migration.op,
        "get_bind",
        lambda: Bind({"typname": "source_type_enum", "typtype": "e"}),
    )
    migration._add_conversation_source_type()
    assert executed == [
        'ALTER TYPE "source_type_enum" ADD VALUE IF NOT EXISTS \'conversation\''
    ]
