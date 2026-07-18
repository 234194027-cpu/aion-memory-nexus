"""Contract tests for the derived Graphiti projection outbox.

The tests deliberately avoid a live Neo4j instance: source-of-truth writes must
remain usable when the graph service is unavailable.
"""

from src.memory.models.graph_projection import GraphProjectionOperation, projection_key


def test_projection_key_is_stable_and_versioned():
    first = projection_key("raw_event", "evt-1", "hash-a", GraphProjectionOperation.UPSERT)
    same = projection_key("raw_event", "evt-1", "hash-a", GraphProjectionOperation.UPSERT)
    changed = projection_key("raw_event", "evt-1", "hash-b", GraphProjectionOperation.UPSERT)
    deleted = projection_key("raw_event", "evt-1", "hash-a", GraphProjectionOperation.DELETE)

    assert first == same
    assert first != changed
    assert first != deleted


def test_projection_ontology_is_closed_to_initial_v3_terms():
    from src.memory.services.graph_projection import GRAPH_RELATION_TYPES, GRAPH_NODE_TYPES

    assert GRAPH_NODE_TYPES == {"person", "project", "repository", "task", "decision", "preference", "event"}
    assert GRAPH_RELATION_TYPES == {
        "related_to", "occurred_in", "responsible_for", "depends_on",
        "supports", "contradicts", "corrects", "supersedes",
    }


def test_replay_checkpoint_advances_by_sorted_source_and_marks_completion():
    from datetime import datetime, timezone
    from types import SimpleNamespace

    from src.memory.models.graph_projection import GraphReplayCheckpoint
    from src.memory.services.graph_projection import _advance_checkpoint

    checkpoint = GraphReplayCheckpoint(id="grc-1", user_id="user-1", source_kind="raw_event")
    rows = [
        SimpleNamespace(id="evt-1", occurred_at=datetime(2026, 1, 1, tzinfo=timezone.utc)),
        SimpleNamespace(id="evt-2", occurred_at=datetime(2026, 1, 2, tzinfo=timezone.utc)),
    ]
    _advance_checkpoint(checkpoint, rows, occurred_attr="occurred_at", source_id_attr="id", queued=1)

    assert checkpoint.cursor_source_id == "evt-2"
    assert checkpoint.scanned_count == 2
    assert checkpoint.queued_count == 1
    assert checkpoint.completed_at is None

    _advance_checkpoint(checkpoint, [], occurred_attr="occurred_at", source_id_attr="id", queued=0)
    assert checkpoint.completed_at is not None


def test_graph_admin_dependency_fails_closed_outside_solo_mode(monkeypatch):
    import asyncio
    from types import SimpleNamespace

    from fastapi import HTTPException

    from src.shared.config import settings
    from src.shared.security.dependencies import get_graph_admin_user

    monkeypatch.setattr(settings, "SOLO_MODE", False)
    monkeypatch.setattr(settings, "GRAPHITI_ADMIN_USER_IDS", "owner-1, owner-2")

    assert asyncio.run(get_graph_admin_user(SimpleNamespace(id="owner-2"))).id == "owner-2"
    try:
        asyncio.run(get_graph_admin_user(SimpleNamespace(id="other-user")))
    except HTTPException as exc:
        assert exc.status_code == 403
        assert exc.detail == "graph_admin_required"
    else:
        raise AssertionError("unconfigured graph operator was accepted")


def test_agent_after_end_response_exposes_formal_memory_count_only():
    from src.execution.schemas.agents import AgentAfterEndResponse

    payload = AgentAfterEndResponse(
        event_id="evt-1", formal_memory_count=0, processing_status="queued"
    )
    assert payload.formal_memory_count == 0
    assert "candidate_count" not in payload.model_dump()
