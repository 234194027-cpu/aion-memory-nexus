from datetime import datetime, timezone
from types import SimpleNamespace

from src.memory.models.memory_type import MemoryType
from src.memory.services.memory_os import (
    build_agent_memory_protocol,
    build_context_tiers,
    build_context_path,
    build_context_tree,
    build_layer_summary,
    build_memory_evolution,
    build_memory_uri,
    build_relation_graph,
    build_retrieval_trace_entry,
    memory_layer_for_type,
)
from src.memory.services.retrieval_engine import RetrievalEngine


def _memory(memory_id, memory_type, importance=0.5):
    return SimpleNamespace(
        id=memory_id,
        title=f"title {memory_id}",
        body=f"body {memory_id}",
        memory_type=memory_type,
        importance=importance,
        confidence=0.8,
        epistemic_status="user_assertion",
        tags=["tag"],
        project_id="project",
        repo_id="repo",
        workspace_id=None,
        created_at=datetime.now(timezone.utc),
        valid_from=datetime.now(timezone.utc),
        valid_until=None,
    )


def test_memory_type_maps_to_operating_layers():
    assert memory_layer_for_type(MemoryType.TASK) == "working"
    assert memory_layer_for_type(MemoryType.TIMELINE_EVENT) == "episodic"
    assert memory_layer_for_type(MemoryType.FACT) == "semantic"
    assert memory_layer_for_type(MemoryType.PROJECT_CONTEXT) == "semantic"
    assert memory_layer_for_type(MemoryType.DECISION) == "procedural"
    assert memory_layer_for_type("unknown") == "semantic"


def test_layer_summary_and_trace_are_agent_readable():
    memories = [
        _memory("m1", MemoryType.TASK),
        _memory("m2", MemoryType.FACT),
        _memory("m3", MemoryType.DECISION),
    ]

    summary = build_layer_summary(memories)
    assert summary["counts"]["working"] == 1
    assert summary["counts"]["semantic"] == 1
    assert summary["counts"]["procedural"] == 1
    assert summary["policy"]["layers"]

    trace = build_retrieval_trace_entry(
        memory=memories[2],
        rank=1,
        similarity=0.42,
        final_score=0.71,
        embed_method="hybrid",
        recall_level="work_context",
    )
    assert trace["memory_id"] == "m3"
    assert trace["memory_uri"] == "life://memory/procedural/project/m3"
    assert trace["layer"] == "procedural"
    assert trace["score"]["similarity"] == 0.42
    assert trace["score"]["final"] == 0.71


def test_context_tiers_relation_graph_and_evolution_are_structured():
    memories = [
        _memory("m1", MemoryType.TIMELINE_EVENT),
        _memory("m2", MemoryType.FACT, importance=0.7),
        _memory("m3", MemoryType.DECISION, importance=0.9),
    ]
    memories[0].confidence = 0.4
    memories[1].tags = ["project", "repeat"]
    memories[2].tags = ["decision", "repeat"]
    relation = SimpleNamespace(
        id="rel1",
        source_memory_id="m1",
        target_memory_id="m3",
        relation_type="supports",
        reason="m1 supports m3",
        confidence=0.9,
        created_at=datetime.now(timezone.utc),
    )

    assert build_memory_uri(memories[0]) == "life://memory/episodic/project/m1"
    assert build_context_path(memories[0]) == "/context/project/episodic/timeline_event/m1"

    tiers = build_context_tiers(memories)
    assert tiers["L0"]["memory_count"] == 3
    assert "compressed_text" in tiers["L0"]
    assert "timeline_event" in tiers["L0"]["compressed_text"]
    assert tiers["L1"]["layered_working_set"]["episodic"][0]["memory_uri"].endswith("/m1")
    assert tiers["L1"]["layer_summaries"]["semantic"]["compressed_text"]
    assert tiers["L2"]["memory_refs"][0]["valid_from"]
    assert tiers["L2"]["memory_refs"][0]["context_path"].startswith("/context/project/")

    graph = build_relation_graph(memories, [relation])
    assert len(graph["nodes"]) == 3
    assert graph["edges"][0]["relation_type"] == "supports"
    assert graph["relation_counts"]["supports"] == 1

    evolution = build_memory_evolution(memories)
    assert "review_low_confidence" in evolution["candidate_actions"]
    assert "repeat" in evolution["promotion_tag_candidates"]


def test_context_tree_groups_memories_by_project_layer_and_type():
    memories = [
        _memory("m1", MemoryType.DECISION),
        _memory("m2", MemoryType.FACT),
        _memory("m3", MemoryType.TIMELINE_EVENT),
    ]
    tree = build_context_tree(memories)

    assert tree["root"]["path"] == "/context"
    assert len(tree["index"]) == 3
    assert "/context/project/procedural/decision/m1" in [
        item["path"] for item in tree["index"]
    ]
    assert tree["recursive_retrieval"]["path_semantics"].startswith("/context/{project_id}")


def test_agent_memory_protocol_requires_before_after_and_delta():
    protocol = build_agent_memory_protocol("ship memory iteration", "work_context")
    joined = "\n".join(protocol["required_steps"])
    assert "relation_graph" in joined
    assert "context_tree" in joined
    assert "L0 compressed_text" in joined
    assert "memory_after_end" in joined
    assert "memory_upload_daily_delta" in joined
    assert protocol["write_back_shape"]["decisions"]


def test_retrieval_prioritization_keeps_similarity_scores_aligned_after_sort():
    engine = RetrievalEngine.__new__(RetrievalEngine)
    low_priority_high_sim = _memory("m_low", MemoryType.TASK, importance=0.1)
    high_priority_low_sim = _memory("m_high", MemoryType.DECISION, importance=1.0)

    memories, sim_scores, final_scores = engine._prioritize_by_type(
        [low_priority_high_sim, high_priority_low_sim],
        [0.4, 0.2],
    )

    assert memories[0].id == "m_high"
    assert sim_scores[0] == 0.2
    assert final_scores[0] > final_scores[1]


def test_retrieval_output_includes_memory_os_context_package():
    engine = RetrievalEngine.__new__(RetrievalEngine)
    memories = [
        _memory("m_fact", MemoryType.FACT, importance=0.7),
        _memory("m_decision", MemoryType.DECISION, importance=0.9),
    ]
    relation = SimpleNamespace(
        id="rel1",
        source_memory_id="m_fact",
        target_memory_id="m_decision",
        relation_type="supports",
        reason="fact supports decision",
        confidence=0.8,
        created_at=datetime.now(timezone.utc),
    )

    output = engine._build_output(
        "what should the agent remember?",
        memories,
        [0.5, 0.4],
        [0.6, 0.7],
        {"context_summary": "summary", "decision_history": [], "patterns": [], "conflicts": []},
        "keyword",
        "work_context",
        [relation],
    )

    assert output["relevant_memories"][0]["memory_uri"] == "life://memory/semantic/project/m_fact"
    assert output["relevant_memories"][0]["context_path"] == "/context/project/semantic/fact/m_fact"
    assert output["context_tiers"]["L0"]["memory_count"] == 2
    assert output["context_tree"]["index"][0]["path"].startswith("/context/project/")
    assert output["relation_graph"]["edges"][0]["relation_type"] == "supports"
    assert output["memory_evolution"]["state_operator"] == "retrieve"
    assert output["retrieval_trace"][0]["memory_uri"]
