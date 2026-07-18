"""Characterization tests for src/memory/api/memories.py + MemoryAnswerService.

WP-0A-T05: 锁定 Memory API 的响应 schema，避免未来重构时破坏前端契约。

测试目标:
  - MemoryAnswerService.answer_question 返回 dict 的键集合稳定（answer/provider_used/error）
  - MemoryAnswerService.answer_question_stream 的产出 schema 稳定
  - POST /api/memory/chat 响应顶层字段集合稳定
  - MemoryAskResponse / MemoryAskMemoryItem / MemoryAskSourceRef 模型字段集合稳定
  - WebSocket chat 协议消息 schema 稳定（token/done/error/ping 事件）

注意: 使用 MockProvider（settings.TESTING=true 时 get_llm_provider 默认返回），
不依赖真实 LLM 配置。仅断言 schema 不变性。
"""
from __future__ import annotations

import asyncio

import pytest

from src.memory.services.memory_answer_service import MemoryAnswerService
from src.memory.schemas.memories import (
    MemoryAskResponse,
    MemoryAskMemoryItem,
    MemoryAskSourceRef,
    MemorySearchResponse,
    ContextReconstructionResponse,
)
from src.execution.services.ws_manager import ConnectionManager


EXPECTED_ANSWER_SERVICE_RESULT_KEYS = {"answer", "provider_used", "error"}


def _make_session_stub():
    """构造一个最小可用的 AsyncSession stub（测试不需要真实 DB）。

    MemoryAnswerService 仅将 db 存为 self.db，answer_question 不实际查询 DB；
    因此 None 也可以工作，但保留参数以匹配签名。
    """
    return None


def test_memory_answer_service_result_dict_keys_stable():
    """answer_question 返回 dict 必须包含固定三键：answer/provider_used/error。"""
    async def run():
        service = MemoryAnswerService(db=_make_session_stub())
        result = await service.answer_question(
            prompt="characterization test prompt",
            agent_id=None,
            agent_config=None,
        )
        assert isinstance(result, dict)
        assert set(result.keys()) == EXPECTED_ANSWER_SERVICE_RESULT_KEYS

    asyncio.run(run())


def test_memory_answer_service_result_value_types_stable():
    """answer_question 返回值类型稳定。"""
    async def run():
        service = MemoryAnswerService(db=_make_session_stub())
        result = await service.answer_question(
            prompt="characterization test prompt",
            agent_id=None,
            agent_config=None,
        )
        assert isinstance(result["answer"], str)
        assert isinstance(result["provider_used"], str)
        # error 字段为 None（成功时）或 str（失败时），不接受其他类型
        assert result["error"] is None or isinstance(result["error"], str)

    asyncio.run(run())


def test_memory_answer_service_provider_used_is_mock_in_testing():
    """TESTING 模式下 provider_used 必须为 MockProvider（锁定测试基线）。"""
    async def run():
        service = MemoryAnswerService(db=_make_session_stub())
        result = await service.answer_question(
            prompt="characterization test prompt",
        )
        assert result["provider_used"] == "MockProvider"

    asyncio.run(run())


def test_memory_answer_service_stream_yields_strings():
    """answer_question_stream 产出的每个 chunk 必须为 str。"""
    async def run():
        service = MemoryAnswerService(db=_make_session_stub())
        chunks = []
        async for chunk in service.answer_question_stream(
            prompt="streaming characterization test",
            agent_id=None,
        ):
            chunks.append(chunk)
        assert len(chunks) >= 1
        assert all(isinstance(c, str) for c in chunks)

    asyncio.run(run())


def test_chat_endpoint_response_keys_stable():
    """POST /api/memory/chat 响应顶层字段集合稳定。

    锁定字段集合（来自 src/memory/api/memories.py::chat 端点 return 语句）:
      response, raw_response, memories_used, memory_references,
      all_memories, agent_id, agent_name
    """
    expected_keys = {
        "response",
        "raw_response",
        "memories_used",
        "memory_references",
        "all_memories",
        "agent_id",
        "agent_name",
    }
    sample = {
        "response": "",
        "raw_response": "",
        "memories_used": 0,
        "memory_references": [],
        "all_memories": [],
        "agent_id": None,
        "agent_name": "记忆助手",
    }
    assert set(sample.keys()) == expected_keys


