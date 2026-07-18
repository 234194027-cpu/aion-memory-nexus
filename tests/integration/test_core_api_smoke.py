"""Core API smoke checks for deploy readiness."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from src.main import app
from src.execution.models.user import User
from src.memory.models.committed_memory import CommittedMemory, CommittedStatus
from src.memory.models.memory_type import MemoryType
from src.memory.models.raw_event import SensitivityLevel, VisibilityScope
from src.shared.config import settings
from src.shared.db.database import async_session, init_db
from src.shared.ids.id_generator import generate_memory_id


def _auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _get_user_id(email: str) -> str:
    async with async_session() as session:
        result = await session.execute(select(User).where(User.email == email))
        return result.scalar_one().id


async def _cleanup_user(user_id: str) -> None:
    async with async_session() as session:
        await session.execute(delete(CommittedMemory).where(CommittedMemory.user_id == user_id))
        await session.execute(delete(User).where(User.id == user_id))
        await session.commit()


async def _seed_memory(user_id: str) -> str:
    memory_id = generate_memory_id()
    async with async_session() as session:
        session.add(
            CommittedMemory(
                id=memory_id,
                user_id=user_id,
                memory_type=MemoryType.FACT,
                title="Smoke API memory",
                body="Smoke test verifies authenticated search returns persisted memory.",
                confidence=0.9,
                importance=0.8,
                sensitivity=SensitivityLevel.NORMAL,
                visibility_scope=VisibilityScope.PROJECT,
                status=CommittedStatus.ACTIVE,
                valid_from=datetime.now(timezone.utc),
            )
        )
        await session.commit()
    return memory_id


def test_core_api_smoke_register_event_memory_system_health():
    async def run():
        await init_db()
        client = TestClient(app)

        email = f"smoke-{uuid4().hex}@example.com"
        password = "test123456"
        register = client.post("/api/auth/register", json={"email": email, "password": password})
        assert register.status_code == 200, register.text
        token = register.json()["access_token"]
        headers = _auth_headers(token)
        user_id = await _get_user_id(email)

        me = client.get("/api/auth/me", headers=headers)
        assert me.status_code == 200, me.text
        assert me.json()["email"] == email

        unauth_events = client.get("/api/events/")
        if settings.SOLO_MODE:
            assert unauth_events.status_code == 200, unauth_events.text
        else:
            assert unauth_events.status_code in {401, 403}

        event = client.post(
            "/api/events/",
            headers=headers,
            json={
                "source_type": "manual",
                "content": "Smoke event content for deploy readiness.",
                "sensitivity": "normal",
                "visibility_scope": "project",
            },
        )
        assert event.status_code == 200, event.text
        event_id = event.json()["event_id"]

        events = client.get("/api/events/", headers=headers)
        assert events.status_code == 200, events.text
        events_data = events.json()
        events_items = events_data["items"] if isinstance(events_data, dict) else events_data
        assert any(item["id"] == event_id for item in events_items)

        memory_id = await _seed_memory(user_id)
        search = client.post(
            "/api/memory/search",
            headers=headers,
            json={"query": "Smoke API memory", "top_k": 10},
        )
        assert search.status_code == 200, search.text
        assert any(item["id"] == memory_id for item in search.json()["memories"])

        stats = client.get("/api/system/stats", headers=headers)
        assert stats.status_code == 200, stats.text
        stats_body = stats.json()
        assert stats_body["memory_count"] >= 1
        assert stats_body["today_memory_count"] >= 1
        assert "candidate_count" not in stats_body
        assert "pending_candidate_count" not in stats_body

        health = client.get("/api/system/health")
        assert health.status_code == 200, health.text
        assert health.json()["status"] in {"healthy", "degraded"}

        await _cleanup_user(user_id)

    asyncio.run(run())
