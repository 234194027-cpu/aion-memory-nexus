"""Gen 3 Orchestration 测试套件 (Multi-Agent + Simulation + Tool Permission)."""

from __future__ import annotations

import asyncio
import json
import secrets
from datetime import datetime, timezone
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from src.shared.db.database import async_session, init_db
from src.main import app
from src.execution.models.agent_permission import AgentPermission
from src.execution.models.agent_profile import AgentProfile, AgentType, LLMProvider, RecallLevel
from src.memory.models.memory_type import MemoryType
from src.memory.models.committed_memory import CommittedMemory, CommittedStatus
from src.cognition.models.decision_record import DecisionRecord
from src.execution.models.simulation_run import SimulationRun
from src.execution.models.user import User
from src.execution.services import multi_agent_orchestrator as mao_module
from src.execution.services import simulation_engine as se_module
from src.shared.security.auth import get_password_hash
from src.shared.ids.id_generator import generate_agent_id, generate_memory_id


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


async def seed_agent(user_id: str, *, agent_name: str = "Test Agent", status: bool = True) -> str:
    agent_id = generate_agent_id()
    token = secrets.token_urlsafe(16)
    async with async_session() as session:
        agent = AgentProfile(
            id=agent_id,
            user_id=user_id,
            agent_name=agent_name,
            agent_type=AgentType.CUSTOM,
            default_recall_level=RecallLevel.WORK_CONTEXT,
            token_hash=get_password_hash(token),
            status=status,
            llm_provider=LLMProvider.QWEN,
            llm_model="qwen-turbo",
            llm_temperature=0.3,
            llm_max_tokens=1024,
            role="测试 agent",
            mission="用于 Gen 3 测试",
        )
        session.add(agent)
        await session.commit()
    return agent_id