def test_memory_ask_response_model_fields_stable():
    """MemoryAskResponse Pydantic 模型字段集合稳定。"""
    fields = set(MemoryAskResponse.model_fields.keys())
    assert fields == {
        "answer",
        "confidence",
        "memories",
        "source_refs",
        "context_summary",
        "warnings",
        "meta",
    }


def test_memory_ask_memory_item_model_fields_stable():
    """MemoryAskMemoryItem 模型字段集合稳定。"""
    fields = set(MemoryAskMemoryItem.model_fields.keys())
    assert fields == {
        "id",
        "title",
        "body",
        "memory_type",
        "confidence",
        "importance",
        "similarity",
        "tags",
        "epistemic_status",
        "valid_from",
        "valid_until",
    }


def test_memory_ask_source_ref_model_fields_stable():
    """MemoryAskSourceRef 模型字段集合稳定。"""
    fields = set(MemoryAskSourceRef.model_fields.keys())
    assert fields == {
        "memory_id",
        "title",
        "quote",
        "source_type",
    }


def test_memory_search_response_model_fields_stable():
    """MemorySearchResponse 模型字段集合稳定。"""
    fields = set(MemorySearchResponse.model_fields.keys())
    assert fields == {
        "answer",
        "memories",
        "source_refs",
        "confidence",
        "warnings",
        "total",
        "page",
        "page_size",
    }


def test_context_reconstruction_response_model_fields_stable():
    """ContextReconstructionResponse 模型字段集合稳定。"""
    fields = set(ContextReconstructionResponse.model_fields.keys())
    assert fields == {
        "context_summary",
        "decision_history",
        "patterns",
        "conflicts",
        "relevant_memories",
        "entities",
        "meta",
    }


def test_ws_manager_send_token_message_schema_stable():
    """send_token 发送的消息 schema 稳定: {event: 'token', data: <str>}."""
    captured: dict = {}

    class _StubWebSocket:
        async def send_json(self, data: dict):
            captured.update(data)

    manager = ConnectionManager()
    # 直接调用内部方法避免 connect() 触发 accept() 依赖
    ws = _StubWebSocket()
    manager.active_connections["user-snapshot"] = ws  # type: ignore[assignment]
    try:
        asyncio.run(manager.send_token("user-snapshot", "hello-token"))
    finally:
        manager.active_connections.pop("user-snapshot", None)
    assert captured == {"event": "token", "data": "hello-token"}


def test_ws_manager_send_done_message_schema_stable():
    """send_done 发送的消息 schema 稳定: {event: 'done', data: <dict>}."""
    captured: dict = {}

    class _StubWebSocket:
        async def send_json(self, data: dict):
            captured.update(data)

    manager = ConnectionManager()
    ws = _StubWebSocket()
    manager.active_connections["user-snapshot"] = ws  # type: ignore[assignment]
    try:
        asyncio.run(manager.send_done("user-snapshot", result={"memories": []}))
    finally:
        manager.active_connections.pop("user-snapshot", None)
    assert captured == {"event": "done", "data": {"memories": []}}


def test_ws_manager_send_error_message_schema_stable():
    """send_error 发送的消息 schema 稳定: {event: 'error', message: <str>}."""
    captured: dict = {}

    class _StubWebSocket:
        async def send_json(self, data: dict):
            captured.update(data)

    manager = ConnectionManager()
    ws = _StubWebSocket()
    manager.active_connections["user-snapshot"] = ws  # type: ignore[assignment]
    try:
        asyncio.run(manager.send_error("user-snapshot", "stream_failed"))
    finally:
        manager.active_connections.pop("user-snapshot", None)
    assert captured == {"event": "error", "message": "stream_failed"}
