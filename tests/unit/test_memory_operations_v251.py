from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import src.memory.services.deduplicator as deduplicator_module
from src.execution.models.memory_operations import (
    MemoryMaintenanceAction,
    MemoryMaintenanceControl,
    MemoryMaintenanceRun,
)
from src.execution.models.memory_relation import MemoryRelation
from src.execution.runtime.quality_eval import (
    ConversationQualityObservation,
    compute_quality_metrics,
)
from src.execution.services.memory_operations import MemoryOperationsCoordinator
from src.memory.models.committed_memory import CommittedMemory, CommittedStatus
from src.memory.models.memory_embedding import MemoryEmbedding
from src.memory.models.memory_source import MemorySource
from src.memory.models.memory_type import MemoryType
from src.memory.models.raw_event import SensitivityLevel, SourceType, VisibilityScope
from src.memory.services.deduplicator import MemoryDeduplicator
from src.shared.db.database import Base


UTC = timezone.utc


async def _factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


def _memory(memory_id: str, body: str, *, importance: float = 0.8) -> CommittedMemory:
    now = datetime.now(UTC)
    return CommittedMemory(
        id=memory_id,
        user_id="u-v251",
        memory_type=MemoryType.FACT,
        title=memory_id,
        body=body,
        confidence=0.95,
        importance=importance,
        sensitivity=SensitivityLevel.NORMAL,
        visibility_scope=VisibilityScope.PERSONAL,
        epistemic_status="user_assertion",
        status=CommittedStatus.ACTIVE,
        valid_from=now,
        origin_kind="working_agent",
    )


def test_quality_regression_pauses_high_risk_writes_and_recovers_through_shadow() -> None:
    async def run() -> None:
        engine, factory = await _factory()
        try:
            async with factory() as db:
                coordinator = MemoryOperationsCoordinator(db)
                control = await coordinator.apply_quality_report(
                    "u-v251",
                    report_id="quality-bad",
                    metrics={
                        "source_coverage": 0.98,
                        "wrong_merge_rate": 0.0,
                        "assistant_fact_leak_rate": 0.0,
                        "cleanup_safety_rate": 1.0,
                    },
                )
                assert control.state == "paused_automatically"
                assert coordinator._write_allowed(control, "merge") is False
                assert coordinator._write_allowed(control, "brief") is True

                control = await coordinator.request_resume(
                    "u-v251", actor="operator", reason="fixed source coverage"
                )
                assert control.state == "recovering"
                assert coordinator._write_allowed(control, "merge") is False

                control = await coordinator._evaluate_circuit_breaker("u-v251")
                assert control.state == "active"
                assert control.shadow_passes == 1
                assert coordinator._write_allowed(control, "merge") is True

                # Replaying a quality report is idempotent.
                await coordinator.apply_quality_report(
                    "u-v251",
                    report_id="quality-bad",
                    metrics={
                        "source_coverage": 0.98,
                        "wrong_merge_rate": 0.0,
                        "assistant_fact_leak_rate": 0.0,
                        "cleanup_safety_rate": 1.0,
                    },
                )
                actions = list((await db.execute(select(MemoryMaintenanceAction))).scalars())
                assert len(actions) == 1
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_quality_observation_rejects_content_and_reports_safety_rates() -> None:
    try:
        ConversationQualityObservation.from_mapping({
            "observation_id": "unsafe",
            "scenario_type": "chat",
            "content": "raw conversation must not be accepted",
        })
    except ValueError as exc:
        assert "unsupported fields" in str(exc)
    else:
        raise AssertionError("raw content was accepted by the content-free evaluator")

    metrics = compute_quality_metrics([
        ConversationQualityObservation.from_mapping({
            "observation_id": "safe",
            "scenario_type": "correction",
            "source_covered": True,
            "assistant_fact_leak": False,
            "wrong_merge": False,
            "cleanup_safe": True,
        })
    ])
    assert metrics["source_coverage"] == 1.0
    assert metrics["assistant_fact_leak_rate"] == 0.0
    assert metrics["wrong_merge_rate"] == 0.0
    assert metrics["cleanup_safety_rate"] == 1.0