async def seed_committed_memory(
    user_id: str,
    *,
    title: str,
    body: str,
    memory_type=MemoryType.FACT,
    importance: float = 0.7,
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


async def cleanup_user_data_extended(user_id: str) -> None:
    async with async_session() as session:
        await session.execute(delete(AgentPermission).where(AgentPermission.user_id == user_id))
        await session.execute(delete(SimulationRun).where(SimulationRun.user_id == user_id))
        await session.execute(delete(DecisionRecord).where(DecisionRecord.user_id == user_id))
        await session.execute(delete(CommittedMemory).where(CommittedMemory.user_id == user_id))
        await session.execute(delete(AgentProfile).where(AgentProfile.user_id == user_id))
        from src.memory.models.raw_event import RawEvent
        from src.cognition.models.weekly_review import WeeklyReview

        await session.execute(delete(RawEvent).where(RawEvent.user_id == user_id))
        await session.execute(delete(WeeklyReview).where(WeeklyReview.user_id == user_id))
        await session.execute(User.__table__.delete().where(User.id == user_id))
        await session.commit()


class _StubLLM:
    def __init__(self, payload=None, *, raise_exc: bool = False):
        self._raise = raise_exc
        if isinstance(payload, str):
            self._text = payload
        elif payload is None:
            self._text = "stub reply"
        else:
            self._text = json.dumps(payload, ensure_ascii=False)
        self.last_prompt: str = ""
        self.call_count: int = 0

    async def generate(self, prompt: str, *args, **kwargs):
        self.last_prompt = prompt
        self.call_count += 1
        if self._raise:
            raise RuntimeError("stub LLM failure")
        return self._text

    async def embed(self, text: str):
        return [0.0] * 16


def test_multi_agent_uses_default_agents_when_no_ids_given(monkeypatch):
    async def run():
        await init_db()
        client = TestClient(app)
        email, token = register_user(client)
        user_id = await get_user_id(email)

        a1 = await seed_agent(user_id, agent_name="A1")
        a2 = await seed_agent(user_id, agent_name="A2")

        stub = _StubLLM("这是 stub 的统一综合答案。")
        monkeypatch.setattr(mao_module, "get_llm_provider", lambda *a, **kw: stub)

        resp = client.post("/api/orchestration/multi-agent/run", headers=auth_headers(token), json={"question": "我应该换工作吗？", "max_agents": 2})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert len(body["drafts"]) == 2
        agent_ids_in_drafts = {d["agent_id"] for d in body["drafts"]}
        assert agent_ids_in_drafts == {a1, a2}
        assert body["meta"]["agent_count"] == 2
        assert body["final_advice"]
        await cleanup_user_data_extended(user_id)

    asyncio.run(run())


def test_multi_agent_subagent_failure_does_not_break_run(monkeypatch):
    async def run():
        await init_db()
        client = TestClient(app)
        email, token = register_user(client)
        user_id = await get_user_id(email)

        await seed_agent(user_id, agent_name="A1")
        await seed_agent(user_id, agent_name="A2")

        call_count = {"n": 0}

        def flaky_provider(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return _StubLLM(raise_exc=True)
            return _StubLLM("ok from second provider")

        monkeypatch.setattr(mao_module, "get_llm_provider", flaky_provider)

        resp = client.post("/api/orchestration/multi-agent/run", headers=auth_headers(token), json={"question": "test subagent failure", "max_agents": 2})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert len(body["drafts"]) == 2

        failed = [d for d in body["drafts"] if d["warnings"]]
        ok = [d for d in body["drafts"] if d["draft"]]
        assert failed
        assert ok
        assert any("subagent" in w for f in failed for w in f["warnings"])
        await cleanup_user_data_extended(user_id)

    asyncio.run(run())


def test_multi_agent_final_advice_includes_warning_when_no_agents(monkeypatch):
    async def run():
        await init_db()
        client = TestClient(app)
        email, token = register_user(client)
        user_id = await get_user_id(email)

        stub = _StubLLM("unused")
        monkeypatch.setattr(mao_module, "get_llm_provider", lambda *a, **kw: stub)

        resp = client.post("/api/orchestration/multi-agent/run", headers=auth_headers(token), json={"question": "hi"})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["drafts"] == []
        assert "no_agents_available" in body["warnings"]
        assert body["meta"]["agent_count"] == 0
        assert body["final_advice"]
        await cleanup_user_data_extended(user_id)

    asyncio.run(run())


def test_simulate_generates_baseline_and_counterfactual(monkeypatch):
    async def run():
        await init_db()
        client = TestClient(app)
        email, token = register_user(client)
        user_id = await get_user_id(email)

        await seed_committed_memory(user_id, title="职业选择", body="用户之前考虑过转行做产品经理。", memory_type=MemoryType.DECISION, importance=0.85)
        await seed_committed_memory(user_id, title="用户偏好", body="用户偏好稳定的工作节奏。", memory_type=MemoryType.PREFERENCE, importance=0.6)

        payload = {"counterfactual": "如果用户当初没去创业而是继续做工程师", "outcome": "现在可能在某大厂做到 P7, 收入更稳定, 但创业想法未实现。", "confidence": 0.62}
        stub = _StubLLM(payload)
        monkeypatch.setattr(se_module, "get_llm_provider", lambda *a, **kw: stub)

        resp = client.post("/api/orchestration/simulate", headers=auth_headers(token), json={"question": "如果当初没去创业而是继续做工程师会怎样", "horizon_days": 90})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["baseline"]
        assert body["counterfactual"]
        assert body["predicted_outcome"]
        assert body["horizon_days"] == 90
        assert body["run_id"] is not None
        await cleanup_user_data_extended(user_id)

    asyncio.run(run())


def test_simulate_persists_run_when_dry_run_false(monkeypatch):
    async def run():
        await init_db()
        client = TestClient(app)
        email, token = register_user(client)
        user_id = await get_user_id(email)

        payload = {"counterfactual": "如果当初选了 A 方案", "outcome": "可能更快进入稳定期, 但少了多样性。", "confidence": 0.5}
        stub = _StubLLM(payload)
        monkeypatch.setattr(se_module, "get_llm_provider", lambda *a, **kw: stub)

        resp = client.post("/api/orchestration/simulate", headers=auth_headers(token), json={"question": "如果当初选了 A 方案会怎样"})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["run_id"] is not None

        async with async_session() as session:
            row = (await session.execute(select(SimulationRun).where(SimulationRun.id == body["run_id"]))).scalar_one_or_none()
        assert row is not None
        assert row.user_id == user_id
        assert row.question == "如果当初选了 A 方案会怎样"
        await cleanup_user_data_extended(user_id)

    asyncio.run(run())


def test_simulate_returns_low_confidence_on_llm_failure(monkeypatch):
    async def run():
        await init_db()
        client = TestClient(app)
        email, token = register_user(client)
        user_id = await get_user_id(email)

        stub = _StubLLM(raise_exc=True)
        monkeypatch.setattr(se_module, "get_llm_provider", lambda *a, **kw: stub)

        resp = client.post("/api/orchestration/simulate", headers=auth_headers(token), json={"question": "如果当初..."})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["confidence"] <= 0.2
        assert "模拟失败" in body["counterfactual"]
        assert any("llm_failure" in w for w in body["warnings"])
        await cleanup_user_data_extended(user_id)

    asyncio.run(run())


def test_grant_permission_persists_record():
    async def run():
        await init_db()
        client = TestClient(app)
        email, token = register_user(client)
        user_id = await get_user_id(email)

        agent_id = await seed_agent(user_id, agent_name="Grant Agent")

        resp = client.post(
            "/api/orchestration/permissions",
            headers=auth_headers(token),
            json={"agent_id": agent_id, "tool_name": "read_memory", "scope": "allow"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["agent_id"] == agent_id
        assert body["tool_name"] == "read_memory"
        assert body["scope"] == "allow"
        assert body["id"].startswith("perm_")

        list_resp = client.get(f"/api/orchestration/permissions?agent_id={agent_id}", headers=auth_headers(token))
        assert list_resp.status_code == 200, list_resp.text
        list_body = list_resp.json()
        tool_names = [p["tool_name"] for p in list_body["permissions"]]
        assert "read_memory" in tool_names
        await cleanup_user_data_extended(user_id)

    asyncio.run(run())


def test_revoke_permission_removes_record():
    async def run():
        await init_db()
        client = TestClient(app)
        email, token = register_user(client)
        user_id = await get_user_id(email)

        agent_id = await seed_agent(user_id, agent_name="Revoke Agent")

        client.post("/api/orchestration/permissions", headers=auth_headers(token), json={"agent_id": agent_id, "tool_name": "add_memory", "scope": "allow"})

        del_resp = client.request("DELETE", "/api/orchestration/permissions", headers=auth_headers(token), params={"agent_id": agent_id, "tool_name": "add_memory"})
        assert del_resp.status_code == 204, del_resp.text

        list_resp = client.get(f"/api/orchestration/permissions?agent_id={agent_id}", headers=auth_headers(token))
        assert list_resp.status_code == 200, list_resp.text
        tool_names = [p["tool_name"] for p in list_resp.json()["permissions"]]
        assert "add_memory" not in tool_names

        del_resp2 = client.request("DELETE", "/api/orchestration/permissions", headers=auth_headers(token), params={"agent_id": agent_id, "tool_name": "add_memory"})
        assert del_resp2.status_code == 204
        await cleanup_user_data_extended(user_id)

    asyncio.run(run())


def test_check_permission_defaults_to_deny():
    async def run():
        await init_db()
        client = TestClient(app)
        email, token = register_user(client)
        user_id = await get_user_id(email)

        agent_id = await seed_agent(user_id, agent_name="Check Agent")

        check_resp = client.post("/api/orchestration/permissions/check", headers=auth_headers(token), json={"agent_id": agent_id, "tool_name": "execute_code"})
        assert check_resp.status_code == 200, check_resp.text
        body = check_resp.json()
        assert body["allowed"] is False
        assert body["source"] == "default_deny"

        client.post("/api/orchestration/permissions", headers=auth_headers(token), json={"agent_id": agent_id, "tool_name": "execute_code", "scope": "allow"})
        check_resp2 = client.post("/api/orchestration/permissions/check", headers=auth_headers(token), json={"agent_id": agent_id, "tool_name": "execute_code"})
        assert check_resp2.status_code == 200, check_resp2.text
        body2 = check_resp2.json()
        assert body2["allowed"] is True
        assert body2["source"] == "explicit_allow"

        client.post("/api/orchestration/permissions", headers=auth_headers(token), json={"agent_id": agent_id, "tool_name": "delete_memory", "scope": "deny"})
        check_resp3 = client.post("/api/orchestration/permissions/check", headers=auth_headers(token), json={"agent_id": agent_id, "tool_name": "delete_memory"})
        assert check_resp3.status_code == 200, check_resp3.text
        body3 = check_resp3.json()
        assert body3["allowed"] is False
        assert body3["source"] == "explicit_deny"
        await cleanup_user_data_extended(user_id)

    asyncio.run(run())


def test_list_tools_handles_db_backed_tools():
    async def run():
        await init_db()
        client = TestClient(app)
        email, token = register_user(client)
        user_id = await get_user_id(email)

        resp = client.get("/api/orchestration/tools", headers=auth_headers(token))
        assert resp.status_code == 200, resp.text
        tool_names = {tool["name"] for tool in resp.json()["tools"]}
        assert {"read_memory", "manage_task", "execute_code", "read_file"} <= tool_names

        await cleanup_user_data_extended(user_id)

    asyncio.run(run())


def test_orchestration_endpoints_require_auth():
    async def run():
        await init_db()
        client = TestClient(app)

        cases = [
            ("POST", "/api/orchestration/multi-agent/run", {"question": "x"}),
            ("GET", "/api/orchestration/simulations", None),
            ("POST", "/api/orchestration/simulate", {"question": "x"}),
            ("GET", "/api/orchestration/simulations/sim_does_not_exist", None),
            ("POST", "/api/orchestration/permissions", {"agent_id": "x", "tool_name": "read_memory", "scope": "allow"}),
            ("DELETE", "/api/orchestration/permissions", None),
            ("GET", "/api/orchestration/permissions", None),
            ("POST", "/api/orchestration/permissions/check", {"agent_id": "x", "tool_name": "read_memory"}),
        ]
        for method, endpoint, body in cases:
            if method == "POST":
                resp = client.post(endpoint, json=body or {})
            elif method == "DELETE":
                resp = client.request("DELETE", endpoint, params=body or {})
            else:
                resp = client.get(endpoint)
            assert resp.status_code == 401, f"{method} {endpoint} 无 token 应返回 401, 实际 {resp.status_code}: {resp.text}"

    asyncio.run(run())
