import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.execution.models.memory_work import MemoryWorkCase, MemoryWorkEvidence
from src.execution.runtime.model import RuntimeModelResponse
from src.execution.runtime.profile import CONVERSATIONAL_PROFILE
from src.execution.runtime.runtime import AgentRuntime
from src.execution.runtime.tools.base import RuntimeTool, ToolCall
from src.execution.runtime.tools.registry import ToolRegistry
from src.execution.runtime.trace import InMemoryTraceStore
from src.execution.services.conversation_knowledge import ConversationKnowledgeService
from src.memory.models.memory_type import MemoryType
from src.memory.models.committed_memory import CommittedMemory, CommittedStatus
from src.memory.models.raw_event import (
    ProcessingStatus,
    RawEvent,
    SensitivityLevel,
    SourceType,
    VisibilityScope,
)
from src.platform.models.media_artifact import MediaArtifact
from src.shared.db.database import Base


class ScriptedModel:
    def __init__(self, responses):
        self.responses = list(responses)

    async def complete(self, **_kwargs):
        return self.responses.pop(0)


def _memory(
    memory_id: str,
    *,
    user_id: str = "u1",
    title: str = "正式记忆",
    body: str = "用户确认的长期记忆",
    status=CommittedStatus.ACTIVE,
    sensitivity=SensitivityLevel.NORMAL,
    valid_until=None,
):
    return CommittedMemory(
        id=memory_id,
        user_id=user_id,
        memory_type=MemoryType.FACT,
        title=title,
        body=body,
        confidence=0.9,
        importance=0.9,
        sensitivity=sensitivity,
        epistemic_status="user_confirmed",
        visibility_scope=VisibilityScope.PERSONAL,
        status=status,
        valid_from=datetime.now(timezone.utc) - timedelta(days=1),
        valid_until=valid_until,
    )


def _event(
    event_id: str,
    *,
    user_id: str,
    content: str,
    source_type=SourceType.FILE_IMPORT,
    metadata=None,
    sensitivity=SensitivityLevel.NORMAL,
    visibility_scope=VisibilityScope.PERSONAL,
):
    return RawEvent(
        id=event_id,
        user_id=user_id,
        source_type=source_type,
        source_id="test",
        occurred_at=datetime.now(timezone.utc),
        content=content,
        content_hash=f"hash-{event_id}",
        event_metadata=metadata or {},
        sensitivity=sensitivity,
        visibility_scope=visibility_scope,
        processing_status=ProcessingStatus.COMPLETED,
    )


