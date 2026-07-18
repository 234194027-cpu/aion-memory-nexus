"""Gen 2 集成验收测试套件 — Conflict / Dedup / Rewriter。"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from src.shared.db.database import async_session, init_db
from src.main import app
import src.memory.services.retrieval_engine as retrieval_module

from src.memory.models.memory_type import MemoryType
from src.memory.models.committed_memory import CommittedMemory, CommittedStatus
from src.memory.models.memory_embedding import MemoryEmbedding
from src.memory.models.memory_source import MemorySource
from src.memory.models.raw_event import (
    ProcessingStatus,
    RawEvent,
    SensitivityLevel,
    SourceType,
    VisibilityScope,
)
from src.execution.models.user import User
from src.execution.models.memory_relation import MemoryRelation
from src.shared.utils.hash import compute_content_hash
from src.shared.ids.id_generator import generate_event_id, generate_memory_id, generate_source_id


def register_user(client: TestClient) -> tuple[str, str]:
    email = f"gen2-{uuid4().hex}@example.com"
    password = "test123456"
    response = client.post("/api/auth/register", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return email, response.json()["access_token"]


def auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def get_user_id(email: str) -> str:
    async with async_session() as session:
        result = await session.execute(select(User).where(User.email == email))
        return result.scalar_one().id


async def seed_event(user_id: str, content: str, *, project_id: str | None = None) -> str:
    event_id = generate_event_id()
    async with async_session() as session:
        event = RawEvent(
            id=event_id,
            source_type=SourceType.MANUAL,
            user_id=user_id,
            project_id=project_id,
            content=content,
            content_hash=compute_content_hash(content),
            occurred_at=datetime.now(timezone.utc),
            sensitivity=SensitivityLevel.NORMAL,
            visibility_scope=VisibilityScope.PROJECT,
            processing_status=ProcessingStatus.COMPLETED,
        )
        session.add(event)
        await session.commit()
    return event_id


async def seed_committed_memory(
    user_id: str,
    *,
    title: str,
    body: str,
    memory_type: MemoryType = MemoryType.FACT,
    importance: float = 0.8,
    confidence: float = 0.9,
    sensitivity: SensitivityLevel = SensitivityLevel.NORMAL,
    visibility_scope: VisibilityScope = VisibilityScope.PROJECT,
    project_id: str | None = None,
    status: CommittedStatus = CommittedStatus.ACTIVE,
) -> str:
    memory_id = generate_memory_id()
    async with async_session() as session:
        memory = CommittedMemory(
            id=memory_id,
            user_id=user_id,
            project_id=project_id,
            memory_type=memory_type,
            title=title,
            body=body,
            confidence=confidence,
            importance=importance,
            sensitivity=sensitivity,
            visibility_scope=visibility_scope,
            status=status,
            valid_from=datetime.now(timezone.utc),
        )
        session.add(memory)
        await session.commit()
    return memory_id


async def attach_source(memory_id: str, event_id: str) -> None:
    async with async_session() as session:
        session.add(
            MemorySource(
                id=generate_source_id(),
                memory_id=memory_id,
                raw_event_id=event_id,
                source_type=SourceType.MANUAL,
            )
        )
        await session.commit()


async def cleanup_user_data(user_id: str) -> None:
    from src.execution.models.agent_profile import AgentProfile
    from src.execution.models.custom_llm_provider import CustomLLMProvider
    from src.memory.models.obsidian_sync_record import ObsidianSyncRecord
    from sqlalchemy import delete

    async with async_session() as session:
        memory_ids_q = select(CommittedMemory.id).where(CommittedMemory.user_id == user_id)
        await session.execute(delete(MemoryEmbedding).where(MemoryEmbedding.memory_id.in_(memory_ids_q)))
        await session.execute(delete(MemorySource).where(MemorySource.memory_id.in_(memory_ids_q)))
        await session.execute(delete(CommittedMemory).where(CommittedMemory.user_id == user_id))
        await session.execute(delete(RawEvent).where(RawEvent.user_id == user_id))
        await session.commit()

        for model in (AgentProfile, CustomLLMProvider, ObsidianSyncRecord):
            try:
                await session.execute(delete(model).where(model.user_id == user_id))
                await session.commit()
            except Exception:
                await session.rollback()

        await session.execute(User.__table__.delete().where(User.id == user_id))
        await session.commit()


def test_conflict_check_returns_structure_when_no_conflict():
    async def run():
        await init_db()
        client = TestClient(app)
        _, token = register_user(client)
        resp = client.post(
            "/api/memory/conflicts/check",
            headers=auth_headers(token),
            json={"body": "一个完全无关的新记忆条目。", "title": "新事实", "memory_type": "fact"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["has_conflict"] is False
        assert body["conflicts"] == []
        assert "similar_memories" in body
        assert "warnings" in body
        assert "checked_at" in body

    asyncio.run(run())


class _ContradictionProvider:
    async def embed(self, text):
        return None

    async def generate(self, prompt, *args, **kwargs):
        return (
            "```json\n"
            "{\n"
            "  \"conflicts\": ["
            "    {\"memory_id\":\"X\",\"title\":\"咖啡因让我清醒\",\"memory_type\":\"preference\","
            "\"severity\":\"high\",\"explanation\":\"两句话在生理反应上完全相反\","
            "\"suggested_resolution\":\"needs_user_review\"}"
            "  ],\n"
            "  \"similar_memories\": ["
            "    {\"memory_id\":\"X\",\"title\":\"咖啡因让我清醒\",\"similarity\":0.9}"
            "  ]\n"
            "}\n"
            "```"
        )


def test_conflict_check_detects_direct_contradiction(monkeypatch):
    async def run():
        await init_db()
        client = TestClient(app)
        email, _token = register_user(client)
        user_id = await get_user_id(email)

        await seed_committed_memory(
            user_id,
            title="咖啡因让我清醒",
            body="每天早上喝咖啡后整个人精神百倍, 工作效率高。",
            memory_type=MemoryType.PREFERENCE,
            importance=0.85,
        )
        await seed_committed_memory(
            user_id,
            title="我最喜欢的咖啡豆",
            body="耶加雪菲, 浅烘, 柑橘调。",
            memory_type=MemoryType.PREFERENCE,
            importance=0.6,
        )

        monkeypatch.setattr(retrieval_module, "get_llm_provider", lambda *a, **kw: _ContradictionProvider())

        from src.memory.services import conflict_checker as conflict_checker_module
        from src.memory.services.conflict_checker import ConflictChecker

        monkeypatch.setattr(conflict_checker_module, "get_llm_provider", lambda *a, **kw: _ContradictionProvider())

        async with async_session() as session:
            checker = ConflictChecker(session)
            result = await checker.check(
                user_id=user_id,
                candidate={"body": "咖啡因反而让我犯困。", "title": "咖啡因让我困"},
                recall_level="work_context",
            )

        assert result["has_conflict"] is True
        assert isinstance(result["conflicts"], list)
        assert result["conflicts"]
        first = result["conflicts"][0]
        assert first["severity"] in {"high", "medium", "low"}
        assert first["suggested_resolution"] in {"supersede_old", "merge", "keep_both", "needs_user_review"}
        assert first["explanation"]
        await cleanup_user_data(user_id)

    asyncio.run(run())


def test_conflict_check_falls_back_when_llm_unavailable(monkeypatch):
    async def run():
        await init_db()
        client = TestClient(app)
        email, _token = register_user(client)
        user_id = await get_user_id(email)

        await seed_committed_memory(
            user_id,
            title="冲突测试相关记忆",
            body="这是一段用于触发 retrieval 的文本, 包含冲突检测关键词。",
            memory_type=MemoryType.FACT,
        )

        class _BoomProvider:
            async def embed(self, text):
                return None

            async def generate(self, prompt, *a, **kw):
                raise RuntimeError("synthetic LLM down for test")

        monkeypatch.setattr(retrieval_module, "get_llm_provider", lambda *a, **kw: _BoomProvider())

        from src.memory.services.conflict_checker import ConflictChecker

        async with async_session() as session:
            checker = ConflictChecker(session)
            result = await checker.check(user_id=user_id, candidate={"body": "待检测的 candidate 内容。", "title": "candidate"})

        assert isinstance(result["conflicts"], list)
        assert "warnings" in result
        assert "similar_memories" in result
        assert result.get("degraded_only") in {True, False}
        await cleanup_user_data(user_id)

    asyncio.run(run())


def test_find_duplicates_with_high_similarity_returns_pair():
    async def run():
        await init_db()
        client = TestClient(app)
        email, _token = register_user(client)
        user_id = await get_user_id(email)

        body_text = (
            "2026 年的人生记忆系统 Gen 2 设计决定: 使用 Python 3.11 + FastAPI + "
            "SQLite 作为核心运行时, 配合 asyncpg 用于将来的 PostgreSQL 升级。"
        )
        await seed_committed_memory(user_id, title="Gen 2 运行时栈选型 (dup)", body=body_text, memory_type=MemoryType.DECISION, importance=0.9)
        await seed_committed_memory(user_id, title="Gen 2 运行时栈选型 (dup)", body=body_text, memory_type=MemoryType.DECISION, importance=0.85)

        from src.memory.services.deduplicator import MemoryDeduplicator

        async with async_session() as session:
            dedup = MemoryDeduplicator(session)
            pairs = await dedup.find_duplicates(user_id=user_id, similarity_threshold=0.85, top_k=20)

        assert isinstance(pairs, list)
        assert pairs
        top = pairs[0]
        assert top["similarity"] >= 0.85
        assert top["memory_id_a"] != top["memory_id_b"]
        assert top["suggested_action"] in {"merge", "supersede", "keep_both"}
        await cleanup_user_data(user_id)

    asyncio.run(run())


def test_merge_marks_secondary_as_superseded():
    async def run():
        await init_db()
        client = TestClient(app)
        email, _token = register_user(client)
        user_id = await get_user_id(email)

        primary_id = await seed_committed_memory(user_id, title="决策: SQLite", body="用 SQLite 作为本地默认数据库。", memory_type=MemoryType.DECISION, importance=0.9)
        secondary_id = await seed_committed_memory(user_id, title="决策: SQLite (dup)", body="用 SQLite 作为本地默认数据库。", memory_type=MemoryType.DECISION, importance=0.85)

        from src.memory.services.deduplicator import MemoryDeduplicator

        async with async_session() as session:
            dedup = MemoryDeduplicator(session)
            returned = await dedup.merge(
                primary_memory_id=primary_id,
                secondary_memory_id=secondary_id,
                merged_body="决策: SQLite (合并)\n\n用 SQLite 作为本地默认数据库。\n---\n合并自: 决策: SQLite (dup)\n用 SQLite 作为本地默认数据库。",
            )
        assert returned == primary_id

        async with async_session() as session:
            sec = (await session.execute(select(CommittedMemory).where(CommittedMemory.id == secondary_id))).scalar_one()
            pri = (await session.execute(select(CommittedMemory).where(CommittedMemory.id == primary_id))).scalar_one()

        assert sec.status == CommittedStatus.SUPERSEDED
        assert pri.status == CommittedStatus.ACTIVE
        assert "合并自" in pri.body
        await cleanup_user_data(user_id)

    asyncio.run(run())


def test_merge_preserves_primary_memory_sources():
    async def run():
        await init_db()
        client = TestClient(app)
        email, _token = register_user(client)
        user_id = await get_user_id(email)

        primary_id = await seed_committed_memory(user_id, title="primary memory", body="primary body", memory_type=MemoryType.FACT)
        secondary_id = await seed_committed_memory(user_id, title="secondary memory", body="secondary body", memory_type=MemoryType.FACT)

        primary_event = await seed_event(user_id, "primary source event")
        secondary_event = await seed_event(user_id, "secondary source event")
        await attach_source(primary_id, primary_event)
        await attach_source(secondary_id, secondary_event)

        from src.memory.services.deduplicator import MemoryDeduplicator

        async with async_session() as session:
            dedup = MemoryDeduplicator(session)
            await dedup.merge(primary_memory_id=primary_id, secondary_memory_id=secondary_id, merged_body="merged")

        async with async_session() as session:
            primary_sources = (
                await session.execute(select(MemorySource).where(MemorySource.memory_id == primary_id))
            ).scalars().all()

        event_ids = {s.raw_event_id for s in primary_sources}
        assert primary_event in event_ids
        assert secondary_event in event_ids
        await cleanup_user_data(user_id)

    asyncio.run(run())


def test_rewrite_proposes_without_modifying():
    async def run():
        await init_db()
        client = TestClient(app)
        email, _token = register_user(client)
        user_id = await get_user_id(email)

        await seed_committed_memory(user_id, title="任务: 实现登录功能", body="用 FastAPI 写一个简单的 login endpoint。", memory_type=MemoryType.TASK, importance=0.5)
        await seed_committed_memory(user_id, title="任务: 实现登录功能 (重复)", body="FastAPI 写一个 login endpoint, 这次使用 OAuth2。", memory_type=MemoryType.TASK, importance=0.5)

        before = {}
        async with async_session() as session:
            memories = (await session.execute(select(CommittedMemory).where(CommittedMemory.user_id == user_id))).scalars().all()
            for m in memories:
                before[m.id] = (m.status, m.body)

        from src.memory.services.memory_rewriter import MemoryRewriter

        async with async_session() as session:
            rewriter = MemoryRewriter(session)
            result = await rewriter.rewrite(user_id=user_id, target_types=None, max_clusters=20)

        assert result["applied"] is False
        assert "proposals" in result
        assert "generated_at" in result

        after = {}
        async with async_session() as session:
            memories = (await session.execute(select(CommittedMemory).where(CommittedMemory.user_id == user_id))).scalars().all()
            for m in memories:
                after[m.id] = (m.status, m.body)

        assert before == after
        await cleanup_user_data(user_id)

    asyncio.run(run())


def test_rewrite_proposals_have_required_fields(monkeypatch):
    async def run():
        await init_db()
        client = TestClient(app)
        email, _token = register_user(client)
        user_id = await get_user_id(email)

        id_a = await seed_committed_memory(user_id, title="事实 A", body="A 的内容", memory_type=MemoryType.FACT)
        id_b = await seed_committed_memory(user_id, title="事实 B", body="B 的内容", memory_type=MemoryType.FACT)
        id_c = await seed_committed_memory(user_id, title="事实 C", body="C 的内容", memory_type=MemoryType.FACT)

        class _ProposalProvider:
            async def embed(self, text):
                return None

            async def generate(self, prompt, *a, **kw):
                return (
                    "{\n"
                    "  \"proposals\": [\n"
                    f"    {{\"action\": \"merge\", \"memory_ids\": [\"{id_a}\", \"{id_b}\"], \"reason\": \"two facts should be merged\", \"merged_draft\": \"merged body\", \"draft_body\": null, \"memory_id\": null}},\n"
                    f"    {{\"action\": \"rewrite\", \"memory_id\": \"{id_c}\", \"reason\": \"clarify wording\", \"draft_body\": \"new body\", \"memory_ids\": null, \"merged_draft\": null}},\n"
                    f"    {{\"action\": \"archive\", \"memory_id\": \"{id_b}\", \"reason\": \"no longer relevant\", \"memory_ids\": null, \"merged_draft\": null, \"draft_body\": null}}\n"
                    "  ]\n"
                    "}"
                )

        from src.memory.services import memory_rewriter as rewriter_module
        from src.memory.services.memory_rewriter import MemoryRewriter

        monkeypatch.setattr(rewriter_module, "get_llm_provider", lambda *a, **kw: _ProposalProvider())

        async with async_session() as session:
            rewriter = MemoryRewriter(session)
            result = await rewriter.rewrite(user_id=user_id, target_types=None, max_clusters=20)

        proposals = result.get("proposals") or []
        assert proposals
        for p in proposals:
            assert p.get("action") in {"merge", "rewrite", "archive"}
            assert p.get("reason")

        actions = {p["action"] for p in proposals}
        assert "merge" in actions
        assert "rewrite" in actions
        assert "archive" in actions
        await cleanup_user_data(user_id)

    asyncio.run(run())


def test_apply_rewrite_increments_rewritten_count():
    async def run():
        await init_db()
        client = TestClient(app)
        email, _token = register_user(client)
        user_id = await get_user_id(email)

        target_id = await seed_committed_memory(user_id, title="待重写", body="原始 body", memory_type=MemoryType.FACT, importance=0.6)

        from src.memory.services.memory_rewriter import MemoryRewriter

        async with async_session() as session:
            rewriter = MemoryRewriter(session)
            proposals = [{"action": "rewrite", "memory_id": target_id, "reason": "improve wording", "draft_body": "改写后的 body, 表达更清晰。"}]
            result = await rewriter.apply_proposals(user_id=user_id, proposals=proposals)

        assert result["applied_count"] >= 1
        assert result["failed"] == []

        async with async_session() as session:
            mem = (await session.execute(select(CommittedMemory).where(CommittedMemory.id == target_id))).scalar_one()
        assert mem.status == CommittedStatus.ACTIVE
        assert mem.body == "改写后的 body, 表达更清晰。"
        await cleanup_user_data(user_id)

    asyncio.run(run())


def test_rewriter_rejects_cross_user_relation_proposal():
    async def run():
        await init_db()
        client = TestClient(app)
        owner_email, _owner_token = register_user(client)
        other_email, _other_token = register_user(client)
        owner_id = await get_user_id(owner_email)
        other_id = await get_user_id(other_email)
        owner_memory_id = await seed_committed_memory(
            owner_id, title="owner", body="owner body"
        )
        other_memory_id = await seed_committed_memory(
            other_id, title="other", body="other body"
        )

        from src.memory.services.memory_rewriter import MemoryRewriter

        async with async_session() as session:
            result = await MemoryRewriter(session).apply_proposals(
                user_id=owner_id,
                proposals=[{
                    "action": "link",
                    "memory_ids": [owner_memory_id, other_memory_id],
                    "relation_type": "supports",
                    "reason": "must not cross ownership boundary",
                }],
            )
            assert result["applied_count"] == 0
            assert result["failed"][0]["reason"] == "relation_memories_not_owned"

        async with async_session() as session:
            count = await session.scalar(
                select(MemoryRelation.id).where(MemoryRelation.user_id == owner_id)
            )
            assert count is None

        await cleanup_user_data(owner_id)
        await cleanup_user_data(other_id)

    asyncio.run(run())


def test_relation_api_enforces_graph_invariants():
    async def run():
        await init_db()
        client = TestClient(app)
        email, token = register_user(client)
        user_id = await get_user_id(email)
        source_id = await seed_committed_memory(user_id, title="source", body="source body")
        target_id = await seed_committed_memory(user_id, title="target", body="target body")
        payload = {
            "source_memory_id": source_id,
            "target_memory_id": target_id,
            "relation_type": "supports",
            "confidence": 0.0,
            "valid_from": "2026-01-01T00:00:00Z",
            "valid_until": "2026-12-31T00:00:00Z",
        }

        invalid_type = client.post(
            "/api/memory/relations",
            json={**payload, "relation_type": "made_up"},
            headers=auth_headers(token),
        )
        assert invalid_type.status_code == 400
        invalid_confidence = client.post(
            "/api/memory/relations",
            json={**payload, "confidence": 1.1},
            headers=auth_headers(token),
        )
        assert invalid_confidence.status_code == 400

        first = client.post("/api/memory/relations", json=payload, headers=auth_headers(token))
        assert first.status_code == 200, first.text
        assert first.json()["confidence"] == 0.0
        assert first.json()["valid_from"]
        assert first.json()["valid_until"]
        duplicate = client.post("/api/memory/relations", json=payload, headers=auth_headers(token))
        assert duplicate.status_code == 200, duplicate.text
        assert duplicate.json()["id"] == first.json()["id"]

        async with async_session() as session:
            relation_ids = list((await session.execute(
                select(MemoryRelation.id).where(MemoryRelation.user_id == user_id)
            )).scalars())
            assert relation_ids == [first.json()["id"]]
            await session.execute(delete(MemoryRelation).where(MemoryRelation.id.in_(relation_ids)))
            await session.commit()
        await cleanup_user_data(user_id)

    asyncio.run(run())


def test_governance_endpoints_require_auth():
    async def run():
        await init_db()
        client = TestClient(app)

        for path, payload in [
            ("/api/memory/conflicts/check", {"body": "x"}),
            ("/api/memory/duplicates/find", {}),
            ("/api/memory/duplicates/merge", {"primary_memory_id": "a", "secondary_memory_id": "b"}),
            ("/api/memory/rewriter/run", {}),
            ("/api/memory/rewriter/apply", {"proposals": []}),
            ("/api/memory/hygiene/run", {}),
            ("/api/memory/hygiene/apply", {"approved": True, "suggestions": []}),
        ]:
            resp = client.post(path, json=payload)
            assert resp.status_code == 401

    asyncio.run(run())


def test_memory_conflicts_route_is_not_treated_as_memory_id():
    async def run():
        await init_db()
        client = TestClient(app)
        email, token = register_user(client)
        user_id = await get_user_id(email)

        resp = client.get("/api/memory/conflicts", headers=auth_headers(token))
        assert resp.status_code == 200, resp.text
        assert isinstance(resp.json(), list)
        assert "Memory not found" not in resp.text

        await cleanup_user_data(user_id)

    asyncio.run(run())
