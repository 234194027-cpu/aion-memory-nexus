from __future__ import annotations

import ast
from pathlib import Path

from src.memory.models.graph_projection import GraphShadowObservation
from src.memory.services.account_deletion import AccountDeletionService
from src.shared.config import settings
from src.shared.db.database import Base, _import_all_models


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"


def _python_files() -> list[Path]:
    return sorted(SRC.rglob("*.py"))


def test_legacy_manual_memory_paths_are_physically_removed() -> None:
    forbidden_files = (
        SRC / "memory/services/memory_rewriter.py",
        SRC / "memory/tasks/memory_hygiene.py",
        SRC / "memory/prompts/rewriter.py",
    )
    assert all(not path.exists() for path in forbidden_files)

    forbidden_routes = (
        "/duplicates/merge",
        "/rewriter/run",
        "/rewriter/apply",
        "/hygiene/run",
        "/hygiene/apply",
    )
    route_sources = "\n".join(
        path.read_text(encoding="utf-8")
        for path in SRC.rglob("*.py")
        if "api" in path.parts
    )
    assert all(route not in route_sources for route in forbidden_routes)


def test_formal_memory_creation_has_one_governed_service_boundary() -> None:
    offenders: list[str] = []
    for path in _python_files():
        if path.name == "committed_memory.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id == "CommittedMemory":
                    offenders.append(path.relative_to(ROOT).as_posix())
    assert offenders == ["src/execution/services/memory_commit_service.py"]


def test_low_level_dedup_merge_is_called_only_by_operations_coordinator() -> None:
    call_sites: list[str] = []
    for path in _python_files():
        if path.name == "deduplicator.py":
            continue
        source = path.read_text(encoding="utf-8")
        if ".merge(" in source and "MemoryDeduplicator" in source:
            call_sites.append(path.relative_to(ROOT).as_posix())
    assert call_sites == ["src/execution/services/memory_operations.py"]


def test_graphiti_remains_shadow_only_and_observations_have_no_raw_content() -> None:
    assert settings.GRAPHITI_SHADOW_MODE is True
    columns = set(GraphShadowObservation.__table__.columns.keys())
    assert {"query_hash", "baseline_memory_ids", "graph_memory_ids", "source_coverage"} <= columns
    assert not columns.intersection({"query", "question", "content", "body", "prompt", "response"})

    runtime_source = (SRC / "execution/api/runtime.py").read_text(encoding="utf-8")
    assert '"active_write_authority": False' in runtime_source
    retrieval_source = (SRC / "memory/services/retrieval_engine.py").read_text(encoding="utf-8")
    assert "graph_time_relations" not in retrieval_source
    assert "graph_paths" not in retrieval_source


def test_account_erasure_order_covers_every_user_owned_table() -> None:
    _import_all_models()
    user_tables = {
        table.name
        for table in Base.metadata.tables.values()
        if "user_id" in table.c and table.name != "users"
    }
    # These three are deleted by owner IDs before the explicit user table pass.
    separately_deleted = {"agent_steps", "memory_embeddings", "memory_sources"}
    assert user_tables.difference(AccountDeletionService._USER_TABLE_DELETE_ORDER) <= separately_deleted
    assert set(AccountDeletionService._USER_TABLE_DELETE_ORDER) <= user_tables