def test_formal_memory_projection_filters_unfit_memories(monkeypatch, tmp_path):
    from src.execution.runtime.workspace import AgentWorkspaceService
    from src.execution.services import conversation_memory_projector as projector

    async def run():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            factory = async_sessionmaker(engine, expire_on_commit=False)
            async with factory() as db:
                db.add_all([
                    _memory("mem-visible", title="搬家计划", body="用户确认明年搬去大连"),
                    _memory("mem-sensitive", title="敏感内容", sensitivity=SensitivityLevel.SENSITIVE),
                    _memory("mem-revoked", title="撤销内容", status=CommittedStatus.REVOKED),
                    _memory(
                        "mem-expired",
                        title="过期内容",
                        valid_until=datetime.now(timezone.utc) - timedelta(hours=1),
                    ),
                ])
                await db.commit()
                monkeypatch.setattr(
                    projector,
                    "AgentWorkspaceService",
                    lambda: AgentWorkspaceService(base_dir=tmp_path),
                )

                result = await projector.refresh_conversation_memory_projection(
                    db, user_id="u1"
                )

                assert result["item_count"] == 1
                content = AgentWorkspaceService(base_dir=tmp_path).load(
                    user_id="u1", agent="conversational"
                ).memory_summary
                assert "mem-visible" in content
                assert "搬家计划" in content
                assert "mem-sensitive" not in content
                assert "mem-revoked" not in content
                assert "mem-expired" not in content
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_document_search_is_user_scoped_and_returns_bounded_source_excerpt():
    async def run():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            factory = async_sessionmaker(engine, expire_on_commit=False)
            async with factory() as db:
                own = _event(
                    "evt-doc-own",
                    user_id="u1",
                    content="年度计划：今年重点完成产品升级，并安排两次长途旅行。" * 40,
                    source_type=SourceType.MANUAL,
                    metadata={
                        "event_kind": "media_note",
                        "media_artifact_id": "media-own",
                        "note_title": "2026 年度计划",
                    },
                )
                other = _event(
                    "evt-doc-other",
                    user_id="u2",
                    content="年度计划：这是其他用户的内容，不允许返回。",
                )
                db.add_all([
                    own,
                    other,
                    MediaArtifact(
                        id="media-own",
                        user_id="u1",
                        raw_event_id=own.id,
                        source_channel="test",
                        media_type="document",
                        original_name="plan.docx",
                        status="extracted",
                    ),
                ])
                await db.commit()

                result = await ConversationKnowledgeService(db).search_source_documents(
                    user_id="u1", query="年度计划", limit=5
                )

                assert len(result["items"]) == 1
                item = result["items"][0]
                assert item["raw_event_id"] == "evt-doc-own"
                assert item["artifact_id"] == "media-own"
                assert item["epistemic_status"] == "document_statement"
                assert "其他用户" not in item["excerpt"]
                assert len(item["excerpt"]) <= 1_002
                assert "assert_as_user_fact_without_confirmation" in item["forbidden_use"]
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_document_search_excludes_sensitive_sources_and_supports_recent_reference():
    async def run():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            factory = async_sessionmaker(engine, expire_on_commit=False)
            async with factory() as db:
                db.add_all([
                    _event(
                        "evt-recent-safe",
                        user_id="u1",
                        content="这是一份足够长度的普通来源材料，用于验证最近文档引用。",
                        metadata={"original_name": "普通材料.md"},
                    ),
                    _event(
                        "evt-recent-sensitive",
                        user_id="u1",
                        content="这是一份绝对不能通过对话检索返回的敏感材料。",
                        sensitivity=SensitivityLevel.SENSITIVE,
                    ),
                    _event(
                        "evt-private-visibility",
                        user_id="u1",
                        content="这是一份可见范围为私密且不能通过共享认知返回的材料。",
                        visibility_scope=VisibilityScope.PRIVATE,
                    ),
                ])
                await db.commit()

                result = await ConversationKnowledgeService(db).search_source_documents(
                    user_id="u1", query="刚上传的文档写了什么", limit=5
                )

                assert [item["raw_event_id"] for item in result["items"]] == [
                    "evt-recent-safe"
                ]
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_unconfirmed_clues_require_verified_user_quote_and_safe_sensitivity():
    async def run():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            factory = async_sessionmaker(engine, expire_on_commit=False)
            async with factory() as db:
                event = _event(
                    "evt-conversation",
                    user_id="u1",
                    content="我可能明年搬去大连",
                    source_type=SourceType.CONVERSATION,
                )
                safe_case = MemoryWorkCase(
                    id="case-safe",
                    user_id="u1",
                    proposition_key="prop-safe",
                    case_type="plan",
                    title="搬去大连",
                    summary="可能存在搬家计划",
                    status="awaiting_evidence",
                    sensitivity="normal",
                    confidence=0.6,
                )
                sensitive_case = MemoryWorkCase(
                    id="case-sensitive",
                    user_id="u1",
                    proposition_key="prop-sensitive",
                    case_type="fact",
                    title="大连的敏感安排",
                    status="open",
                    sensitivity="sensitive",
                    confidence=0.6,
                )
                no_quote_case = MemoryWorkCase(
                    id="case-no-quote",
                    user_id="u1",
                    proposition_key="prop-no-quote",
                    case_type="plan",
                    title="大连的模型猜测",
                    status="open",
                    sensitivity="normal",
                    confidence=0.4,
                )
                db.add_all([event, safe_case, sensitive_case, no_quote_case])
                await db.flush()
                db.add(MemoryWorkEvidence(
                    id="evidence-safe",
                    case_id=safe_case.id,
                    user_id="u1",
                    raw_event_id=event.id,
                    source_turn_id="turn-user-1",
                    quote="我可能明年搬去大连",
                    relationship="supports",
                    source_type="conversation",
                    trust_class="user_assertion",
                    occurred_at=event.occurred_at,
                ))
                await db.commit()

                result = await ConversationKnowledgeService(db).get_unconfirmed_clues(
                    user_id="u1", query="搬去大连", limit=5
                )

                assert [item["case_id"] for item in result["items"]] == ["case-safe"]
                assert result["items"][0]["status"] == "unconfirmed"
                assert result["items"][0]["user_quote"] == "我可能明年搬去大连"
                assert "answer_as_fact" in result["items"][0]["forbidden_use"]
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_runtime_forces_clarification_after_unconfirmed_clue_lookup():
    async def lookup(_user_id, _params):
        return {
            "result_kind": "unconfirmed_clues",
            "items": [{
                "case_id": "case-1",
                "source_event_id": "evt-1",
                "status": "unconfirmed",
                "suggested_question": "你现在确实打算搬去大连吗？",
            }],
        }

    async def run():
        tool = RuntimeTool(
            "get_unconfirmed_memory_clues",
            "lookup",
            {"type": "object"},
            lookup,
        )
        runtime = AgentRuntime(
            model=ScriptedModel([
                RuntimeModelResponse(tool_calls=(ToolCall(tool.name, {"query": "搬家"}),)),
                RuntimeModelResponse(
                    text="你明年一定会搬去大连。",
                    response_mode="ANSWER",
                    confidence="HIGH",
                    citations=("case-1",),
                ),
            ]),
            registry=ToolRegistry([tool]),
            trace_store=InMemoryTraceStore(),
        )
        profile = replace(
            CONVERSATIONAL_PROFILE,
            allowed_tools=frozenset({tool.name}),
        )
        result = await runtime.run(
            runtime.new_context(user_id="u1", profile=profile),
            ({"role": "user", "content": "我有什么搬家计划？"},),
        )

        assert result.response_mode == "CLARIFY"
        assert result.confidence == "LOW"
        assert result.citations == ()
        assert result.unconfirmed_clue_accessed is True
        assert "不能把它当作事实" in result.final_text
        assert "确实打算搬去大连吗" in result.final_text

    asyncio.run(run())


