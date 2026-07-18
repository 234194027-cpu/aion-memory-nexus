"""Admin WeCom API regression tests."""

from __future__ import annotations

import asyncio
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from src.execution.models.user import User
from src.main import app
from src.shared.db.database import async_session, init_db


async def _cleanup_user(email: str) -> None:
    async with async_session() as session:
        user_id = await session.scalar(select(User.id).where(User.email == email))
        if user_id:
            await session.execute(delete(User).where(User.id == user_id))
            await session.commit()


def test_wecom_test_message_treats_malformed_json_as_empty_body() -> None:
    async def run() -> None:
        await init_db()
        client = TestClient(app)
        email = f"wecom-{uuid4().hex}@example.com"

        register = client.post(
            "/api/auth/register",
            json={"email": email, "password": "test123456"},
        )
        assert register.status_code == 200, register.text
        token = register.json()["access_token"]

        response = client.post(
            "/api/admin/wecom/test-message",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            content="{",
        )

        assert response.status_code == 400, response.text
        assert response.json()["detail"] == "user_id is required"
        await _cleanup_user(email)

    asyncio.run(run())
