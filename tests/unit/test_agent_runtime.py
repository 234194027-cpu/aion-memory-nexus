import asyncio
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.execution.models.agent_runtime import AgentHandoff, AgentRole, AgentRun, AgentRunStatus, AgentSession, AgentStep
from src.execution.models.conversation import ConversationTurn
from src.execution.runtime.model import JsonCompatibilityModel, RuntimeModelResponse
from src.execution.runtime.feature_flags import is_runtime_enabled, require_runtime_enabled
from src.execution.runtime.profile import AgentProfileSpec
from src.execution.runtime.runtime import AgentRuntime
from src.execution.runtime.tools.base import RuntimeTool, ToolCall
from src.execution.runtime.tools.registry import ToolRegistry
from src.execution.runtime.tools.conversation import build_conversation_tools
from src.execution.runtime.trace import InMemoryTraceStore, SqlAlchemyTraceStore
from src.shared.db.database import Base


class ScriptedModel:
    def __init__(self, responses):
        self._responses = iter(responses)

    async def complete(self, **_kwargs):
        return next(self._responses)


class CapturingModel(ScriptedModel):
    def __init__(self, responses):
        super().__init__(responses)
        self.calls = []

    async def complete(self, **kwargs):
        self.calls.append(kwargs)
        return await super().complete(**kwargs)


def _profile(*, max_tool_calls: int = 3) -> AgentProfileSpec:
    return AgentProfileSpec(
        name="test",
        role=AgentRole.CONVERSATIONAL,
        system_prompt="fixed",
        allowed_tools=frozenset({"lookup"}),
        max_steps=4,
        max_model_calls=4,
        max_tool_calls=max_tool_calls,
        max_wall_time_seconds=10,
        max_total_tokens=1000,
        max_cost=None,
        may_reply_to_user=True,
        may_propose_memory=False,
    )