def test_merge_rollback_restores_both_memories_and_removes_derived_links() -> None:
    async def run() -> None:
        engine, factory = await _factory()
        try:
            async with factory() as db:
                primary = _memory("mem-primary", "合并后的正文", importance=1.0)
                secondary = _memory("mem-secondary", "原始次要正文")
                secondary.status = CommittedStatus.SUPERSEDED
                run_row = MemoryMaintenanceRun(
                    id="run-merge",
                    user_id="u-v251",
                    kind="daily",
                    state="completed",
                    idempotency_key="run-merge-key",
                    cursor={},
                    counters={},
                    token_budget=0,
                    token_used=0,
                )
                db.add_all([primary, secondary, run_row])
                await db.flush()
                db.add_all([
                    MemorySource(
                        id="source-original",
                        memory_id=secondary.id,
                        source_type=SourceType.MANUAL,
                    ),
                    MemorySource(
                        id="source-copied",
                        memory_id=primary.id,
                        source_type=SourceType.MANUAL,
                    ),
                    MemoryRelation(
                        id="relation-merge",
                        user_id="u-v251",
                        source_memory_id=secondary.id,
                        target_memory_id=primary.id,
                        relation_type="duplicates",
                        confidence=1.0,
                    ),
                    MemoryMaintenanceAction(
                        id="action-merge",
                        run_id=run_row.id,
                        user_id="u-v251",
                        action="merge",
                        state="completed",
                        input_memory_ids=[primary.id, secondary.id],
                        input_event_ids=[],
                        output_memory_id=primary.id,
                        reason_code="exact_duplicate",
                        idempotency_key="action-merge-key",
                        reversible_until=datetime.now(UTC) + timedelta(days=30),
                        details={
                            "primary_before": {
                                "id": primary.id,
                                "title": "合并前主记忆",
                                "body": "原始主要正文",
                                "status": "active",
                                "revision": 1,
                                "valid_until": None,
                            },
                            "secondary_before": {
                                "id": secondary.id,
                                "title": "合并前次记忆",
                                "body": "原始次要正文",
                                "status": "active",
                                "revision": 1,
                                "valid_until": None,
                            },
                            "copied_source_ids": ["source-copied"],
                            "relation_id": "relation-merge",
                        },
                    ),
                ])
                await db.flush()

                with patch.object(
                    MemoryDeduplicator, "regenerate_embedding", new=AsyncMock()
                ):
                    rollback = await MemoryOperationsCoordinator(db).rollback_action(
                        user_id="u-v251",
                        action_id="action-merge",
                        actor="operator",
                        reason="验收回滚",
                    )
                await db.flush()

                assert rollback.action == "rollback"
                assert primary.title == "合并前主记忆"
                assert primary.body == "原始主要正文"
                assert secondary.status == CommittedStatus.ACTIVE
                assert await db.get(MemorySource, "source-copied") is None
                assert await db.get(MemoryRelation, "relation-merge") is None
                original_action = await db.get(MemoryMaintenanceAction, "action-merge")
                assert original_action.state == "rolled_back"
                assert original_action.rollback_action_id == rollback.id

                replay = await MemoryOperationsCoordinator(db).rollback_action(
                    user_id="u-v251",
                    action_id="action-merge",
                    actor="operator",
                    reason="重复请求",
                )
                assert replay.id == rollback.id
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_dedup_uses_bounded_neighbors_with_ten_thousand_memories(monkeypatch) -> None:
    async def run() -> None:
        engine, factory = await _factory()
        try:
            async with factory() as db:
                now = datetime.now(UTC)
                memories = []
                embeddings = []
                for index in range(10_000):
                    memory_id = f"mem-{index:05d}"
                    memories.append({
                        "id": memory_id,
                        "user_id": "u-v251",
                        "memory_type": MemoryType.FACT,
                        "title": memory_id,
                        "body": f"稳定事实 {index}",
                        "confidence": 0.9,
                        "importance": 0.8,
                        "sensitivity": SensitivityLevel.NORMAL,
                        "visibility_scope": VisibilityScope.PERSONAL,
                        "epistemic_status": "user_assertion",
                        "status": CommittedStatus.ACTIVE,
                        "valid_from": now,
                        "created_at": now + timedelta(microseconds=index),
                        "origin_kind": "working_agent",
                        "revision": 1,
                        "automation_metadata": {},
                        "tags": [],
                    })
                    if index >= 9_580:
                        embeddings.append({
                            "id": f"emb-{index:05d}",
                            "memory_id": memory_id,
                            "embedding_model": "test",
                            "embedding_vector": [1.0, 0.0],
                            "content_snapshot": memory_id,
                            "dimension": 2,
                        })
                await db.execute(insert(CommittedMemory), memories)
                await db.execute(insert(MemoryEmbedding), embeddings)
                await db.flush()

                comparisons = 0
                original = deduplicator_module.cosine_similarity

                def counted(first, second):
                    nonlocal comparisons
                    comparisons += 1
                    return original(first, second)

                monkeypatch.setattr(deduplicator_module, "cosine_similarity", counted)
                pairs = await MemoryDeduplicator(db).find_duplicates(
                    user_id="u-v251", similarity_threshold=0.9, top_k=20
                )
                assert pairs
                assert len(pairs) <= 20
                assert comparisons <= 20 * 20
        finally:
            await engine.dispose()

    asyncio.run(run())
