"""第三代认知 OS 闭环验收测试 (Cognitive OS Final Check)。"""

import asyncio
import json
import uuid
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from src.shared.db.database import async_session, init_db
from src.main import app
from src.memory.models.memory_type import MemoryType
from src.memory.models.committed_memory import CommittedMemory, CommittedStatus
from src.cognition.models.decision_record import DecisionRecord
from src.memory.models.raw_event import SensitivityLevel, VisibilityScope
from src.shared.ids.id_generator import generate_decision_id, generate_memory_id

_c = 0


def _unique_email():
    global _c
    _c += 1
    return f"loop{_c}_{uuid.uuid4().hex[:8]}@test.com"


def _register(client):
    email = _unique_email()
    r = client.post("/api/auth/register", json={"email": email, "password": "test1234"})
    assert r.status_code == 200, r.text
    return email, r.json()["access_token"]


def _headers(token):
    return {"Authorization": f"Bearer {token}"}


async def _uid(email):
    from src.execution.models.user import User
    from sqlalchemy.future import select

    async with async_session() as db:
        r = await db.execute(select(User).where(User.email == email))
        return r.scalar_one().id


async def _cleanup(uid):
    from sqlalchemy import text

    async with async_session() as db:
        for tbl in [
            "advisor_sessions", "audit_logs", "memory_relations",
            "decision_reviews", "conflict_records", "memory_sources",
            "memory_embeddings", "simulation_runs", "persona_snapshots",
            "weekly_reviews", "life_timeline_entries", "agent_permissions",
            "life_tasks", "committed_memories",
            "raw_events", "decision_records",
        ]:
            try:
                await db.execute(text(f"DELETE FROM {tbl} WHERE user_id = :uid"), {"uid": uid})
            except Exception:
                pass
        try:
            await db.execute(text("DELETE FROM users WHERE id = :uid"), {"uid": uid})
        except Exception:
            pass
        await db.commit()


async def _seed_decision(uid, title, body, importance=0.9):
    mid = generate_memory_id()
    async with async_session() as db:
        m = CommittedMemory(
            id=mid,
            user_id=uid,
            title=title,
            body=body,
            memory_type=MemoryType.DECISION,
            importance=importance,
            confidence=0.9,
            status=CommittedStatus.ACTIVE,
            sensitivity=SensitivityLevel.NORMAL,
            visibility_scope=VisibilityScope.PROJECT,
            valid_from=datetime.now(timezone.utc),
        )
        db.add(m)
        await db.commit()
    return mid


async def _seed_memory(uid, title, body, mtype=MemoryType.FACT, importance=0.5):
    mid = generate_memory_id()
    async with async_session() as db:
        m = CommittedMemory(
            id=mid,
            user_id=uid,
            title=title,
            body=body,
            memory_type=mtype,
            importance=importance,
            confidence=0.8,
            status=CommittedStatus.ACTIVE,
            sensitivity=SensitivityLevel.NORMAL,
            visibility_scope=VisibilityScope.PROJECT,
            valid_from=datetime.now(timezone.utc),
        )
        db.add(m)
        await db.commit()
    return mid


async def _seed_decision_record(uid, title, decision, status="open"):
    did = generate_decision_id()
    async with async_session() as db:
        dr = DecisionRecord(
            id=did,
            user_id=uid,
            title=title,
            context="test context",
            decision=decision,
            rationale="test rationale",
            status=status,
        )
        db.add(dr)
        await db.commit()
    return did


