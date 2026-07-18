"""Characterization tests for src/memory/services/retrieval_engine.py.

WP-0A-T05: 锁定 RetrievalEngine 的 reconstruct_context 输出 schema。

测试目标:
  - _empty_context 顶层字段集合稳定
  - _build_output 顶层字段集合稳定（与 _empty_context 一致）
  - relevant_memories 每项的字段集合稳定
  - meta 字段集合稳定
  - retrieval_trace 每项字段集合稳定（含 score/scope 子结构）
  - _empty_context 与 _build_output 的顶层字段集合一致（schema 不变）

注意: 仅断言 schema 不变性，不验证检索质量。
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from src.memory.services.retrieval_engine import RetrievalEngine
from src.memory.services.memory_os import build_retrieval_trace_entry


def _make_memory_stub(*, memory_id: str = "mem_snapshot_001", tags: list | None = None) -> SimpleNamespace:
    """构造一个最小的 memory stub 用于 _build_output schema 测试。

    SimpleNamespace 提供与 CommittedMemory 相同的属性访问接口，
    避免依赖 DB fixture。
    """
    return SimpleNamespace(
        id=memory_id,
        user_id="user_snapshot",
        project_id="life-memory-system",
        repo_id=None,
        workspace_id=None,
        memory_type=type("MT", (), {"value": "fact"})(),
        title="Snapshot memory",
        body="Body content for snapshot characterization test.",
        importance=0.8,
        confidence=0.9,
        epistemic_status="verified",
        tags=tags if tags is not None else ["python", "testing"],
        valid_from=datetime(2026, 7, 12, tzinfo=timezone.utc),
        valid_until=None,
        created_at=datetime(2026, 7, 12, 10, 0, 0, tzinfo=timezone.utc),
    )


EXPECTED_TOP_LEVEL_KEYS = {
    "context_summary",
    "decision_history",
    "patterns",
    "conflicts",
    "relevant_memories",
    "entities",
    "context_tiers",
    "context_tree",
    "memory_layers",
    "relation_graph",
    "graph_context",
    "memory_evolution",
    "retrieval_trace",
    "meta",
}


EXPECTED_RELEVANT_MEMORY_KEYS = {
    "memory_id",
    "memory_uri",
    "context_path",
    "title",
    "content",
    "memory_type",
    "epistemic_status",
    "memory_layer",
    "importance",
    "confidence",
    "tags",
    "similarity",
    "final_score",
    "valid_from",
    "valid_until",
}


EXPECTED_META_KEYS = {
    "total_found",
    "question",
    "retrieved_at",
    "embed_method",
    "recall_level",
}


EXPECTED_TRACE_ENTRY_KEYS = {
    "rank",
    "memory_id",
    "memory_uri",
    "context_path",
    "layer",
    "memory_type",
    "matched_by",
    "recall_level",
    "reason",
    "score",
    "scope",
    "created_at",
    "tags",
}


EXPECTED_TRACE_SCORE_KEYS = {"similarity", "final", "importance", "confidence"}
EXPECTED_TRACE_SCOPE_KEYS = {"project_id", "repo_id", "workspace_id"}


def test_empty_context_top_level_keys_stable():
    """_empty_context 顶层字段集合稳定。"""
    engine = RetrievalEngine(db=None)
    result = engine._empty_context(question="snapshot test", embed_method="keyword", recall_level="work_context")
    assert set(result.keys()) == EXPECTED_TOP_LEVEL_KEYS


def test_empty_context_meta_keys_stable():
    """_empty_context.meta 字段集合稳定。"""
    engine = RetrievalEngine(db=None)
    result = engine._empty_context(question="snapshot test", embed_method="keyword", recall_level="work_context")
    assert set(result["meta"].keys()) == EXPECTED_META_KEYS


def test_empty_context_relevant_memories_is_empty_list():
    """_empty_context.relevant_memories 必须为空 list（schema 类型稳定）。"""
    engine = RetrievalEngine(db=None)
    result = engine._empty_context(question="snapshot test", embed_method="keyword", recall_level="work_context")
    assert result["relevant_memories"] == []
    assert result["retrieval_trace"] == []
    assert result["entities"] == []


def test_build_output_top_level_keys_stable():
    """_build_output 顶层字段集合稳定（与 _empty_context 一致）。"""
    engine = RetrievalEngine(db=None)
    memory = _make_memory_stub()
    clusters = {
        "context_summary": "Found 1 relevant memory.",
        "decision_history": [],
        "patterns": [],
        "conflicts": [],
    }
    result = engine._build_output(
        question="snapshot test",
        memories=[memory],
        scores=[0.85],
        final_scores=[0.72],
        clusters=clusters,
        embed_method="semantic",
        recall_level="work_context",
        relations=[],
    )
    assert set(result.keys()) == EXPECTED_TOP_LEVEL_KEYS


def test_build_output_relevant_memory_item_keys_stable():
    """_build_output.relevant_memories 每项的字段集合稳定。"""
    engine = RetrievalEngine(db=None)
    memory = _make_memory_stub()
    clusters = {"context_summary": "", "decision_history": [], "patterns": [], "conflicts": []}
    result = engine._build_output(
        question="snapshot test",
        memories=[memory],
        scores=[0.85],
        final_scores=[0.72],
        clusters=clusters,
        embed_method="semantic",
        recall_level="work_context",
        relations=[],
    )
    assert len(result["relevant_memories"]) == 1
    assert set(result["relevant_memories"][0].keys()) == EXPECTED_RELEVANT_MEMORY_KEYS


def test_build_output_meta_keys_stable():
    """_build_output.meta 字段集合稳定。"""
    engine = RetrievalEngine(db=None)
    memory = _make_memory_stub()
    clusters = {"context_summary": "", "decision_history": [], "patterns": [], "conflicts": []}
    result = engine._build_output(
        question="snapshot test",
        memories=[memory],
        scores=[0.85],
        final_scores=[0.72],
        clusters=clusters,
        embed_method="semantic",
        recall_level="work_context",
        relations=[],
    )
    assert set(result["meta"].keys()) == EXPECTED_META_KEYS


def test_build_output_and_empty_context_share_top_level_keys():
    """_build_output 与 _empty_context 顶层字段集合一致。"""
    engine = RetrievalEngine(db=None)
    memory = _make_memory_stub()
    clusters = {"context_summary": "", "decision_history": [], "patterns": [], "conflicts": []}
    full = engine._build_output(
        question="snapshot test",
        memories=[memory],
        scores=[0.85],
        final_scores=[0.72],
        clusters=clusters,
        embed_method="semantic",
        recall_level="work_context",
        relations=[],
    )
    empty = engine._empty_context(question="snapshot test", embed_method="keyword", recall_level="work_context")
    assert set(full.keys()) == set(empty.keys()) == EXPECTED_TOP_LEVEL_KEYS


def test_build_retrieval_trace_entry_keys_stable():
    """build_retrieval_trace_entry 返回的字段集合稳定。"""
    memory = _make_memory_stub()
    entry = build_retrieval_trace_entry(
        memory=memory,
        rank=1,
        similarity=0.85,
        final_score=0.72,
        embed_method="semantic",
        recall_level="work_context",
    )
    assert set(entry.keys()) == EXPECTED_TRACE_ENTRY_KEYS


def test_build_retrieval_trace_entry_score_keys_stable():
    """trace entry.score 子字段集合稳定。"""
    memory = _make_memory_stub()
    entry = build_retrieval_trace_entry(
        memory=memory,
        rank=1,
        similarity=0.85,
        final_score=0.72,
        embed_method="semantic",
        recall_level="work_context",
    )
    assert set(entry["score"].keys()) == EXPECTED_TRACE_SCORE_KEYS


def test_build_retrieval_trace_entry_scope_keys_stable():
    """trace entry.scope 子字段集合稳定。"""
    memory = _make_memory_stub()
    entry = build_retrieval_trace_entry(
        memory=memory,
        rank=1,
        similarity=0.85,
        final_score=0.72,
        embed_method="semantic",
        recall_level="work_context",
    )
    assert set(entry["scope"].keys()) == EXPECTED_TRACE_SCOPE_KEYS


def test_build_output_decision_history_item_keys_stable():
    """_build_output.decision_history 每项的字段集合稳定。"""
    engine = RetrievalEngine(db=None)
    memory = _make_memory_stub()
    # _build_output 在 _llm_cluster 返回后从 decision_history 中读取 memory_id/content/reason/outcome
    clusters = {
        "context_summary": "",
        "decision_history": [
            {
                "memory_id": memory.id,
                "content": "decided to use sqlite",
                "reason": "single-user mode",
                "outcome": "shipped",
                "memory_type": "decision",
                "epistemic_status": "verified",
                "importance": 0.9,
            }
        ],
        "patterns": [],
        "conflicts": [],
    }
    result = engine._build_output(
        question="snapshot test",
        memories=[memory],
        scores=[0.85],
        final_scores=[0.72],
        clusters=clusters,
        embed_method="semantic",
        recall_level="work_context",
        relations=[],
    )
    assert len(result["decision_history"]) == 1
    assert set(result["decision_history"][0].keys()) == {
        "memory_id",
        "content",
        "reason",
        "outcome",
        "memory_type",
        "epistemic_status",
        "importance",
    }


def test_build_output_conflicts_item_keys_stable():
    """_build_output.conflicts 每项的字段集合稳定。"""
    engine = RetrievalEngine(db=None)
    memory = _make_memory_stub()
    clusters = {
        "context_summary": "",
        "decision_history": [],
        "patterns": [],
        "conflicts": [
            {
                "current": "use sqlite",
                "past": "use postgres",
                "explanation": "switched to sqlite for single-user",
            }
        ],
    }
    result = engine._build_output(
        question="snapshot test",
        memories=[memory],
        scores=[0.85],
        final_scores=[0.72],
        clusters=clusters,
        embed_method="semantic",
        recall_level="work_context",
        relations=[],
    )
    assert len(result["conflicts"]) == 1
    assert set(result["conflicts"][0].keys()) == {"current", "past", "explanation"}
