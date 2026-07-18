"""Admin LLM provider API integration tests."""

from __future__ import annotations

import asyncio
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from src.execution.models.custom_llm_provider import CustomLLMProvider
from src.execution.models.user import User
from src.main import app
from src.shared.db.database import async_session, init_db


def auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def register_user(client: TestClient) -> tuple[str, str]:
    email = f"llm-{uuid4().hex}@example.com"
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
        await session.execute(delete(CustomLLMProvider).where(CustomLLMProvider.user_id == user_id))
        await session.execute(delete(User).where(User.id == user_id))
        await session.commit()


def test_llm_provider_presets_and_api_key_only_save():
    async def run():
        await init_db()
        client = TestClient(app)
        email, token = register_user(client)
        headers = auth_headers(token)
        user_id = await get_user_id(email)

        presets_resp = client.get("/api/admin/custom-llm-providers/presets", headers=headers)
        assert presets_resp.status_code == 200, presets_resp.text
        presets = presets_resp.json()
        preset_keys = {item["provider_key"] for item in presets}
        assert {"deepseek", "qwen", "doubao", "xiaomi", "ollama"} <= preset_keys
        ollama_preset = next(item for item in presets if item["provider_key"] == "ollama")
        assert ollama_preset["api_format"] == "ollama"
        assert ollama_preset["requires_api_key"] is False
        assert ollama_preset["base_url_editable"] is True

        missing_key = client.post(
            "/api/admin/custom-llm-providers/test-config",
            headers=headers,
            json={
                "provider_name": "DeepSeek",
                "base_url": "https://api.deepseek.com/v1",
                "model_name": "deepseek-chat",
            },
        )
        assert missing_key.status_code == 400, missing_key.text
        assert "API Key" in missing_key.json()["detail"]

        create_resp = client.post(
            "/api/admin/custom-llm-providers/from-preset/deepseek",
            headers=headers,
            json={"api_key": "sk-test", "model_name": "deepseek-chat"},
        )
        assert create_resp.status_code == 200, create_resp.text
        assert create_resp.json()["provider_key"] == "deepseek"
        assert create_resp.json()["model_name"] == "deepseek-chat"

        update_resp = client.post(
            "/api/admin/custom-llm-providers/from-preset/deepseek",
            headers=headers,
            json={"api_key": "sk-test-2", "model_name": "deepseek-reasoner"},
        )
        assert update_resp.status_code == 200, update_resp.text
        assert update_resp.json()["model_name"] == "deepseek-reasoner"

        ollama_resp = client.post(
            "/api/admin/custom-llm-providers/from-preset/ollama",
            headers=headers,
            json={
                "base_url": "http://127.0.0.1:11434",
                "model_name": "qwen2.5",
            },
        )
        assert ollama_resp.status_code == 200, ollama_resp.text
        assert ollama_resp.json()["provider_key"] == "ollama"
        assert ollama_resp.json()["api_format"] == "ollama"
        assert ollama_resp.json()["model_name"] == "qwen2.5"

        list_resp = client.get("/api/admin/custom-llm-providers", headers=headers)
        assert list_resp.status_code == 200, list_resp.text
        deepseek_rows = [row for row in list_resp.json() if row["provider_key"] == "deepseek"]
        assert len(deepseek_rows) == 1
        assert deepseek_rows[0]["is_preset"] is True
        assert deepseek_rows[0]["status"] is True
        ollama_rows = [row for row in list_resp.json() if row["provider_key"] == "ollama"]
        assert len(ollama_rows) == 1
        assert ollama_rows[0]["api_format"] == "ollama"
        assert ollama_rows[0]["status"] is True

        await cleanup_user(user_id)

    asyncio.run(run())