class _SmartMock:
    def __init__(self, *, extra_answer="基于你的历史记忆分析如下"):
        self.extra_answer = extra_answer

    async def embed(self, text):
        return None

    async def generate(self, prompt, *a, **kw):
        p = (prompt or "").lower()

        if "人格画像" in prompt or "persona" in p:
            return json.dumps({
                "traits": {"decision_style": "分析型", "risk_profile": "中等", "thinking_mode": "系统性", "execution_style": "计划优先", "stability": "较高"},
                "trait_details": [], "behavior_patterns": ["喜欢先调研再行动"], "decision_patterns": ["倾向技术成熟方案"],
                "biases": [], "evolution_trends": ["决策速度提升"], "strengths": ["技术判断力强"], "watchouts": ["有时过度分析"],
                "summary": "技术导向的系统性思考者，决策风格稳健。", "summary_model": "稳健型技术决策者", "confidence": 0.7,
            }, ensure_ascii=False)

        if "research agent" in p or ("研究" in prompt[:300] if prompt else False):
            return json.dumps({"findings": ["系统架构使用 FastAPI", "使用 SQLite"], "gaps": ["缺少压力测试"], "sources": []}, ensure_ascii=False)

        if "planning agent" in p or ("拆解" in prompt[:300] if prompt else False):
            return json.dumps({"steps": [{"title": "分析现有架构", "description": "审查代码", "priority": "high"}, {"title": "制定改进计划", "description": "列出优化项", "priority": "medium"}], "rationale": "分步计划"}, ensure_ascii=False)

        if "critic agent" in p or ("风险" in prompt[:300] if prompt else False):
            return json.dumps({"risks": [{"risk": "SQLite并发限制", "severity": "medium", "suggestion": "迁移PG"}], "objections": ["高并发可能出问题"], "historical_lessons": ["SQLite曾遇锁竞争"]}, ensure_ascii=False)

        if "executor agent" in p or ("执行" in prompt[:300] and "步骤" in prompt[:300] if prompt else False):
            return json.dumps({"actions_taken": ["已完成架构分析"], "results": ["架构合理"], "next_steps": ["建议加缓存"]}, ensure_ascii=False)

        if "拆解" in prompt or "decompose" in p or "子任务" in prompt:
            return json.dumps([
                {"title": "子任务1: 需求分析", "description": "分析需求", "priority_score": 0.9},
                {"title": "子任务2: 架构设计", "description": "设计架构", "priority_score": 0.8},
                {"title": "子任务3: 实现核心模块", "description": "编码实现", "priority_score": 0.7},
            ], ensure_ascii=False)

        if "模拟" in prompt or "simulation" in p or "反事实" in prompt:
            return json.dumps({"baseline": "系统运行稳定", "counterfactual": "选不同技术栈", "predicted_outcome": "需更多时间但扩展性好", "risk_factors": ["迁移成本"], "risk_level": "medium"}, ensure_ascii=False)

        return json.dumps({
            "answer": self.extra_answer, "direct_recommendation": "建议继续当前方向",
            "historical_basis": [{"memory_id": "m1", "title": "历史决策", "content_snippet": "相关内容", "memory_type": "decision"}],
            "risk_points": [{"risk": "资源有限", "severity": "medium", "source": "历史模式"}],
            "conflicts_or_changes": [], "suggested_next_steps": [{"step": "继续推进", "priority": "high", "reason": "历史支持"}],
            "uncertainty": "部分信息可能不完整",
            "cited_memories": [{"memory_id": "m1", "title": "相关记忆", "relevance": "直接相关"}],
            "cited_decisions": [],
        }, ensure_ascii=False)


def test_loop1_memory_recall():
    async def run():
        await init_db()
        client = TestClient(app)
        email, token = _register(client)
        uid = await _uid(email)

        await _seed_decision(uid, "002项目决策: 微服务架构", "决定采用微服务，原因是团队规模大。预期提升开发效率。", 0.95)
        await _seed_memory(uid, "002项目进展", "微服务上线后部署复杂度增加3倍", mtype=MemoryType.INSIGHT, importance=0.8)

        import src.memory.api.memories as mem_mod
        orig = getattr(mem_mod, "get_llm_provider", None)
        try:
            mem_mod.get_llm_provider = lambda *a, **kw: _SmartMock(extra_answer="你之前决定采用微服务架构，主要因为团队规模大。但后来部署复杂度增加。")
            resp = client.post("/api/memory/ask", headers=_headers(token), json={"question": "我以前怎么做002项目决策？", "recall_level": "work_context"})
        finally:
            if orig:
                mem_mod.get_llm_provider = orig

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body.get("answer")
        assert isinstance(body.get("memories"), list)
        assert body.get("memories")
        assert 0 < body.get("confidence", 0) <= 1.0
        await _cleanup(uid)

    asyncio.run(run())


def test_loop2_decision_advisory():
    async def run():
        await init_db()
        client = TestClient(app)
        email, token = _register(client)
        uid = await _uid(email)

        await _seed_decision_record(uid, "启动002项目", "决定启动002项目")
        await _seed_decision(uid, "002技术选型", "选择 Python + FastAPI", 0.85)
        await _seed_memory(uid, "002进度", "核心模块完成但测试不足", mtype=MemoryType.INSIGHT)

        import src.cognition.services.advisor_engine as ae_mod
        orig = ae_mod.get_llm_provider
        try:
            ae_mod.get_llm_provider = lambda *a, **kw: _SmartMock(extra_answer="基于历史决策，002已投入大量精力，建议继续但补充测试。")
            resp = client.post("/api/advisor/ask", headers=_headers(token), json={"question": "我现在该不该继续002项目？", "mode": "decision"})
        finally:
            ae_mod.get_llm_provider = orig

        assert resp.status_code == 200, resp.text
        body = resp.json()
        answer = body.get("answer") or body.get("advice") or ""
        assert answer
        risks = body.get("risk_points") or body.get("risks") or []
        assert isinstance(risks, list)
        steps = body.get("suggested_next_steps") or body.get("next_steps") or []
        assert isinstance(steps, list)
        await _cleanup(uid)

    asyncio.run(run())