def test_runtime_allows_real_document_source_id_as_citation():
    async def lookup(_user_id, _params):
        return {
            "result_kind": "document_sources",
            "items": [{"raw_event_id": "evt-doc", "excerpt": "文档陈述"}],
        }

    async def run():
        tool = RuntimeTool(
            "search_source_documents",
            "lookup",
            {"type": "object"},
            lookup,
        )
        runtime = AgentRuntime(
            model=ScriptedModel([
                RuntimeModelResponse(tool_calls=(ToolCall(tool.name, {"query": "计划"}),)),
                RuntimeModelResponse(
                    text="你上传的文档中写到年度计划。",
                    response_mode="ANSWER",
                    confidence="MEDIUM",
                    citations=("evt-doc", "invented"),
                ),
            ]),
            registry=ToolRegistry([tool]),
            trace_store=InMemoryTraceStore(),
        )
        profile = replace(
            CONVERSATIONAL_PROFILE,
            allowed_tools=frozenset({tool.name}),
        )
        result = await runtime.run(
            runtime.new_context(user_id="u1", profile=profile),
            ({"role": "user", "content": "年度计划文档写了什么？"},),
        )

        assert result.response_mode == "ANSWER"
        assert result.citations == ("evt-doc",)
        assert result.document_source_accessed is True
        assert result.unconfirmed_clue_accessed is False

    asyncio.run(run())
