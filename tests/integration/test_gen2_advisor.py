"""Gen 2 Cognitive Advisor 测试套件 (Decision Tracker + Advisor Engine + Weekly Review)。"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from src.shared.db.database import async_session, init_db
from src.main import app
from src.memory.models.committed_memory import CommittedMemory, CommittedStatus
from src.cognition.models.decision_record import DecisionRecord
from src.execution.models.user import User
from src.cognition.models.weekly_review import WeeklyReview
from src.cognition.services import advisor_engine as advisor_module
from src.cognition.services import weekly_review as weekly_review_module
from src.shared.ids.id_generator import generate_memory_id


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


async def seed_committed_memory(
    user_id: str,
    *,
    title: str,
    body: str,
    memory_type,
    importance: float = 0.85,
    project_id: str | None = None,
) -> str:
    memory_id = generate_memory_id()
    valid_from = datetime.now(timezone.utc)
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
            status=CommittedStatus.ACTIVE,
            valid_from=valid_from,
        )
        session.add(memory)
        await session.commit()
    return memory_id


async def cleanup_user_data(user_id: str) -> None:
    async with async_session() as session:
        await session.execute(delete(DecisionRecord).where(DecisionRecord.user_id == user_id))
        await session.execute(delete(WeeklyReview).where(WeeklyReview.user_id == user_id))
        await session.commit()

        from src.memory.models.raw_event import RawEvent

        await session.execute(delete(CommittedMemory).where(CommittedMemory.user_id == user_id))
        await session.execute(delete(RawEvent).where(RawEvent.user_id == user_id))
        await session.commit()
        await session.execute(User.__table__.delete().where(User.id == user_id))
        await session.commit()


class _StubLLM:
    def __init__(self, payload: dict | str):
        if isinstance(payload, str):
            self._text = payload
        else:
            self._text = json.dumps(payload, ensure_ascii=False)
        self.last_prompt: str = ""

    async def generate(self, prompt: str, *args, **kwargs):
        self.last_prompt = prompt
        return self._text

    async def embed(self, text: str):
        return [0.0] * 16


def test_track_decision_creates_decision_record():
    async def run():
        await init_db()
        client = TestClient(app)
        email, token = register_user(client)
        user_id = await get_user_id(email)

        resp = client.post(
            "/api/advisor/decisions/track",
            headers=auth_headers(token),
            json={
                "title": "数据库选型: SQLite",
                "context": "Gen 1 启动阶段, 决定默认存储方案。",
                "decision": "采用 SQLite 作为默认数据库。",
                "rationale": "降低本地启动成本, 单机可跑通, 适合个人记忆库。",
                "expected_outcome": "单机能直接启动系统, 性能满足百级记忆规模。",
                "project_id": "gen2-test",
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["title"] == "数据库选型: SQLite"
        assert body["status"] == "open"
        assert body["user_id"] == user_id
        assert body["id"].startswith("dec_")

        async with async_session() as session:
            record = (
                await session.execute(select(DecisionRecord).where(DecisionRecord.id == body["id"]))
            ).scalar_one()
        assert record.decision.startswith("采用 SQLite")
        assert record.project_id == "gen2-test"
        await cleanup_user_data(user_id)

    asyncio.run(run())


def test_track_decision_links_to_memory():
    async def run():
        await init_db()
        client = TestClient(app)
        email, token = register_user(client)
        user_id = await get_user_id(email)

        from src.memory.models.memory_type import MemoryType

        memory_id = await seed_committed_memory(
            user_id,
            title="前端框架选型",
            body="前端框架选型决定",
            memory_type=MemoryType.DECISION,
            project_id="gen2-test",
        )

        resp = client.post(
            "/api/advisor/decisions/track",
            headers=auth_headers(token),
            json={
                "title": "前端框架: React",
                "context": "项目早期决定。",
                "decision": "采用 React + TypeScript。",
                "rationale": "生态成熟, 类型系统强。",
                "linked_memory_id": memory_id,
                "project_id": "gen2-test",
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["linked_memory_id"] == memory_id

        async with async_session() as session:
            record = (
                await session.execute(select(DecisionRecord).where(DecisionRecord.id == body["id"]))
            ).scalar_one()
        assert record.linked_memory_id == memory_id
        await cleanup_user_data(user_id)

    asyncio.run(run())


def test_update_outcome_changes_status():
    async def run():
        await init_db()
        client = TestClient(app)
        email, token = register_user(client)
        user_id = await get_user_id(email)

        track_resp = client.post(
            "/api/advisor/decisions/track",
            headers=auth_headers(token),
            json={"title": "测试决策", "context": "测试 context", "decision": "做 X", "rationale": "因为 Y"},
        )
        assert track_resp.status_code == 200, track_resp.text
        decision_id = track_resp.json()["id"]

        outcome_resp = client.post(
            f"/api/advisor/decisions/{decision_id}/outcome",
            headers=auth_headers(token),
            json={"actual_outcome": "实际结果: 效果不错", "status": "resolved"},
        )
        assert outcome_resp.status_code == 200, outcome_resp.text
        body = outcome_resp.json()
        assert body["status"] == "resolved"
        assert "效果不错" in body["actual_outcome"]
        assert body["resolved_at"] is not None
        assert body["review_count"] >= 1
        await cleanup_user_data(user_id)

    asyncio.run(run())


def test_list_open_decisions_filters_correctly():
    async def run():
        await init_db()
        client = TestClient(app)
        email, token = register_user(client)
        user_id = await get_user_id(email)

        track_a = client.post(
            "/api/advisor/decisions/track",
            headers=auth_headers(token),
            json={"title": "open-A", "context": "ctx", "decision": "D1", "rationale": "R1"},
        )
        track_b = client.post(
            "/api/advisor/decisions/track",
            headers=auth_headers(token),
            json={"title": "open-B", "context": "ctx", "decision": "D2", "rationale": "R2"},
        )
        assert track_a.status_code == 200
        assert track_b.status_code == 200
        decision_id_b = track_b.json()["id"]

        resolved_resp = client.post(
            f"/api/advisor/decisions/{decision_id_b}/outcome",
            headers=auth_headers(token),
            json={"actual_outcome": "已结", "status": "resolved"},
        )
        assert resolved_resp.status_code == 200

        list_resp = client.get("/api/advisor/decisions?status=open", headers=auth_headers(token))
        assert list_resp.status_code == 200, list_resp.text
        body = list_resp.json()
        titles = [d["title"] for d in body["decisions"]]
        assert "open-A" in titles
        assert "open-B" not in titles

        all_resp = client.get("/api/advisor/decisions", headers=auth_headers(token))
        all_titles = [d["title"] for d in all_resp.json()["decisions"]]
        assert "open-A" in all_titles
        assert "open-B" in all_titles
        await cleanup_user_data(user_id)

    asyncio.run(run())


def test_advisor_ask_explain_returns_text_and_meta():
    async def run():
        await init_db()
        client = TestClient(app)
        email, token = register_user(client)
        user_id = await get_user_id(email)

        stub = _StubLLM("这是 explain 模式的回答文本 (mock).")
        def monkey_payload(*args, **kwargs):
            return stub
        import src.cognition.services.advisor_engine as ae_mod

        original = ae_mod.get_llm_provider
        ae_mod.get_llm_provider = monkey_payload
        try:
            resp = client.post(
                "/api/advisor/ask",
                headers=auth_headers(token),
                json={"question": "为什么我总是拖延", "mode": "reflection", "recall_level": "work_context"},
            )
        finally:
            ae_mod.get_llm_provider = original

        assert resp.status_code == 200, resp.text
        body = resp.json()
        actual_mode = body.get("advisor_mode") or body.get("mode")
        assert actual_mode == "reflection"
        advice = body.get("answer") or body.get("advice") or ""
        assert advice
        assert "meta" in body and isinstance(body["meta"], dict)
        await cleanup_user_data(user_id)

    asyncio.run(run())


def test_advisor_ask_suggest_mode_returns_suggestions(monkeypatch):
    async def run():
        await init_db()
        client = TestClient(app)
        email, token = register_user(client)
        user_id = await get_user_id(email)

        suggestions_payload = {
            "suggestions": ["尝试 25 分钟番茄钟。", "每周日复盘上周的任务清单。"],
            "reasoning": "基于用户偏好与历史决策。",
        }
        stub = _StubLLM(suggestions_payload)
        monkeypatch.setattr(advisor_module, "get_llm_provider", lambda *a, **kw: stub)

        resp = client.post(
            "/api/advisor/ask",
            headers=auth_headers(token),
            json={"question": "请给我一些提高效率的建议", "mode": "planning", "recall_level": "work_context"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        actual_mode = body.get("advisor_mode") or body.get("mode")
        assert actual_mode == "planning"
        advice = body.get("answer") or body.get("advice") or ""
        assert advice
        await cleanup_user_data(user_id)

    asyncio.run(run())


def test_auto_track_from_committed_memory_creates_decision_record():
    async def run():
        await init_db()
        client = TestClient(app)
        email, token = register_user(client)
        user_id = await get_user_id(email)

        from src.memory.models.memory_type import MemoryType

        decision_memory_id = await seed_committed_memory(
            user_id,
            title="技术栈决策: FastAPI",
            body="后端采用 FastAPI + SQLAlchemy 2.0 异步。",
            memory_type=MemoryType.DECISION,
            importance=0.9,
            project_id="gen2-auto-track",
        )
        fact_memory_id = await seed_committed_memory(
            user_id,
            title="事实: 今天天气",
            body="今天天气晴。",
            memory_type=MemoryType.FACT,
            importance=0.3,
        )

        resp = client.post(f"/api/advisor/memory/{decision_memory_id}/auto-track", headers=auth_headers(token))
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["linked_memory_id"] == decision_memory_id
        assert body["status"] == "open"

        async with async_session() as session:
            count = (
                await session.execute(select(DecisionRecord).where(DecisionRecord.linked_memory_id == decision_memory_id))
            ).scalars().all()
        assert len(count) == 1

        resp2 = client.post(f"/api/advisor/memory/{decision_memory_id}/auto-track", headers=auth_headers(token))
        assert resp2.status_code == 200, resp2.text
        body2 = resp2.json()
        assert body2["id"] == body["id"]

        fact_resp = client.post(f"/api/advisor/memory/{fact_memory_id}/auto-track", headers=auth_headers(token))
        assert fact_resp.status_code == 400
        await cleanup_user_data(user_id)

    asyncio.run(run())


def test_weekly_review_generate_creates_record(monkeypatch):
    async def run():
        await init_db()
        client = TestClient(app)
        email, token = register_user(client)
        user_id = await get_user_id(email)

        from src.memory.models.memory_type import MemoryType

        now = datetime.now(timezone.utc)
        last_monday = (now - timedelta(days=now.weekday() + 7)).replace(hour=0, minute=0, second=0, microsecond=0)
        week_start_str = last_monday.strftime("%Y-%m-%d")

        await seed_committed_memory(
            user_id,
            title="本周记忆 A",
            body="本周新增记忆 A 的内容",
            memory_type=MemoryType.FACT,
        )

        payload = {
            "new_memories_count": 1,
            "decisions_count": 0,
            "highlights": ["完成 Gen 2 周报接口", "接通 weekly review"],
            "open_questions": ["如何让周报更有洞察？"],
            "summary": "本周主要推进了 Gen 2 的周报与决策跟踪能力。整体节奏稳定, 下周需要补充更多用户记忆。",
        }
        stub = _StubLLM(payload)
        monkeypatch.setattr(weekly_review_module, "get_llm_provider", lambda *a, **kw: stub)

        resp = client.post(
            "/api/advisor/review/run",
            headers=auth_headers(token),
            json={"week_start": week_start_str, "dry_run": False},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["persisted"] is True
        assert body["week_start"] == week_start_str
        assert body["highlights"]
        assert body["summary"]

        async with async_session() as session:
            record = (
                await session.execute(select(WeeklyReview).where(WeeklyReview.id == body["id"], WeeklyReview.user_id == user_id))
            ).scalar_one_or_none()
        assert record is not None
        assert record.summary == body["summary"]
        assert record.week_start == week_start_str
        await cleanup_user_data(user_id)

    asyncio.run(run())


def test_weekly_review_latest_returns_most_recent():
    async def run():
        await init_db()
        client = TestClient(app)
        email, token = register_user(client)
        user_id = await get_user_id(email)

        now = datetime.now(timezone.utc)
        async with async_session() as session:
            older = WeeklyReview(
                id=f"wrv_{uuid4().hex[:16]}",
                user_id=user_id,
                week_start="2026-01-05",
                week_end="2026-01-11",
                new_memories_json="[]",
                decisions_json="[]",
                highlights_json='["旧"]',
                open_questions_json="[]",
                summary="旧周报",
                word_count=4,
                created_at=now - timedelta(days=10),
            )
            newer = WeeklyReview(
                id=f"wrv_{uuid4().hex[:16]}",
                user_id=user_id,
                week_start="2026-06-22",
                week_end="2026-06-28",
                new_memories_json="[]",
                decisions_json="[]",
                highlights_json='["新"]',
                open_questions_json="[]",
                summary="新周报",
                word_count=4,
                created_at=now,
            )
            session.add_all([older, newer])
            await session.commit()

        resp = client.get("/api/advisor/review/latest", headers=auth_headers(token))
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["review"] is not None
        assert body["review"]["week_start"] == "2026-06-22"
        assert body["review"]["week_end"] == "2026-06-28"
        assert body["review"]["summary"] == "新周报"
        await cleanup_user_data(user_id)

    asyncio.run(run())


def test_advisor_endpoints_require_auth():
    async def run():
        await init_db()
        client = TestClient(app)

        for endpoint, method, body in [
            ("/api/advisor/decisions/track", "post", {"title": "x", "context": "x", "decision": "x", "rationale": "x"}),
            ("/api/advisor/ask", "post", {"question": "x"}),
            ("/api/advisor/review/run", "post", {"dry_run": True}),
            ("/api/advisor/review/latest", "get", None),
            ("/api/advisor/decisions", "get", None),
        ]:
            if method == "post":
                resp = client.post(endpoint, json=body)
            else:
                resp = client.get(endpoint)
            assert resp.status_code == 401

    asyncio.run(run())