def test_loop3_task_generation():
    async def run():
        await init_db()
        client = TestClient(app)
        email, token = _register(client)
        uid = await _uid(email)

        create_resp = client.post("/api/os/tasks", headers=_headers(token), json={"title": "完成认知OS第三代", "description": "实现核心模块", "priority": "P0"})
        assert create_resp.status_code == 200, create_resp.text
        task_id = create_resp.json()["id"]

        import src.execution.services.task_system as ts_mod
        orig = ts_mod.get_llm_provider
        try:
            ts_mod.get_llm_provider = lambda *a, **kw: _SmartMock()
            decompose_resp = client.post(f"/api/os/tasks/{task_id}/decompose", headers=_headers(token), json={"max_sub_tasks": 3})
        finally:
            ts_mod.get_llm_provider = orig

        assert decompose_resp.status_code == 200, decompose_resp.text
        body = decompose_resp.json()
        sub_tasks = body.get("sub_tasks") or []
        assert len(sub_tasks) >= 1, f"必须生成至少1个子任务, got {body}"
        for st in sub_tasks:
            assert st.get("title")

        list_resp = client.get("/api/os/tasks", headers=_headers(token))
        assert list_resp.status_code == 200
        await _cleanup(uid)

    asyncio.run(run())


def test_loop4_agent_execution():
    async def run():
        await init_db()
        client = TestClient(app)
        email, token = _register(client)
        uid = await _uid(email)

        await _seed_memory(uid, "架构现状", "FastAPI + SQLite，单机部署")
        await _seed_decision(uid, "选择SQLite", "选择SQLite作为开发数据库", 0.8)

        import src.execution.services.multi_agent_orchestrator as ma_mod
        orig = ma_mod.get_llm_provider
        try:
            ma_mod.get_llm_provider = lambda *a, **kw: _SmartMock()
            resp = client.post("/api/orchestration/multi-agent/run", headers=_headers(token), json={
                "question": "分析当前架构问题", "execution_mode": "sequential", "max_agents": 4, "recall_level": "work_context",
            })
        finally:
            ma_mod.get_llm_provider = orig

        assert resp.status_code == 200, resp.text
        body = resp.json()
        role_outputs = body.get("role_outputs") or {}
        assert role_outputs
        assert "research" in role_outputs or "critic" in role_outputs
        assert body.get("execution_mode") == "sequential"
        await _cleanup(uid)

    asyncio.run(run())


def test_loop5_agent_memory_writeback():
    async def run():
        await init_db()
        client = TestClient(app)
        email, token = _register(client)
        uid = await _uid(email)

        await _seed_memory(uid, "项目背景", "正在开发认知操作系统")

        from sqlalchemy.future import select
        from sqlalchemy import func as sqlfunc
        async with async_session() as db:
            r = await db.execute(select(sqlfunc.count()).select_from(CommittedMemory).where(CommittedMemory.user_id == uid))
            _count_before = r.scalar() or 0

        import src.execution.services.multi_agent_orchestrator as ma_mod
        orig = ma_mod.get_llm_provider
        try:
            ma_mod.get_llm_provider = lambda *a, **kw: _SmartMock()
            resp = client.post("/api/orchestration/multi-agent/run", headers=_headers(token), json={
                "question": "分析系统架构", "execution_mode": "sequential", "writeback_to_memory": True,
            })
        finally:
            ma_mod.get_llm_provider = orig

        assert resp.status_code == 200, resp.text
        body = resp.json()
        wb = body.get("writeback_results", {})
        assert "memories_created" in wb
        assert "tasks_created" in wb

        if wb.get("memories_created"):
            async with async_session() as db:
                r = await db.execute(select(sqlfunc.count()).select_from(CommittedMemory).where(CommittedMemory.user_id == uid))
                count_after = r.scalar() or 0
                assert count_after > 0

        await _cleanup(uid)

    asyncio.run(run())


