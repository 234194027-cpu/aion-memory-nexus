"""Persona API integration tests."""

from __future__ import annotations

import asyncio
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from src.cognition.models.persona_snapshot import PersonaSnapshot
from src.execution.models.user import User
from src.main import app
from src.shared.db.database import async_session, init_db


def _auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _register_user(client: TestClient) -> tuple[str, str]:
    email = f"persona-{uuid4().hex}@example.com"
    response = client.post(
        "/api/auth/register",
        json={"email": email, "password": "test123456"},
    )
    assert response.status_code == 200, response.text
    return email, response.json()["access_token"]


async def _get_user_id(email: str) -> str:
    async with async_session() as session:
        result = await session.execute(select(User).where(User.email == email))
        return result.scalar_one().id


async def _cleanup_user(user_id: str) -> None:
    async with async_session() as session:
        await session.execute(delete(PersonaSnapshot).where(PersonaSnapshot.user_id == user_id))
        await session.execute(delete(User).where(User.id == user_id))
        await session.commit()


def test_persona_missing_snapshot_returns_empty_state_for_root_and_latest():
    async def run():
        await init_db()
        client = TestClient(app)
        email, token = _register_user(client)
        user_id = await _get_user_id(email)
        headers = _auth_headers(token)

        for path in ("/api/persona", "/api/persona/", "/api/persona/latest"):
            response = client.get(path, headers=headers)
            assert response.status_code == 200, response.text
            body = response.json()
            assert body["user_id"] == user_id
            assert body["summary"] == ""
            assert body["snapshot_id"] is None
            assert body["evidence_count"] == 0

        await _cleanup_user(user_id)

    asyncio.run(run())
