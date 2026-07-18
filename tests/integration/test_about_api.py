"""Integration tests for /api/admin/system/about endpoint (WP-10-T02)."""
from __future__ import annotations

import asyncio
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from src.execution.models.user import User
from src.main import app
from src.shared.db.database import async_session, init_db
from src.shared.version import get_product_version


def auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def register_user(client: TestClient) -> tuple[str, str]:
    email = f"about-{uuid4().hex}@example.com"
    response = client.post(
        "/api/auth/register",
        json={"email": email, "password": "test123456"},
    )
    assert response.status_code == 200, response.text
    return email, response.json()["access_token"]


async def get_user_id(email: str) -> str:
    async with async_session() as session:
        result = await session.execute(select(User).where(User.email == email))
        return result.scalar_one().id


async def cleanup_user(user_id: str) -> None:
    async with async_session() as session:
        await session.execute(delete(User).where(User.id == user_id))
        await session.commit()


REQUIRED_FIELDS = {
    "product_name",
    "product_version",
    "api_version",
    "schema_revision",
    "build_commit",
    "built_at",
    "environment",
    "runtime_profiles",
    "release_notes",
}


def test_about_returns_200_and_complete_schema():
    async def run():
        await init_db()
        client = TestClient(app)
        email, token = register_user(client)
        headers = auth_headers(token)
        user_id = await get_user_id(email)

        try:
            resp = client.get("/api/admin/system/about", headers=headers)
            assert resp.status_code == 200, resp.text
            body = resp.json()

            # 9 个字段全部存在
            assert set(body.keys()) == REQUIRED_FIELDS, (
                f"Missing/extra fields. Got: {set(body.keys())}"
            )

            # 字段语义校验
            assert body["product_name"] == "Aion Memory Nexus · 永识中枢"
            assert body["api_version"] == "v1"
            assert isinstance(body["runtime_profiles"], list)
            assert body["runtime_profiles"] == ["conversational", "working-active"]
            # schema_revision 可为 None（未迁移），但 key 必须存在
            assert "schema_revision" in body
            if body["schema_revision"] is not None:
                assert isinstance(body["schema_revision"], str)

            # build_commit / built_at 必须是字符串（默认 'unknown'）
            assert isinstance(body["build_commit"], str)
            assert isinstance(body["built_at"], str)
            assert isinstance(body["environment"], str)

            # release_notes 是 dict 或 None
            assert body["release_notes"] is None or isinstance(
                body["release_notes"], dict
            )

            # 不返回 401/403
            assert resp.status_code != 401
            assert resp.status_code != 403
        finally:
            await cleanup_user(user_id)

    asyncio.run(run())


def test_about_product_version_matches_version_file():
    async def run():
        await init_db()
        client = TestClient(app)
        email, token = register_user(client)
        headers = auth_headers(token)
        user_id = await get_user_id(email)

        try:
            resp = client.get("/api/admin/system/about", headers=headers)
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["product_version"] == get_product_version()
        finally:
            await cleanup_user(user_id)

    asyncio.run(run())


def test_about_does_not_leak_sensitive_fields():
    """白皮书安全要求：不返回绝对路径、密钥、DSN、主机名等敏感字段。"""
    async def run():
        await init_db()
        client = TestClient(app)
        email, token = register_user(client)
        headers = auth_headers(token)
        user_id = await get_user_id(email)

        try:
            resp = client.get("/api/admin/system/about", headers=headers)
            assert resp.status_code == 200, resp.text
            body_text = resp.text.lower()

            # 不应包含敏感字段名
            forbidden_keys = {
                "secret_key",
                "secret",
                "api_key",
                "token",
                "password",
                "dsn",
                "database_url",
                "postgres_url",
                "redis_url",
                "host",
                "hostname",
                "abs_path",
                "absolute_path",
                "vault_path",
                "remote",
                "git_remote",
                "prompt",
                "system_prompt",
            }
            body = resp.json()
            leaked = forbidden_keys & set(body.keys())
            assert not leaked, f"About endpoint leaked sensitive keys: {leaked}"

            # 不应包含绝对路径标记（Windows / Unix）
            for marker in (
                "c:\\\\",
                "c:/",
                "/home/",
                "/app/",
                "/var/",
                ".env",
                "postgresql://",
                "redis://",
                "sqlite+aiosqlite",
            ):
                assert marker not in body_text, (
                    f"About endpoint leaked marker '{marker}' in response: {body_text}"
                )
        finally:
            await cleanup_user(user_id)

    asyncio.run(run())


def test_about_requires_auth():
    """未授权访问应返回 401。"""
    async def run():
        await init_db()
        client = TestClient(app)
        resp = client.get("/api/admin/system/about")
        # 未授权应该 401（或 403，取决于 get_current_user 实现）
        assert resp.status_code in (401, 403), resp.text

    asyncio.run(run())
