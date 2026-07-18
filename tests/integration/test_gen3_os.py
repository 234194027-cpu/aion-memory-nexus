"""Gen 3 / Cognitive OS 集成验收测试套件。"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from src.shared.db.database import async_session, init_db
from src.main import app
from src.memory.models.memory_type import MemoryType
from src.memory.models.committed_memory import CommittedMemory, CommittedStatus
from src.cognition.models.decision_record import DecisionRecord
from src.execution.models.life_task import LifeTask
from src.execution.models.life_timeline_entry import LifeTimelineEntry
from src.execution.models.user import User
from src.execution.services.context_router import ContextRouter
from src.execution.services.task_system import TaskSystem
from src.shared.ids.id_generator import generate_decision_id, generate_memory_id


def register_user(client: TestClient) -> tuple[str, str]:
    email = f"gen3-{uuid4().hex}@example.com"
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


async def seed_committed_memory(
    user_id: str,
    *,
    title: str,
    body: str,
    memory_type: MemoryType = MemoryType.FACT,
    importance: float = 0.8,
    project_id: str | None = None,
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
            confidence=0.9,
            importance=importance,
            sensitivity="normal",
            visibility_scope="project",
            status=CommittedStatus.ACTIVE,
            valid_from=datetime.now(timezone.utc),
        )
        session.add(memory)
        await session.commit()
    return memory_id


async def seed_decision(user_id: str, *, title: str, decision: str = "ok") -> str:
    did = generate_decision_id()
    async with async_session() as session:
        d = DecisionRecord(
            id=did,
            user_id=user_id,
            title=title,
            context="ctx",
            decision=decision,
            rationale="r",
            status="open",
            decided_at=datetime.now(timezone.utc),
        )
        session.add(d)
        await session.commit()
    return did


async def cleanup_user_data_extended(user_id: str) -> None:
    async with async_session() as session:
        await session.execute(delete(LifeTask).where(LifeTask.user_id == user_id))
        await session.execute(delete(LifeTimelineEntry).where(LifeTimelineEntry.user_id == user_id))
        await session.execute(delete(CommittedMemory).where(CommittedMemory.user_id == user_id))
        await session.execute(delete(DecisionRecord).where(DecisionRecord.user_id == user_id))
        await session.execute(User.__table__.delete().where(User.id == user_id))
        await session.commit()


def test_context_router_heuristic_routes_store_intent():
    async def run():
        await init_db()
        client = TestClient(app)
        email, _token = register_user(client)
        user_id = await get_user_id(email)

        async with async_session() as db:
            router = ContextRouter(db)
            res = await router.route(user_id=user_id, message="帮我记住这个想法: 周末要去看展。")

        assert res["intent"] == "store", res
        assert res["recall_level"] == "full_trusted", res
        assert res["suggested_agent_type"] == "default", res
        assert res["meta"]["model"] in {"heuristic", "llm"}, res
        assert res["confidence"] > 0.0
        await cleanup_user_data_extended(user_id)

    asyncio.run(run())


def test_context_router_heuristic_routes_decision_intent():
    async def run():
        await init_db()
        client = TestClient(app)
        email, _ = register_user(client)
        user_id = await get_user_id(email)

        async with async_session() as db:
            router = ContextRouter(db)
            res = await router.route(user_id=user_id, message="我决定了: 这个项目使用 SQLite, 不再讨论。")

        assert res["intent"] == "decide", res
        assert res["recall_level"] == "personal_context", res
        assert res["suggested_agent_type"] == "cognitive_advisor", res
        await cleanup_user_data_extended(user_id)

    asyncio.run(run())


def test_context_router_picks_default_recall_when_no_match():
    async def run():
        await init_db()
        client = TestClient(app)
        email, _ = register_user(client)
        user_id = await get_user_id(email)

        async with async_session() as db:
            router = ContextRouter(db)
            res = await router.route(user_id=user_id, message="今天天气如何")

        assert res["intent"] == "ask", res
        assert res["recall_level"] == "work_context", res
        assert res["suggested_agent_type"] == "memory_curator", res
        assert res["confidence"] >= 0.0
        assert res["rationale"]
        await cleanup_user_data_extended(user_id)

    asyncio.run(run())


def test_context_router_falls_back_when_llm_unavailable(monkeypatch):
    async def run():
        await init_db()
        client = TestClient(app)
        email, _ = register_user(client)
        user_id = await get_user_id(email)

        def _boom(*args, **kwargs):
            raise RuntimeError("llm provider down for test")

        import src.execution.services.context_router as cr_module
        monkeypatch.setattr(cr_module, "get_llm_provider", _boom)

        async with async_session() as db:
            router = ContextRouter(db)
            res = await router.route(user_id=user_id, message="帮我记住今天读了 3 篇论文。")

        assert res["intent"] == "store", res
        assert res["recall_level"] == "full_trusted", res
        assert res["meta"]["model"] == "heuristic", res
        assert res["meta"]["embed_method"] == "heuristic", res
        assert res["confidence"] == 0.55, res
        await cleanup_user_data_extended(user_id)

    asyncio.run(run())


def test_create_task_persists_with_valid_status():
    async def run():
        await init_db()
        client = TestClient(app)
        email, token = register_user(client)
        user_id = await get_user_id(email)

        create_resp = client.post(
            "/api/os/tasks",
            headers=auth_headers(token),
            json={"title": "完成 Gen 3 OS 集成", "description": "context router / task / timeline", "priority": "P1", "project_id": "gen3"},
        )
        assert create_resp.status_code == 200, create_resp.text
        task = create_resp.json()
        task_id = task["id"]
        assert task["status"] == "todo"
        assert task["priority"] == "P1"
        assert task["linked_memory_ids"] == []

        r_doing = client.patch(f"/api/os/tasks/{task_id}", headers=auth_headers(token), json={"status": "doing"})
        assert r_doing.status_code == 200, r_doing.text
        assert r_doing.json()["status"] == "doing"
        assert r_doing.json()["started_at"] is not None

        r_done = client.patch(f"/api/os/tasks/{task_id}", headers=auth_headers(token), json={"status": "done"})
        assert r_done.status_code == 200, r_done.text
        body = r_done.json()
        assert body["status"] == "done"
        assert body["completed_at"] is not None
        assert body["started_at"] is not None

        list_resp = client.get("/api/os/tasks?status=done", headers=auth_headers(token))
        assert list_resp.status_code == 200
        assert any(t["id"] == task_id for t in list_resp.json()["tasks"])

        detail_resp = client.get(f"/api/os/tasks/{task_id}", headers=auth_headers(token))
        assert detail_resp.status_code == 200
        assert detail_resp.json()["id"] == task_id
        await cleanup_user_data_extended(user_id)

    asyncio.run(run())


def test_update_status_rejects_invalid_transition():
    async def run():
        await init_db()
        client = TestClient(app)
        email, token = register_user(client)
        user_id = await get_user_id(email)

        create_resp = client.post("/api/os/tasks", headers=auth_headers(token), json={"title": "T1", "priority": "P2"})
        assert create_resp.status_code == 200, create_resp.text
        tid = create_resp.json()["id"]

        assert client.patch(f"/api/os/tasks/{tid}", headers=auth_headers(token), json={"status": "doing"}).status_code == 200
        assert client.patch(f"/api/os/tasks/{tid}", headers=auth_headers(token), json={"status": "done"}).status_code == 200

        bad_resp = client.patch(f"/api/os/tasks/{tid}", headers=auth_headers(token), json={"status": "doing"})
        assert bad_resp.status_code == 400, bad_resp.text
        assert "illegal" in bad_resp.json()["detail"].lower()

        async with async_session() as db:
            svc = TaskSystem(db)
            try:
                await svc.update_status(user_id, tid, "doing")
                raise AssertionError("expected ValueError")
            except ValueError as e:
                assert "illegal" in str(e).lower()

        await cleanup_user_data_extended(user_id)

    asyncio.run(run())


def test_link_task_to_memory_records_relation():
    async def run():
        await init_db()
        client = TestClient(app)
        email, token = register_user(client)
        user_id = await get_user_id(email)

        mem_id = await seed_committed_memory(user_id, title="重要资料", body="要读的书单", memory_type=MemoryType.FACT)

        create_resp = client.post("/api/os/tasks", headers=auth_headers(token), json={"title": "读完书单", "priority": "P1"})
        assert create_resp.status_code == 200, create_resp.text
        tid = create_resp.json()["id"]

        link_resp = client.post(f"/api/os/tasks/{tid}/link-memory/{mem_id}", headers=auth_headers(token))
        assert link_resp.status_code == 200, link_resp.text
        linked = link_resp.json()["linked_memory_ids"]
        assert mem_id in linked, linked

        bad_link = client.post(f"/api/os/tasks/{tid}/link-memory/mem_does_not_exist", headers=auth_headers(token))
        assert bad_link.status_code == 404, bad_link.text
        await cleanup_user_data_extended(user_id)

    asyncio.run(run())


def test_task_owner_isolation():
    async def run():
        await init_db()
        client = TestClient(app)
        email_a, token_a = register_user(client)
        email_b, token_b = register_user(client)
        user_a_id = await get_user_id(email_a)
        user_b_id = await get_user_id(email_b)
        assert user_a_id != user_b_id

        create_resp = client.post("/api/os/tasks", headers=auth_headers(token_b), json={"title": "B 私有任务", "priority": "P2"})
        assert create_resp.status_code == 200, create_resp.text
        b_task_id = create_resp.json()["id"]

        a_view = client.get(f"/api/os/tasks/{b_task_id}", headers=auth_headers(token_a))
        assert a_view.status_code == 403, a_view.text

        a_list = client.get("/api/os/tasks", headers=auth_headers(token_a))
        assert a_list.status_code == 200
        ids_a = [t["id"] for t in a_list.json()["tasks"]]
        assert b_task_id not in ids_a

        b_list = client.get("/api/os/tasks", headers=auth_headers(token_b))
        assert b_list.status_code == 200
        ids_b = [t["id"] for t in b_list.json()["tasks"]]
        assert b_task_id in ids_b

        await cleanup_user_data_extended(user_a_id)
        await cleanup_user_data_extended(user_b_id)

    asyncio.run(run())


def test_timeline_rebuild_groups_by_date():
    async def run():
        await init_db()
        client = TestClient(app)
        email, token = register_user(client)
        user_id = await get_user_id(email)

        await seed_committed_memory(user_id, title="M1", body="b1", memory_type=MemoryType.FACT)
        await seed_committed_memory(user_id, title="M2", body="b2", memory_type=MemoryType.DECISION, importance=0.95)
        await seed_decision(user_id, title="D1")
        create_resp = client.post("/api/os/tasks", headers=auth_headers(token), json={"title": "T1", "priority": "P2"})
        assert create_resp.status_code == 200, create_resp.text

        rebuild_resp = client.post("/api/os/timeline/rebuild", headers=auth_headers(token), json={})
        assert rebuild_resp.status_code == 200, rebuild_resp.text
        body = rebuild_resp.json()
        assert body["user_id"] == user_id
        assert body["entry_count"] >= 4
        assert isinstance(body["by_date"], dict)
        assert len(body["by_date"]) >= 1
        for d, entries in body["by_date"].items():
            assert isinstance(entries, list)
            assert all(e["entry_date"] == d for e in entries)
        assert isinstance(body["highlights"], list)
        before_count = body["entry_count"]
        rebuild_again = client.post("/api/os/timeline/rebuild", headers=auth_headers(token), json={})
        assert rebuild_again.status_code == 200
        assert rebuild_again.json()["entry_count"] == before_count
        await cleanup_user_data_extended(user_id)

    asyncio.run(run())


def test_timeline_get_timeline_returns_recent_entries_desc():
    async def run():
        await init_db()
        client = TestClient(app)
        email, token = register_user(client)
        user_id = await get_user_id(email)

        for i in range(3):
            await seed_committed_memory(user_id, title=f"M{i}", body=f"body {i}", memory_type=MemoryType.FACT)

        rebuild_resp = client.post("/api/os/timeline/rebuild", headers=auth_headers(token), json={})
        assert rebuild_resp.status_code == 200, rebuild_resp.text

        list_resp = client.get("/api/os/timeline?limit=10", headers=auth_headers(token))
        assert list_resp.status_code == 200, list_resp.text
        entries = list_resp.json()["entries"]
        assert list_resp.json()["total"] == len(entries)
        assert len(entries) >= 3

        dates = [e["entry_date"] for e in entries]
        assert dates == sorted(dates, reverse=True), dates

        mem_only = client.get("/api/os/timeline?kind=memory&limit=10", headers=auth_headers(token))
        assert mem_only.status_code == 200
        assert all(e["entry_kind"] == "memory" for e in mem_only.json()["entries"])

        email2, token2 = register_user(client)
        user2_id = await get_user_id(email2)
        r2 = client.post("/api/os/timeline/rebuild", headers=auth_headers(token2), json={})
        assert r2.status_code == 200
        b2 = r2.json()
        assert b2["entry_count"] == 0
        assert b2["by_date"] == {}
        assert b2["highlights"] == []

        await cleanup_user_data_extended(user_id)
        await cleanup_user_data_extended(user2_id)

    asyncio.run(run())


def test_auto_extract_tasks_idempotent_no_duplicates(monkeypatch):
    async def run():
        await init_db()
        client = TestClient(app)
        email, _ = register_user(client)
        user_id = await get_user_id(email)

        mem_id = await seed_committed_memory(
            user_id,
            title="research project: read Transformer paper",
            body="next week finish reading Attention Is All You Need and take notes.",
            memory_type=MemoryType.TASK,
        )

        class _IdempotentProvider:
            async def embed(self, text):
                return None

            async def generate(self, prompt, *a, **kw):
                return (
                    "[\n"
                    "  {\"title\": \"finish transformer paper and notes\",\n"
                    "   \"description\": \"next week reading Attention Is All You Need\",\n"
                    "   \"priority\": \"P1\",\n"
                    f"   \"linked_memory_ids\": [\"{mem_id}\"],\n"
                    "   \"linked_decision_ids\": []\n"
                    "  }\n"
                    "]"
                )

        import src.execution.services.task_system as task_module
        monkeypatch.setattr(task_module, "get_llm_provider", lambda *a, **kw: _IdempotentProvider())

        async with async_session() as db:
            ts = TaskSystem(db)
            first = await ts.auto_extract_tasks_from_recent_memories(user_id=user_id, days=30, limit=10)
            second = await ts.auto_extract_tasks_from_recent_memories(user_id=user_id, days=30, limit=10)

        assert len(first) == 1, f"first run should create 1 task, got {len(first)}"
        assert len(second) == 0, f"second run should not create duplicates, got {second}"
        await cleanup_user_data_extended(user_id)

    asyncio.run(run())