def test_loop6_persona_advisor_loop():
    async def run():
        await init_db()
        client = TestClient(app)
        email, token = _register(client)
        uid = await _uid(email)

        await _seed_decision(uid, "选择Rust做后端", "用Rust重写核心模块，追求性能", 0.9)
        await _seed_memory(uid, "技术偏好", "偏好强类型语言", mtype=MemoryType.PREFERENCE, importance=0.8)
        await _seed_memory(uid, "工作习惯", "TDD信徒，先写测试再实现", mtype=MemoryType.FACT, importance=0.7)
        await _seed_decision(uid, "拒绝NoSQL", "明确拒绝MongoDB", 0.85)

        import src.cognition.services.persona_engine as pe_mod
        orig_pe = pe_mod.get_llm_provider
        try:
            pe_mod.get_llm_provider = lambda *a, **kw: _SmartMock()
            persona_resp = client.post("/api/persona/rebuild", headers=_headers(token), json={})
        finally:
            pe_mod.get_llm_provider = orig_pe

        assert persona_resp.status_code == 200, persona_resp.text
        pb = persona_resp.json()
        assert pb.get("traits") or pb.get("summary")

        import src.cognition.services.advisor_engine as ae_mod
        orig_ae = ae_mod.get_llm_provider
        try:
            ae_mod.get_llm_provider = lambda *a, **kw: _SmartMock(extra_answer="根据你的历史决策，你是技术导向的稳健型决策者。偏好强类型、注重测试、对新技术审慎。")
            advisor_resp = client.post("/api/advisor/ask", headers=_headers(token), json={"question": "你觉得我是什么样的人？", "mode": "reflection", "recall_level": "personal_context"})
        finally:
            ae_mod.get_llm_provider = orig_ae

        assert advisor_resp.status_code == 200
        adv = advisor_resp.json()
        answer = adv.get("answer") or adv.get("advice") or ""
        assert answer

        latest_resp = client.get("/api/persona/latest", headers=_headers(token))
        assert latest_resp.status_code == 200
        assert latest_resp.json().get("summary") or latest_resp.json().get("traits")
        await _cleanup(uid)

    asyncio.run(run())


def test_loop_ultimate_full_pipeline():
    async def run():
        await init_db()
        client = TestClient(app)
        email, token = _register(client)
        uid = await _uid(email)

        await _seed_decision(uid, "启动认知OS", "决定开发第三代认知OS", 0.95)
        await _seed_memory(uid, "技术栈", "FastAPI + SQLAlchemy + SQLite")

        import src.execution.services.context_router as cr_mod
        orig_cr = cr_mod.get_llm_provider
        try:
            cr_mod.get_llm_provider = lambda *a, **kw: _SmartMock()
            route_resp = client.post("/api/os/context/route", headers=_headers(token), json={"message": "帮我分析认知OS架构"})
        finally:
            cr_mod.get_llm_provider = orig_cr

        assert route_resp.status_code == 200, route_resp.text
        route = route_resp.json()
        assert route.get("intent")
        assert "selected_memories" in route
        assert "execution_strategy" in route

        import src.execution.services.multi_agent_orchestrator as ma_mod
        orig_ma = ma_mod.get_llm_provider
        try:
            ma_mod.get_llm_provider = lambda *a, **kw: _SmartMock()
            agent_resp = client.post("/api/orchestration/multi-agent/run", headers=_headers(token), json={
                "question": "分析架构并制定改进计划", "execution_mode": "sequential", "writeback_to_memory": True,
            })
        finally:
            ma_mod.get_llm_provider = orig_ma

        assert agent_resp.status_code == 200
        ab = agent_resp.json()
        assert ab.get("role_outputs")
        assert ab.get("writeback_results")

        task_resp = client.post("/api/os/tasks", headers=_headers(token), json={"title": "优化认知OS架构", "priority": "P1"})
        assert task_resp.status_code == 200
        task_id = task_resp.json()["id"]

        import src.execution.services.task_system as ts_mod
        orig_ts = ts_mod.get_llm_provider
        try:
            ts_mod.get_llm_provider = lambda *a, **kw: _SmartMock()
            decompose_resp = client.post(f"/api/os/tasks/{task_id}/decompose", headers=_headers(token), json={"max_sub_tasks": 3})
        finally:
            ts_mod.get_llm_provider = orig_ts
        assert decompose_resp.status_code == 200

        import src.cognition.services.advisor_engine as ae_mod
        orig_ae = ae_mod.get_llm_provider
        try:
            ae_mod.get_llm_provider = lambda *a, **kw: _SmartMock(extra_answer="基于历史决策和Agent分析，建议优先解决数据库并发问题。")
            advisor_resp = client.post("/api/advisor/ask", headers=_headers(token), json={"question": "基于刚才的分析，下一步做什么？", "mode": "planning"})
        finally:
            ae_mod.get_llm_provider = orig_ae

        assert advisor_resp.status_code == 200
        assert advisor_resp.json().get("answer") or advisor_resp.json().get("advice")

        tl_resp = client.get("/api/os/timeline?limit=50", headers=_headers(token))
        assert tl_resp.status_code == 200

        from sqlalchemy.future import select
        from sqlalchemy import func as sqlfunc
        async with async_session() as db:
            r = await db.execute(select(sqlfunc.count()).select_from(CommittedMemory).where(CommittedMemory.user_id == uid))
            mem_count = r.scalar() or 0
            assert mem_count >= 2

        await _cleanup(uid)

    asyncio.run(run())
