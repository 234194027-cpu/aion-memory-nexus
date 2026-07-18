import asyncio
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import delete, select

from src.memory.models.memory_type import MemoryType
from src.memory.models.committed_memory import CommittedMemory, CommittedStatus
from src.memory.models.raw_event import SensitivityLevel, VisibilityScope
from src.memory.services.governance_policy import (
    FORMAL_MEMORY_BLOCKED_EPISTEMIC_STATUSES,
    POLICY_VERSION,
    derive_epistemic_status,
    normalize_recall_level,
    source_trust_class,
)
from src.memory.services.retrieval_engine import RetrievalEngine
from src.shared.db.database import async_session, init_db


def test_policy_normalizes_unknown_scope_and_classifies_sources() -> None:
    assert POLICY_VERSION == "memory-governance-v2.4"
    assert normalize_recall_level("unknown-scope") == "work_context"
    assert normalize_recall_level("FULL_TRUSTED") == "full_trusted"
    assert source_trust_class("manual") == "user_assertion"
    assert source_trust_class("agent_api") == "agent_assertion"
    assert source_trust_class("unknown") == "unclassified"
    assert derive_epistemic_status("manual") == "user_assertion"
    assert derive_epistemic_status("file_import") == "user_imported"


def test_formal_memory_policy_blocks_unconfirmed_model_and_agent_claims() -> None:
    assert FORMAL_MEMORY_BLOCKED_EPISTEMIC_STATUSES == {
        "agent_assertion",
        "assistant_supplied",
        "external_claim",
        "model_inference",
    }


def test_unknown_recall_scope_falls_back_to_safe_work_context_filter() -> None:
    async def run() -> None:
        await init_db()
        user_id = f"policy-user-{uuid4().hex}"
        visible_id = f"mem-{uuid4().hex}"
        hidden_sensitive_id = f"mem-{uuid4().hex}"
        hidden_private_id = f"mem-{uuid4().hex}"
        async with async_session() as session:
            now = datetime.now(timezone.utc)
            session.add_all([
                CommittedMemory(
                    id=visible_id, user_id=user_id, memory_type=MemoryType.FACT, title="visible", body="visible",
                    confidence=0.9, importance=0.8, sensitivity=SensitivityLevel.NORMAL,
                    visibility_scope=VisibilityScope.PROJECT, status=CommittedStatus.ACTIVE, valid_from=now,
                ),
                CommittedMemory(
                    id=hidden_sensitive_id, user_id=user_id, memory_type=MemoryType.FACT, title="sensitive", body="sensitive",
                    confidence=0.9, importance=0.8, sensitivity=SensitivityLevel.SENSITIVE,
                    visibility_scope=VisibilityScope.PRIVATE, status=CommittedStatus.ACTIVE, valid_from=now,
                ),
                CommittedMemory(
                    id=hidden_private_id, user_id=user_id, memory_type=MemoryType.FACT, title="private", body="private",
                    confidence=0.9, importance=0.8, sensitivity=SensitivityLevel.NORMAL,
                    visibility_scope=VisibilityScope.PERSONAL, status=CommittedStatus.ACTIVE, valid_from=now,
                ),
            ])
            await session.commit()
            engine = RetrievalEngine(session)
            result = await session.execute(
                select(CommittedMemory.id).where(engine._build_filter(user_id, None, "unknown-scope"))
            )
            assert set(result.scalars()) == {visible_id}
            await session.execute(delete(CommittedMemory).where(CommittedMemory.user_id == user_id))
            await session.commit()

    asyncio.run(run())


def test_expired_active_memory_is_not_retrievable() -> None:
    async def run() -> None:
        await init_db()
        user_id = f"expired-policy-user-{uuid4().hex}"
        now = datetime.now(timezone.utc)
        active_id = f"active-{uuid4().hex}"
        expired_id = f"expired-{uuid4().hex}"
        async with async_session() as session:
            session.add_all([
                CommittedMemory(
                    id=active_id, user_id=user_id, memory_type=MemoryType.FACT, title="current", body="current",
                    confidence=0.9, importance=0.8, sensitivity=SensitivityLevel.NORMAL,
                    visibility_scope=VisibilityScope.PROJECT, status=CommittedStatus.ACTIVE, valid_from=now,
                ),
                CommittedMemory(
                    id=expired_id, user_id=user_id, memory_type=MemoryType.FACT, title="expired", body="expired",
                    confidence=0.9, importance=0.8, sensitivity=SensitivityLevel.NORMAL,
                    visibility_scope=VisibilityScope.PROJECT, status=CommittedStatus.ACTIVE,
                    valid_from=now, valid_until=now,
                ),
            ])
            await session.commit()
            engine = RetrievalEngine(session)
            result = await session.execute(
                select(CommittedMemory.id).where(engine._build_filter(user_id, None, "work_context"))
            )
            assert set(result.scalars()) == {active_id}
            await session.execute(delete(CommittedMemory).where(CommittedMemory.user_id == user_id))
            await session.commit()

    asyncio.run(run())


def test_no_textual_match_returns_no_importance_ranked_memories() -> None:
    async def run() -> None:
        await init_db()
        user_id = f"abstention-policy-user-{uuid4().hex}"
        async with async_session() as session:
            session.add(
                CommittedMemory(
                    id=f"unrelated-{uuid4().hex}", user_id=user_id, memory_type=MemoryType.FACT,
                    title="unrelated topic", body="an unrelated retained memory", confidence=0.9,
                    importance=1.0, sensitivity=SensitivityLevel.NORMAL,
                    visibility_scope=VisibilityScope.PROJECT, status=CommittedStatus.ACTIVE,
                    valid_from=datetime.now(timezone.utc),
                )
            )
            await session.commit()
            memories, scores = await RetrievalEngine(session)._simple_text_search(
                "完全不相干的检索问题", user_id, None, "work_context", 5
            )
            assert memories == []
            assert scores == []
            await session.execute(delete(CommittedMemory).where(CommittedMemory.user_id == user_id))
            await session.commit()

    asyncio.run(run())