def test_runtime_executes_explicit_tool_and_persists_safe_trace():
    async def run():
        async def lookup(_user_id, params):
            return {"answer": params["query"], "sensitive": "not persisted as trace payload"}

        tool = RuntimeTool(
            "lookup",
            "lookup",
            {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
            lookup,
        )
        trace = InMemoryTraceStore()
        runtime = AgentRuntime(
            model=ScriptedModel([
                RuntimeModelResponse(tool_calls=(ToolCall("lookup", {"query": "hello"}),)),
                RuntimeModelResponse(text="done"),
            ]),
            registry=ToolRegistry([tool]),
            trace_store=trace,
        )
        result = await runtime.run(
            runtime.new_context(user_id="u1", profile=_profile()),
            ({"role": "user", "content": "hello"},),
        )
        assert result.status == AgentRunStatus.COMPLETED
        assert result.final_text == "done"
        assert len(trace.steps) == 4
        tool_trace = next(step for step in trace.steps if step.get("tool_name") == "lookup")
        assert tool_trace["arguments_hash"] != "hello"
        assert "not persisted" not in tool_trace["result_summary"]

    asyncio.run(run())


def test_runtime_blocks_tool_outside_profile_without_looping():
    trace = InMemoryTraceStore()
    runtime = AgentRuntime(
        model=ScriptedModel([
            RuntimeModelResponse(tool_calls=(ToolCall("not_registered", {}),)),
            RuntimeModelResponse(text="fallback answer"),
        ]),
        registry=ToolRegistry([]),
        trace_store=trace,
    )
    result = asyncio.run(
        runtime.run(runtime.new_context(user_id="u1", profile=_profile()), ({"role": "user", "content": "x"},))
    )
    assert result.status == AgentRunStatus.COMPLETED
    assert result.final_text == "fallback answer"
    assert any(step.get("error_code") == "tool_not_allowed" for step in trace.steps)


def test_runtime_stops_when_tool_budget_is_exhausted():
    async def run():
        async def lookup(_user_id, _params):
            return {"ok": True}

        tool = RuntimeTool("lookup", "lookup", {"type": "object"}, lookup)
        trace = InMemoryTraceStore()
        runtime = AgentRuntime(
            model=ScriptedModel([
                RuntimeModelResponse(tool_calls=(ToolCall("lookup", {}), ToolCall("lookup", {}))),
            ]),
            registry=ToolRegistry([tool]),
            trace_store=trace,
        )
        result = await runtime.run(
            runtime.new_context(user_id="u1", profile=_profile(max_tool_calls=1)),
            ({"role": "user", "content": "x"},),
        )
        assert result.status == AgentRunStatus.NEEDS_REVIEW
        assert result.error_code == "budget"

    asyncio.run(run())


def test_runtime_rejects_invalid_tool_arguments_before_handler_execution():
    async def run():
        invoked = False

        async def lookup(_user_id, _params):
            nonlocal invoked
            invoked = True
            return {"unexpected": True}

        tool = RuntimeTool(
            "lookup",
            "lookup",
            {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
            lookup,
        )
        runtime = AgentRuntime(
            model=ScriptedModel([
                RuntimeModelResponse(tool_calls=(ToolCall("lookup", {"query": 1}),)),
                RuntimeModelResponse(text="safe fallback"),
            ]),
            registry=ToolRegistry([tool]),
            trace_store=InMemoryTraceStore(),
        )
        result = await runtime.run(
            runtime.new_context(user_id="u1", profile=_profile()),
            ({"role": "user", "content": "x"},),
        )
        assert result.status == AgentRunStatus.COMPLETED
        assert invoked is False

    asyncio.run(run())


def test_runtime_returns_stable_timeout_without_exposing_tool_payload():
    async def run():
        async def lookup(_user_id, _params):
            await asyncio.sleep(0.05)
            return {"secret": "must not be returned"}

        tool = RuntimeTool("lookup", "lookup", {"type": "object"}, lookup, timeout_seconds=0.001)
        trace = InMemoryTraceStore()
        runtime = AgentRuntime(
            model=ScriptedModel([
                RuntimeModelResponse(tool_calls=(ToolCall("lookup", {}),)),
                RuntimeModelResponse(text="timeout fallback"),
            ]),
            registry=ToolRegistry([tool]),
            trace_store=trace,
        )
        result = await runtime.run(
            runtime.new_context(user_id="u1", profile=_profile()),
            ({"role": "user", "content": "x"},),
        )
        assert result.status == AgentRunStatus.COMPLETED
        timeout_step = next(step for step in trace.steps if step.get("error_code") == "tool_timeout")
        assert "secret" not in timeout_step["result_summary"]

    asyncio.run(run())


def test_runtime_persists_session_run_and_step_without_hidden_reasoning():
    async def run():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            session_factory = async_sessionmaker(engine, expire_on_commit=False)
            async with session_factory() as session:
                runtime = AgentRuntime(
                    model=ScriptedModel([RuntimeModelResponse(text="final response")]),
                    registry=ToolRegistry(),
                    trace_store=SqlAlchemyTraceStore(session),
                )
                context = runtime.new_context(user_id="u1", profile=_profile(), goal="short objective")
                result = await runtime.run(context, ({"role": "user", "content": "private content"},))
                await session.commit()
                assert result.status == AgentRunStatus.COMPLETED
                saved_session = (await session.execute(select(AgentSession))).scalar_one()
                saved_run = (await session.execute(select(AgentRun))).scalar_one()
                saved_steps = list((await session.execute(select(AgentStep).order_by(AgentStep.step_no))).scalars())
                assert saved_session.id == context.session_id
                assert saved_run.id == context.run_id
                assert saved_run.status == AgentRunStatus.COMPLETED
                assert len(saved_steps) == 2
                assert all("private content" not in (step.result_summary or "") for step in saved_steps)
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_runtime_feature_flags_are_deny_by_default(monkeypatch):
    from src.shared.config import settings

    monkeypatch.setattr(settings, "AGENT_RUNTIME_ENABLED", False)
    monkeypatch.setattr(settings, "CONVERSATIONAL_AGENT_ENABLED", True)
    assert not is_runtime_enabled(AgentRole.CONVERSATIONAL)
    try:
        require_runtime_enabled(AgentRole.CONVERSATIONAL)
    except Exception as exc:
        assert getattr(exc, "error_class").value == "policy"
    else:
        raise AssertionError("disabled runtime must be rejected")


def test_json_compatibility_adapter_accepts_only_strict_tool_protocol():
    class Provider:
        async def generate(self, *_args, **_kwargs):
            return '{"tool_calls":[{"name":"lookup","arguments":{"query":"x"}}]}'

    async def lookup(_user_id, _params):
        return {"ok": True}

    tool = RuntimeTool("lookup", "lookup", {"type": "object"}, lookup)
    response = asyncio.run(
        JsonCompatibilityModel(Provider()).complete(
            system_prompt="fixed",
            messages=({"role": "user", "content": "x"},),
            tools=ToolRegistry([tool]).snapshot_for(_profile()),
        )
    )
    assert response.tool_calls == (ToolCall("lookup", {"query": "x"}),)


def test_runtime_keeps_only_citations_returned_by_a_tool():
    async def run():
        async def lookup(_user_id, _params):
            return {"relevant_memories": [{"memory_id": "mem-1"}]}

        runtime = AgentRuntime(
            model=ScriptedModel([
                RuntimeModelResponse(tool_calls=(ToolCall("lookup", {}),)),
                RuntimeModelResponse(text="answer", citations=("mem-1", "invented"), response_mode="ANSWER", confidence="HIGH"),
            ]),
            registry=ToolRegistry([RuntimeTool("lookup", "lookup", {"type": "object"}, lookup)]),
            trace_store=InMemoryTraceStore(),
        )
        result = await runtime.run(
            runtime.new_context(user_id="u1", profile=_profile()),
            ({"role": "user", "content": "x"},),
        )
        assert result.citations == ("mem-1",)
        assert result.response_mode == "ANSWER"
        assert result.confidence == "HIGH"

    asyncio.run(run())


def test_conversation_citation_evidence_is_user_scoped_and_traceable():
    from datetime import datetime, timezone

    from src.execution.runtime.citation_evidence import resolve_citation_evidence
    from src.memory.models.committed_memory import CommittedMemory
    from src.memory.models.memory_source import MemorySource
    from src.memory.models.memory_type import MemoryType
    from src.memory.models.raw_event import EpistemicStatus, SensitivityLevel, SourceType, VisibilityScope

    async def run():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            session_factory = async_sessionmaker(engine, expire_on_commit=False)
            async with session_factory() as db:
                db.add_all([
                    CommittedMemory(
                        id="mem-visible", user_id="u1", memory_type=MemoryType.FACT,
                        title="搬家计划", body="用户计划搬去杭州", confidence=0.8, importance=0.8,
                        sensitivity=SensitivityLevel.NORMAL, epistemic_status=EpistemicStatus.USER_ASSERTION.value,
                        visibility_scope=VisibilityScope.PERSONAL, status="active", valid_from=datetime(2026, 7, 1, tzinfo=timezone.utc),
                    ),
                    CommittedMemory(
                        id="mem-other-user", user_id="u2", memory_type=MemoryType.FACT,
                        title="不应泄露", body="private", confidence=0.8, importance=0.8,
                        sensitivity=SensitivityLevel.NORMAL, epistemic_status=EpistemicStatus.USER_ASSERTION.value,
                        visibility_scope=VisibilityScope.PERSONAL, status="active", valid_from=datetime.now(timezone.utc),
                    ),
                ])
                db.add(MemorySource(
                    id="source-visible", memory_id="mem-visible", raw_event_id="evt-visible", source_type=SourceType.MANUAL,
                ))
                await db.commit()

                evidence = await resolve_citation_evidence(db, user_id="u1", citation_ids=("mem-other-user", "mem-visible", "invented"))

                assert len(evidence) == 1
                assert evidence[0].memory_id == "mem-visible"
                assert evidence[0].source_event_ids == ("evt-visible",)
                assert evidence[0].epistemic_status == EpistemicStatus.USER_ASSERTION.value
                assert evidence[0].valid_from == datetime(2026, 7, 1, tzinfo=timezone.utc)
                assert evidence[0].valid_until is None
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_conversational_turn_refuses_memory_answer_without_retrieval_evidence(monkeypatch, tmp_path):
    from types import SimpleNamespace

    from src.execution.models.agent_runtime import AgentRunStatus
    from src.execution.runtime import conversation_agent
    from src.execution.runtime.runtime import RuntimeResult
    from src.execution.runtime.workspace import AgentWorkspaceService
    from src.shared.config import settings

    class EmptyEvidenceRuntime:
        context_kwargs = None

        def new_context(self, **_kwargs):
            self.context_kwargs = _kwargs
            return SimpleNamespace()

        async def run(self, *_args, **_kwargs):
            return RuntimeResult(
                status=AgentRunStatus.COMPLETED,
                final_text="你去年十月已经决定搬家。",
                error_code=None,
                run_id="run-empty-evidence",
                response_mode="ANSWER",
                confidence="HIGH",
                citations=(),
                memory_retrieval_attempted=True,
            )

    async def run():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            session_factory = async_sessionmaker(engine, expire_on_commit=False)
            async with session_factory() as db:
                monkeypatch.setattr(settings, "AGENT_RUNTIME_ENABLED", True)
                monkeypatch.setattr(settings, "CONVERSATIONAL_AGENT_ENABLED", True)
                runtime = EmptyEvidenceRuntime()
                monkeypatch.setattr(conversation_agent, "build_conversational_runtime", lambda *_args, **_kwargs: runtime)
                monkeypatch.setattr(
                    conversation_agent,
                    "AgentWorkspaceService",
                    lambda: AgentWorkspaceService(base_dir=tmp_path),
                )
                answer = await conversation_agent.run_conversational_turn(
                    db, user_id="u1", channel="web", channel_session_key="empty-evidence", message="我什么时候决定搬家？"
                )
                assert answer is not None
                assert answer.response_mode == "SAFE_REFUSAL"
                assert answer.confidence == "LOW"
                assert "没有找到可核验的记忆依据" in answer.text
                assert answer.citations == ()
                assert "AGENT WORKSPACE CONTEXT" in runtime.context_kwargs["profile"].system_prompt
                assert "CONVERSATION LEDGER CONTEXT" in runtime.context_kwargs["profile"].system_prompt
                assert runtime.context_kwargs["context_version"] == "conv-shared-cognition-v1"
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_conversation_ledger_is_durable_bounded_in_context_and_resettable():
    async def run():
        from sqlalchemy import func

        from src.execution.models.conversation import ConversationTurn
        from src.execution.runtime.conversation_ledger import ConversationLedger

        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            session_factory = async_sessionmaker(engine, expire_on_commit=False)
            async with session_factory() as db:
                ledger = ConversationLedger(db)
                session = await ledger.get_or_create_session(
                    user_id="u1", channel="wecom", channel_session_key="direct:wx-1"
                )
                for index in range(30):
                    await ledger.append_user_turn(
                        session=session,
                        content=f"message {index}",
                        message_id=f"message-id-{index}",
                    )
                await db.commit()
                assert len(await ledger.recent_messages(session_id=session.id)) == 24
                assert await db.scalar(
                    select(func.count()).select_from(ConversationTurn)
                ) == 30
                assert session.context_payload is None
                await ledger.reset_session(
                    user_id="u1",
                    channel="wecom",
                    channel_session_key="direct:wx-1",
                )
                await db.commit()
                assert session.status.value == "cancelled"
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_working_shadow_persists_evidence_and_never_writes_formal_memory(monkeypatch):
    from src.execution.runtime.working_agent import run_working_shadow
    from src.memory.models.committed_memory import CommittedMemory
    from src.shared.config import settings

    async def run():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            session_factory = async_sessionmaker(engine, expire_on_commit=False)
            async with session_factory() as db:
                monkeypatch.setattr(settings, "AGENT_RUNTIME_ENABLED", True)
                monkeypatch.setattr(settings, "WORKING_AGENT_SHADOW_ENABLED", True)
                result = await run_working_shadow(
                    db,
                    raw_event={"id": "evt-1", "user_id": "u1", "content": "我可能要换城市", "metadata": {}},
                    model=ScriptedModel([
                        RuntimeModelResponse(text='{"business_state":"NEEDS_MORE_EVIDENCE","memories":[],"question":"你计划何时搬去哪个城市？"}')
                    ]),
                )
                await db.commit()
                assert result is not None
                assert result.state.value == "NEEDS_MORE_EVIDENCE"
                run_row = (await db.execute(select(AgentRun))).scalar_one()
                handoff = (await db.execute(select(AgentHandoff))).scalar_one()
                assert run_row.evidence_payload["mode"] == "shadow"
                assert handoff.mode == "shadow"
                assert (await db.execute(select(CommittedMemory))).scalars().all() == []
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_working_active_is_single_formal_memory_writer_and_idempotent(monkeypatch):
    from src.execution.runtime.working_agent import run_working_active
    from src.memory.models.committed_memory import CommittedMemory
    from src.memory.models.raw_event import ProcessingStatus, RawEvent, SourceType
    from src.shared.utils.hash import compute_content_hash
    from src.shared.config import settings

    final = '{"business_state":"MEMORY_READY","memories":[{"memory_type":"fact","title":"搬家计划","content":"我计划搬去杭州","importance":0.8,"confidence":0.7,"sensitivity":"normal","entities":["杭州"]}]}'

    async def run():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            session_factory = async_sessionmaker(engine, expire_on_commit=False)
            async with session_factory() as db:
                monkeypatch.setattr(settings, "AGENT_RUNTIME_ENABLED", True)
                monkeypatch.setattr(settings, "WORKING_AGENT_ACTIVE_ENABLED", True)
                event = RawEvent(
                    id="evt-active", user_id="u1", source_type=SourceType.MANUAL,
                    occurred_at=datetime.now(timezone.utc), content="我准备搬去杭州",
                    content_hash=compute_content_hash("我准备搬去杭州"),
                    processing_status=ProcessingStatus.PROCESSING,
                )
                db.add(event)
                await db.commit()
                raw_event = {"id": event.id, "user_id": event.user_id, "content": event.content, "source_type": event.source_type, "metadata": {}}
                first = await run_working_active(db, raw_event=raw_event, model=ScriptedModel([RuntimeModelResponse(text=final)]))
                await db.commit()
                second = await run_working_active(db, raw_event=raw_event, model=ScriptedModel([RuntimeModelResponse(text=final)]))
                await db.commit()
                memories = list((await db.execute(select(CommittedMemory))).scalars())
                assert first is not None and second is not None
                assert len(memories) == 1
                assert memories[0].origin_kind == "working_agent"
                assert first.memory_ids == second.memory_ids
                db.add(AgentHandoff(id="handoff-active", user_id="u1", source_run_id=first.run_id, handoff_type="needs_more_evidence", mode="active", priority=1, question="补充时间", status="active"))
                await db.commit()
                await run_working_active(
                    db,
                    raw_event={"id": "evt-active-reply", "user_id": "u1", "content": "我明天搬去杭州", "metadata": {"handoff_id": "handoff-active"}},
                    model=ScriptedModel([RuntimeModelResponse(text=final)]),
                )
                await db.commit()
                handoff = await db.get(AgentHandoff, "handoff-active")
                assert handoff.status.value == "resolved"
                assert handoff.resolved_by_event_id == "evt-active-reply"
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_working_active_reprocesses_handoff_with_source_evidence(monkeypatch):
    """A reply must retain the original evidence, not only the reply text."""
    from datetime import datetime, timezone

    from src.execution.runtime.working_agent import run_working_active
    from src.memory.models.committed_memory import CommittedMemory
    from src.memory.models.memory_source import MemorySource
    from src.memory.models.raw_event import (
        ProcessingStatus,
        RawEvent,
        SensitivityLevel,
        SourceType,
        VisibilityScope,
    )
    from src.shared.config import settings
    from src.shared.utils.hash import compute_content_hash

    final = '{"business_state":"MEMORY_READY","memories":[{"memory_type":"fact","title":"搬家计划","content":"我明天搬去杭州","importance":0.8,"confidence":0.7,"sensitivity":"normal","entities":["杭州"]}]}'

    async def run():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            session_factory = async_sessionmaker(engine, expire_on_commit=False)
            async with session_factory() as db:
                monkeypatch.setattr(settings, "AGENT_RUNTIME_ENABLED", True)
                monkeypatch.setattr(settings, "WORKING_AGENT_ACTIVE_ENABLED", True)
                db.add(RawEvent(
                    id="evt-source", user_id="u1", source_type=SourceType.MANUAL,
                    source_id="wecom", occurred_at=datetime.now(timezone.utc),
                    content="我准备搬家，但时间还没确定", content_hash=compute_content_hash("source"),
                    sensitivity=SensitivityLevel.NORMAL, visibility_scope=VisibilityScope.PERSONAL,
                    processing_status=ProcessingStatus.COMPLETED,
                ))
                db.add(AgentSession(id="session-source", user_id="u1", agent_role=AgentRole.WORKING, channel="system"))
                db.add(AgentRun(id="run-source", session_id="session-source", user_id="u1", trigger_type="raw_event", status=AgentRunStatus.COMPLETED))
                db.add(AgentHandoff(
                    id="handoff-source", user_id="u1", source_run_id="run-source",
                    source_event_id="evt-source", handoff_type="needs_more_evidence", mode="active",
                    priority=1, question="你准备什么时候搬家？", status="active",
                ))
                db.add(RawEvent(
                    id="evt-reply", user_id="u1", source_type=SourceType.MANUAL,
                    source_id="wecom", occurred_at=datetime.now(timezone.utc),
                    content="我明天搬去杭州", content_hash=compute_content_hash("reply"),
                    sensitivity=SensitivityLevel.NORMAL, visibility_scope=VisibilityScope.PERSONAL,
                    processing_status=ProcessingStatus.PROCESSING,
                ))
                await db.commit()

                model = CapturingModel([RuntimeModelResponse(text=final)])
                result = await run_working_active(
                    db,
                    raw_event={
                        "id": "evt-reply", "user_id": "u1", "content": "我明天搬去杭州",
                        "metadata": {"handoff_id": "handoff-source", "runtime_handoff_response": True},
                    },
                    model=model,
                )
                await db.commit()

                prompt = model.calls[0]["messages"][0]["content"]
                assert "evt-source" in prompt
                assert "我准备搬家，但时间还没确定" in prompt
                assert "你准备什么时候搬家？" in prompt
                memories = list((await db.execute(select(CommittedMemory))).scalars())
                assert result is not None
                sources = list((await db.execute(select(MemorySource))).scalars())
                assert len(memories) == 1
                assert {item.raw_event_id for item in sources} == {"evt-source", "evt-reply"}
                handoff = await db.get(AgentHandoff, "handoff-source")
                assert handoff.status.value == "resolved"
                assert handoff.resolved_by_event_id == "evt-reply"
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_working_active_discards_handoff_reply_and_closes_the_handoff(monkeypatch):
    from src.execution.runtime.working_agent import run_working_active
    from src.shared.config import settings

    async def run():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            session_factory = async_sessionmaker(engine, expire_on_commit=False)
            async with session_factory() as db:
                monkeypatch.setattr(settings, "AGENT_RUNTIME_ENABLED", True)
                monkeypatch.setattr(settings, "WORKING_AGENT_ACTIVE_ENABLED", True)
                db.add(AgentSession(id="session-discard", user_id="u1", agent_role=AgentRole.WORKING, channel="system"))
                db.add(AgentRun(id="run-discard", session_id="session-discard", user_id="u1", trigger_type="raw_event", status=AgentRunStatus.COMPLETED))
                db.add(AgentHandoff(
                    id="handoff-discard", user_id="u1", source_run_id="run-discard",
                    handoff_type="needs_more_evidence", mode="active", priority=1,
                    question="请补充背景", status="active",
                ))
                await db.commit()

                result = await run_working_active(
                    db,
                    raw_event={
                        "id": "evt-discard", "user_id": "u1", "content": "不用记录",
                        "metadata": {"handoff_id": "handoff-discard"},
                    },
                    model=ScriptedModel([RuntimeModelResponse(text='{"business_state":"DISCARDED","candidates":[]}')]),
                )
                await db.commit()

                handoff = await db.get(AgentHandoff, "handoff-discard")
                assert result is not None
                assert result.state.value == "DISCARDED"
                assert handoff.status.value == "resolved"
                assert handoff.resolved_by_event_id == "evt-discard"
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_shadow_report_contains_only_aggregate_counts():
    from src.execution.runtime.shadow_report import build_shadow_report

    async def run():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            session_factory = async_sessionmaker(engine, expire_on_commit=False)
            async with session_factory() as db:
                session = AgentSession(id="session-1", user_id="u1", agent_role=AgentRole.WORKING, channel="system")
                db.add(session)
                db.add(AgentRun(id="run-1", session_id="session-1", user_id="u1", trigger_type="raw_event", status=AgentRunStatus.COMPLETED, evidence_payload={"mode": "shadow", "business_state": "MEMORY_READY", "source_event_id": "evt-1", "memory_proposals": [{"title": "private title"}]}))
                await db.commit()
                report = await build_shadow_report(db, user_id="u1")
                assert report["total_shadow_runs"] == 1
                assert report["business_state_counts"] == {"MEMORY_READY": 1}
                assert report["shadow_failed_run_count"] == 0
                assert report["shadow_conflict_run_count"] == 0
                assert report["shadow_memory_proposal_count"] == 1
                assert report["formal_memory_count_for_compared_events"] == 0
                assert report["duplicate_metric_available"] is False
                assert "private title" not in str(report)
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_shadow_report_aggregates_governance_metrics_without_content():
    from src.execution.runtime.shadow_report import build_shadow_report

    async def run():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            session_factory = async_sessionmaker(engine, expire_on_commit=False)
            async with session_factory() as db:
                db.add(AgentSession(id="shadow-session", user_id="u1", agent_role=AgentRole.WORKING, channel="system"))
                db.add_all([
                    AgentRun(
                        id="shadow-complete", session_id="shadow-session", user_id="u1", trigger_type="raw_event",
                        status=AgentRunStatus.COMPLETED,
                        evidence_payload={"mode": "shadow", "business_state": "CONFLICT_REVIEW", "source_event_id": "evt-shadow", "memory_proposals": []},
                    ),
                    AgentRun(
                        id="shadow-failed", session_id="shadow-session", user_id="u1", trigger_type="raw_event",
                        status=AgentRunStatus.FAILED,
                        evidence_payload={"mode": "shadow", "business_state": "UNKNOWN", "source_event_id": "evt-failed", "memory_proposals": []},
                    ),
                ])
                await db.commit()

                report = await build_shadow_report(db, user_id="u1")

                assert report["shadow_failed_run_count"] == 1
                assert report["shadow_conflict_run_count"] == 1
                assert report["governed_decision_count_for_compared_events"] == 0
                assert report["duplicate_metric_available"] is False
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_attention_service_never_consumes_shadow_handoffs():
    from src.execution.models.agent_runtime import AgentHandoffStatus
    from src.platform.services.attention_service import AttentionService

    async def run():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            session_factory = async_sessionmaker(engine, expire_on_commit=False)
            async with session_factory() as db:
                db.add(AgentSession(id="session-a", user_id="u1", agent_role=AgentRole.WORKING, channel="system"))
                db.add(AgentRun(id="run-a", session_id="session-a", user_id="u1", trigger_type="raw_event", status=AgentRunStatus.COMPLETED))
                db.add(AgentHandoff(id="handoff-shadow", user_id="u1", source_run_id="run-a", handoff_type="needs_more_evidence", mode="shadow", priority=9, question="private shadow question", status=AgentHandoffStatus.ACTIVE))
                await db.commit()
                assert await AttentionService(db).list_candidates(user_id="u1") == []
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_attention_service_expires_or_skips_non_active_handoffs():
    from datetime import datetime, timedelta, timezone

    from src.execution.models.agent_runtime import AgentHandoffStatus, AgentRole
    from src.platform.services.attention_service import AttentionService

    async def run():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            session_factory = async_sessionmaker(engine, expire_on_commit=False)
            async with session_factory() as db:
                db.add(AgentSession(id="expiry-session", user_id="u1", agent_role=AgentRole.WORKING, channel="system"))
                db.add(AgentRun(id="expiry-run", session_id="expiry-session", user_id="u1", trigger_type="raw_event", status=AgentRunStatus.COMPLETED))
                db.add_all([
                    AgentHandoff(
                        id="expired-handoff", user_id="u1", source_run_id="expiry-run", handoff_type="needs_more_evidence",
                        mode="active", priority=9, question="must not repeat", status=AgentHandoffStatus.ACTIVE,
                        expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
                    ),
                    AgentHandoff(
                        id="cancelled-handoff", user_id="u1", source_run_id="expiry-run", handoff_type="needs_more_evidence",
                        mode="active", priority=8, question="must not repeat", status=AgentHandoffStatus.CANCELLED,
                        expires_at=datetime.now(timezone.utc) + timedelta(days=1),
                    ),
                ])
                await db.commit()

                assert await AttentionService(db).list_candidates(user_id="u1") == []
                expired = await db.get(AgentHandoff, "expired-handoff")
                assert expired.status == AgentHandoffStatus.EXPIRED
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_conversation_tool_factory_exposes_only_white_listed_read_tools():
    tools = build_conversation_tools(db=object())
    assert {tool.name for tool in tools} == {
        "retrieve_memories",
        "get_persona",
        "get_conflicts",
        "get_tasks",
        "get_timeline",
        "get_attention",
        "search_source_documents",
        "get_unconfirmed_memory_clues",
    }
    assert all(tool.read_only for tool in tools)


def test_runtime_status_is_observable_while_execution_remains_disabled(monkeypatch):
    from src.execution.api.runtime import runtime_status
    from src.shared.config import settings

    monkeypatch.setattr(settings, "AGENT_RUNTIME_ENABLED", False)
    monkeypatch.setattr(settings, "CONVERSATIONAL_AGENT_ENABLED", False)
    monkeypatch.setattr(settings, "WORKING_AGENT_SHADOW_ENABLED", False)
    monkeypatch.setattr(settings, "WORKING_AGENT_ACTIVE_ENABLED", False)
    status = asyncio.run(runtime_status())
    assert status.runtime_enabled is False
    assert status.profiles == ["disabled"]


def test_conversation_turn_schema_rejects_blank_message():
    from pydantic import ValidationError
    from src.execution.schemas.runtime import ConversationTurnRequest

    try:
        ConversationTurnRequest(message="", session_key="web-1")
    except ValidationError:
        return
    raise AssertionError("blank conversation messages must be rejected")


def test_prompt_and_skill_registries_are_versioned_and_disabled_by_default():
    from src.execution.runtime.prompt_registry import get_prompt
    from src.execution.runtime.skills import approved_skills_for, list_skills

    prompt = get_prompt("conversational-agent-core")
    assert prompt.version == "v5"
    assert prompt.evaluation_suite == "conversation-runtime-v5"
    assert "问候、测试" in prompt.text
    working_prompt = get_prompt("working-agent-core")
    assert working_prompt.version == "v6"
    assert working_prompt.evaluation_suite == "working-runtime-v6"
    assert "绝不直接回复用户" in working_prompt.text
    assert list_skills()["deep-interview"].approved is False
    assert approved_skills_for("conversational") == ()


def test_retrieval_plan_is_bounded_and_deterministic():
    from src.memory.services.retrieval_plan import RetrievalIntent, build_retrieval_plan

    task_plan = build_retrieval_plan("我的待办下一步是什么？", requested_top_k=200)
    reason_plan = build_retrieval_plan("我以前为什么选择 SQLite？")
    assert task_plan.intent == RetrievalIntent.TASK
    assert task_plan.recall_level == "task_only"
    assert task_plan.top_k == 20
    assert reason_plan.intent == RetrievalIntent.REASON


def test_model_gateway_classifies_timeout_without_prompt_leakage():
    from src.shared.errors.error_classification import ClassifiedError, ErrorClass
    from src.shared.llm.model_gateway import ModelGateway

    class SlowProvider:
        async def generate(self, *_args, **_kwargs):
            await asyncio.sleep(0.05)
            return "never"

    async def run():
        try:
            await ModelGateway(SlowProvider(), timeout_seconds=0.001).generate(
                "private prompt", model_name=None, temperature=0.1, max_tokens=10
            )
        except ClassifiedError as exc:
            assert exc.error_class == ErrorClass.TIMEOUT
            assert "private prompt" not in str(exc)
            return
        raise AssertionError("model timeout must be classified")

    asyncio.run(run())


def test_model_gateway_supports_keyword_only_legacy_provider():
    from src.shared.llm.model_gateway import ModelGateway

    class KeywordOnlyProvider:
        async def generate(self, **kwargs):
            assert kwargs == {
                "prompt": "legacy question",
                "model_name": "legacy-model",
                "temperature": 0.2,
                "max_tokens": 12,
            }
            return "legacy answer"

    async def run():
        answer = await ModelGateway(KeywordOnlyProvider()).generate_text(
            "legacy question",
            model_name="legacy-model",
            temperature=0.2,
            max_tokens=12,
        )
        assert answer == "legacy answer"

    asyncio.run(run())


def test_model_gateway_retries_transient_provider_failure():
    from src.shared.llm.model_gateway import ModelGateway

    class FlakyProvider:
        calls = 0

        async def generate(self, _prompt, **_kwargs):
            self.calls += 1
            if self.calls == 1:
                raise ConnectionError("temporary provider failure")
            return "recovered"

    async def run():
        provider = FlakyProvider()
        answer = await ModelGateway(
            provider, max_retries=1, retry_initial_delay=0
        ).generate_text("retry safely")
        assert answer == "recovered"
        assert provider.calls == 2

    asyncio.run(run())


def test_model_gateway_classifies_http_provider_error():
    import httpx

    from src.shared.errors.error_classification import ClassifiedError, ErrorClass
    from src.shared.llm.model_gateway import ModelGateway

    class FailedProvider:
        async def generate(self, _prompt, **_kwargs):
            raise httpx.HTTPStatusError(
                "upstream failure",
                request=httpx.Request("POST", "https://provider.invalid"),
                response=httpx.Response(400),
            )

    async def run():
        try:
            await ModelGateway(FailedProvider(), max_retries=0).generate_text("safe prompt")
        except ClassifiedError as exc:
            assert exc.error_class == ErrorClass.PROVIDER
            assert "safe prompt" not in str(exc)
            return
        raise AssertionError("provider error must be classified")

    asyncio.run(run())


def test_text_model_calls_do_not_bypass_model_gateway():
    """Keep the strangler boundary from silently regressing in legacy services."""
    source_root = Path(__file__).resolve().parents[2] / "src"
    allowed = {
        source_root / "shared" / "llm" / "providers.py",
        source_root / "shared" / "llm" / "model_gateway.py",
    }
    bypasses = []
    for path in source_root.rglob("*.py"):
        if path in allowed:
            continue
        source = path.read_text(encoding="utf-8")
        if "provider.generate(" in source or "get_llm_provider().generate(" in source:
            bypasses.append(path.relative_to(source_root).as_posix())
    assert bypasses == []


def test_model_router_escalates_high_impact_work_without_changing_safety():
    from src.execution.runtime.model_routing import ModelTier, route_model

    assert route_model(role="working", conflict=True).tier == ModelTier.STRONG_REVIEW
    assert route_model(role="working").requires_structured_output is True
    assert route_model(role="conversational", structured_output=True).tier == ModelTier.PRIMARY


def test_windows_psycopg_launcher_configures_selector_before_server(monkeypatch):
    from src.app import windows_compat

    applied = []

    class Policy:
        pass

    monkeypatch.setattr(windows_compat.sys, "platform", "win32")
    monkeypatch.setattr(
        windows_compat.asyncio,
        "WindowsSelectorEventLoopPolicy",
        Policy,
        raising=False,
    )
    monkeypatch.setattr(windows_compat.asyncio, "set_event_loop_policy", applied.append)
    assert windows_compat.configure_windows_psycopg_event_loop() is True
    assert len(applied) == 1
    assert isinstance(applied[0], Policy)


def test_runtime_metrics_are_aggregate_only():
    from src.execution.runtime.metrics import build_runtime_metrics

    async def run():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            session_factory = async_sessionmaker(engine, expire_on_commit=False)
            async with session_factory() as db:
                db.add(AgentSession(id="metrics-session", user_id="u1", agent_role=AgentRole.CONVERSATIONAL, channel="web"))
                db.add(AgentRun(id="metrics-run", session_id="metrics-session", user_id="u1", trigger_type="user_message", status=AgentRunStatus.COMPLETED, input_tokens=3, output_tokens=4, cost=0.1))
                db.add(AgentStep(id="metrics-step", run_id="metrics-run", step_no=1, step_type="tool", tool_name="lookup", status="blocked", duration_ms=12, result_summary="no raw data"))
                await db.commit()
                report = await build_runtime_metrics(db, user_id="u1")
                assert report["run_count"] == 1
                assert report["blocked_tool_call_count"] == 1
                assert "no raw data" not in str(report)
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_open_loops_hide_shadow_handoffs():
    from src.cognition.services.open_loops import OpenLoopService

    async def run():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            session_factory = async_sessionmaker(engine, expire_on_commit=False)
            async with session_factory() as db:
                db.add(AgentSession(id="open-loop-session", user_id="u1", agent_role=AgentRole.WORKING, channel="system"))
                db.add(AgentRun(id="open-loop-run", session_id="open-loop-session", user_id="u1", trigger_type="raw_event", status=AgentRunStatus.COMPLETED))
                db.add(AgentHandoff(id="shadow-loop", user_id="u1", source_run_id="open-loop-run", handoff_type="needs_more_evidence", mode="shadow", priority=9, question="must hide", status="active"))
                db.add(AgentHandoff(id="active-loop", user_id="u1", source_run_id="open-loop-run", handoff_type="needs_more_evidence", mode="active", priority=2, question="show", status="active"))
                await db.commit()
                loops = await OpenLoopService(db).list(user_id="u1")
                assert [(item.source_type, item.source_id) for item in loops] == [("handoff", "active-loop")]
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_reflection_proposal_stays_non_factual_and_preserves_user_decision():
    from datetime import datetime, timezone

    from src.cognition.models.insight_proposal import InsightProposal
    from src.cognition.services.reflection import ReflectionService
    from src.memory.models.memory_type import MemoryType
    from src.memory.models.committed_memory import CommittedMemory, CommittedStatus

    async def run():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            session_factory = async_sessionmaker(engine, expire_on_commit=False)
            async with session_factory() as db:
                for index in range(2):
                    db.add(CommittedMemory(id=f"mem-insight-{index}", user_id="u1", memory_type=MemoryType.FACT, title="t", body="b", confidence=0.8, importance=0.7, sensitivity="normal", visibility_scope="personal", status=CommittedStatus.ACTIVE, valid_from=datetime.now(timezone.utc), tags=["运动"]))
                await db.commit()
                proposals = await ReflectionService(db).refresh(user_id="u1")
                proposal = proposals[0]
                assert proposal.status == "proposed"
                assert proposal.support_memory_ids == ["mem-insight-0", "mem-insight-1"]
                proposal.status = "ignored"
                await db.commit()
                await ReflectionService(db).refresh(user_id="u1")
                reloaded = (await db.execute(select(InsightProposal))).scalar_one()
                assert reloaded.status == "ignored"
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_reflection_feedback_is_audited_without_promoting_a_memory():
    from src.cognition.models.insight_proposal import InsightProposal
    from src.cognition.services.reflection import ReflectionService
    from src.execution.models.audit_log import AuditLog
    from src.memory.models.committed_memory import CommittedMemory

    async def run():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            session_factory = async_sessionmaker(engine, expire_on_commit=False)
            async with session_factory() as db:
                proposal = InsightProposal(
                    id="ins-feedback",
                    user_id="u1",
                    source_key="test-feedback",
                    title="test",
                    summary="derived only",
                    support_memory_ids=[],
                    counter_memory_ids=[],
                    confidence=0.2,
                    invalidation_condition="user disagrees",
                )
                db.add(proposal)
                await db.commit()
                await ReflectionService(db).record_feedback(
                    proposal=proposal, user_id="u1", status="corrected"
                )
                await db.commit()
                assert proposal.status == "corrected"
                audit = (await db.execute(select(AuditLog))).scalar_one()
                assert audit.action == "insight_feedback"
                assert audit.detail == '{"status":"corrected","learning":"offline_review_only"}'
                assert (await db.execute(select(CommittedMemory))).scalars().all() == []
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_offline_learning_review_requires_human_approval_and_never_mutates_runtime():
    from src.execution.runtime.learning_review import (
        build_learning_review,
        validate_learning_release,
    )

    review = build_learning_review(
        feedback_counts={"accepted": 3, "corrected": 1, "ignored": 0, "closed": 0},
        baseline_metrics={"citation_precision": 0.80, "safe_abstention": 0.90},
        candidate_metrics={"citation_precision": 0.82, "safe_abstention": 0.90},
    )
    assert review["contains_user_content"] is False
    assert review["change_mode"] == "offline_review_only"
    assert validate_learning_release(review, reviewer="", decision="approved")["approved"] is False
    accepted = validate_learning_release(review, reviewer="human-reviewer", decision="approved")
    assert accepted["approved"] is True
    assert accepted["release_action"] == "manual_code_review_required"
    regressed = build_learning_review(
        feedback_counts={},
        baseline_metrics={"citation_precision": 0.80},
        candidate_metrics={"citation_precision": 0.79},
    )
    assert validate_learning_release(regressed, reviewer="human-reviewer", decision="approved")["approved"] is False


def test_offline_learning_review_aggregates_audit_statuses_without_user_text():
    from src.execution.runtime.learning_review import build_learning_review_from_audit_logs
    from src.execution.models.audit_log import AuditLog

    async def run():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            session_factory = async_sessionmaker(engine, expire_on_commit=False)
            async with session_factory() as db:
                db.add_all([
                    AuditLog(id="audit-accept", user_id="u1", action="insight_feedback", detail='{"status":"accepted","learning":"offline_review_only"}'),
                    AuditLog(id="audit-correct", user_id="u1", action="insight_feedback", detail='{"status":"corrected","learning":"offline_review_only"}'),
                    AuditLog(id="audit-irrelevant", user_id="u1", action="other", detail='{"status":"accepted"}'),
                ])
                await db.commit()
                review = await build_learning_review_from_audit_logs(
                    db, user_id="u1", baseline_metrics={"citation_precision": 0.8}, candidate_metrics={"citation_precision": 0.8}
                )
                assert review["feedback_counts"] == {"accepted": 1, "corrected": 1, "ignored": 0, "closed": 0}
                assert "u1" not in str(review)
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_runtime_permission_denial_never_invokes_handler():
    class DenyAllPermissions:
        async def check(self, *_args, **_kwargs):
            return {"allowed": False, "source": "default_deny"}

    async def run():
        invoked = False

        async def lookup(_user_id, _params):
            nonlocal invoked
            invoked = True
            return {"unexpected": True}

        tool = RuntimeTool("lookup", "lookup", {"type": "object"}, lookup)
        runtime = AgentRuntime(
            model=ScriptedModel([
                RuntimeModelResponse(tool_calls=(ToolCall("lookup", {}),)),
                RuntimeModelResponse(text="permission-safe fallback"),
            ]),
            registry=ToolRegistry([tool]),
            trace_store=InMemoryTraceStore(),
            permission_service=DenyAllPermissions(),
        )
        result = await runtime.run(
            runtime.new_context(user_id="u1", agent_id="external-agent", profile=_profile()),
            ({"role": "user", "content": "x"},),
        )
        assert result.status == AgentRunStatus.COMPLETED
        assert invoked is False

    asyncio.run(run())


def test_runtime_circuit_breaker_stops_repeated_failed_call():
    async def run():
        async def lookup(_user_id, _params):
            raise RuntimeError("transient failure")

        tool = RuntimeTool("lookup", "lookup", {"type": "object"}, lookup)
        runtime = AgentRuntime(
            model=ScriptedModel([
                RuntimeModelResponse(tool_calls=(ToolCall("lookup", {}),)),
                RuntimeModelResponse(tool_calls=(ToolCall("lookup", {}),)),
            ]),
            registry=ToolRegistry([tool]),
            trace_store=InMemoryTraceStore(),
        )
        result = await runtime.run(
            runtime.new_context(user_id="u1", profile=_profile()),
            ({"role": "user", "content": "x"},),
        )
        assert result.status == AgentRunStatus.NEEDS_REVIEW
        assert result.error_code == "policy"

    asyncio.run(run())
