import json
import logging
import time

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from src.shared.db.database import get_db
from src.execution.models.agent_profile import AgentProfile
from src.execution.models.custom_llm_provider import CustomLLMProvider
from src.shared.security.dependencies import get_current_user
from src.shared.ids.id_generator import generate_id
from src.shared.security.encryption import (
    decrypt_header_values,
    decrypt_value,
    encrypt_header_values,
    encrypt_value,
)
from src.shared.security.outbound_url import assert_safe_llm_endpoint

logger = logging.getLogger(__name__)

router = APIRouter()


def _provider_payload(provider: CustomLLMProvider) -> dict:
    return {
        "id": provider.id,
        "provider_name": provider.provider_name,
        "provider_key": provider.provider_key,
        "base_url": provider.base_url,
        "model_name": provider.model_name,
        "api_format": provider.api_format,
        "icon": provider.icon,
        "is_preset": provider.is_preset,
        "status": provider.status,
        "created_at": provider.created_at,
        "updated_at": provider.updated_at,
    }


def _chat_completions_url(base_url: str) -> str:
    normalized = (base_url or "").rstrip("/")
    if normalized.endswith("/chat/completions"):
        return normalized
    return f"{normalized}/chat/completions"


async def _test_openai_compatible_provider(
    *,
    provider_name: str,
    base_url: str,
    api_key: str | None,
    model_name: str | None,
    headers: dict | None = None,
) -> dict:
    if not base_url:
        raise HTTPException(status_code=400, detail="请填写 API 地址")
    if not api_key:
        raise HTTPException(status_code=400, detail="请先填写 API Key")
    if not model_name:
        raise HTTPException(status_code=400, detail="请填写模型名称")
    try:
        await assert_safe_llm_endpoint(base_url, "openai")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    request_headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    if "xiaomimimo.com" in base_url:
        request_headers["api-key"] = api_key
    if headers:
        request_headers.update(headers)

    payload = {
        "model": model_name,
        "messages": [{"role": "user", "content": "请只回复 OK"}],
        "temperature": 0,
        "max_tokens": 8,
    }
    started_at = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=20, trust_env=False) as client:
            response = await client.post(
                _chat_completions_url(base_url),
                headers=request_headers,
                json=payload,
            )
            response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        status_code = exc.response.status_code if exc.response is not None else "unknown"
        raise HTTPException(
            status_code=400,
            detail=f"{provider_name} 连接失败：HTTP {status_code}",
        ) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"{provider_name} 连接失败：{exc}",
        ) from exc

    latency_ms = int((time.perf_counter() - started_at) * 1000)
    return {
        "status": "ok",
        "message": f"{provider_name} 连接成功",
        "latency_ms": latency_ms,
    }


def _ollama_chat_url(base_url: str) -> str:
    normalized = (base_url or "").rstrip("/")
    if normalized.endswith("/api/chat"):
        return normalized
    return f"{normalized}/api/chat"


async def _test_ollama_provider(
    *,
    provider_name: str,
    base_url: str,
    model_name: str | None,
    headers: dict | None = None,
) -> dict:
    if not base_url:
        raise HTTPException(status_code=400, detail="请填写 Ollama 地址")
    if not model_name:
        raise HTTPException(status_code=400, detail="请填写模型名称")
    try:
        await assert_safe_llm_endpoint(base_url, "ollama")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    request_headers = {
        "Content-Type": "application/json",
    }
    if headers:
        request_headers.update(headers)

    payload = {
        "model": model_name,
        "messages": [{"role": "user", "content": "请只回复 OK"}],
        "stream": False,
        "options": {"temperature": 0},
    }
    started_at = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=30, trust_env=False) as client:
            response = await client.post(
                _ollama_chat_url(base_url),
                headers=request_headers,
                json=payload,
            )
            response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        status_code = exc.response.status_code if exc.response is not None else "unknown"
        raise HTTPException(
            status_code=400,
            detail=f"{provider_name} 连接失败：HTTP {status_code}",
        ) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"{provider_name} 连接失败：{exc}",
        ) from exc

    latency_ms = int((time.perf_counter() - started_at) * 1000)
    return {
        "status": "ok",
        "message": f"{provider_name} 连接成功",
        "latency_ms": latency_ms,
    }


async def _test_provider_by_format(
    *,
    provider_name: str,
    base_url: str,
    api_key: str | None,
    model_name: str | None,
    api_format: str | None = "openai",
    headers: dict | None = None,
) -> dict:
    normalized_format = (api_format or "openai").lower()
    if normalized_format == "ollama":
        return await _test_ollama_provider(
            provider_name=provider_name,
            base_url=base_url,
            model_name=model_name,
            headers=headers,
        )

    return await _test_openai_compatible_provider(
        provider_name=provider_name,
        base_url=base_url,
        api_key=api_key,
        model_name=model_name,
        headers=headers,
    )


@router.get("")
async def list_custom_llm_providers(
    db: AsyncSession = Depends(get_db),
    user = Depends(get_current_user),
):
    result = await db.execute(select(CustomLLMProvider).where(CustomLLMProvider.user_id == user.id))
    providers = result.scalars().all()
    return [_provider_payload(p) for p in providers]


@router.post("")
async def create_custom_llm_provider(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user = Depends(get_current_user),
):
    try:
        body = await request.json()
    except (ValueError, json.JSONDecodeError):
        body = {}
    
    provider_name = body.get("provider_name")
    provider_key = body.get("provider_key")
    base_url = body.get("base_url")
    api_key = body.get("api_key")
    model_name = body.get("model_name")
    api_format = body.get("api_format", "openai")
    headers = body.get("headers", {})
    
    if not provider_name or not provider_key or not base_url:
        raise HTTPException(status_code=400, detail="provider_name, provider_key, base_url 为必填项")
    try:
        await assert_safe_llm_endpoint(base_url, api_format)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    
    existing_key = await db.execute(
        select(CustomLLMProvider)
        .where(CustomLLMProvider.provider_key == provider_key)
        .where(CustomLLMProvider.user_id == user.id)
    )
    if existing_key.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="提供商标识已存在")
    
    existing_name = await db.execute(
        select(CustomLLMProvider)
        .where(CustomLLMProvider.provider_name == provider_name)
        .where(CustomLLMProvider.user_id == user.id)
    )
    if existing_name.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="提供商名称已存在")
    
    provider = CustomLLMProvider(
        id=generate_id(),
        user_id=user.id,
        provider_name=provider_name,
        provider_key=provider_key,
        base_url=base_url,
        api_key=encrypt_value(api_key) if api_key else None,
        model_name=model_name,
        api_format=api_format,
        headers=encrypt_header_values(headers),
    )
    
    db.add(provider)
    await db.commit()
    await db.refresh(provider)
    
    return {
        "id": provider.id,
        "provider_name": provider.provider_name,
        "provider_key": provider.provider_key,
        "base_url": provider.base_url,
        "model_name": provider.model_name,
        "api_format": provider.api_format,
        "status": provider.status,
        "message": "LLM 提供商创建成功",
    }


@router.get("/presets")
async def get_preset_providers(
    db: AsyncSession = Depends(get_db),
    user = Depends(get_current_user),
):
    from src.shared.llm.preset_providers import PRESET_PROVIDERS
    
    result = await db.execute(
        select(CustomLLMProvider)
        .where(CustomLLMProvider.is_preset.is_(True))
        .where(CustomLLMProvider.user_id == user.id)
    )
    db_presets = result.scalars().all()
    
    db_preset_map = {p.provider_key: p for p in db_presets}
    
    presets_with_status = []
    for preset in PRESET_PROVIDERS:
        db_preset = db_preset_map.get(preset["provider_key"])
        presets_with_status.append({
            "provider_key": preset["provider_key"],
            "provider_name": preset["provider_name"],
            "base_url": db_preset.base_url if db_preset else preset["base_url"],
            "api_format": preset["api_format"],
            "model_name": db_preset.model_name if db_preset else preset["model_name"],
            "icon": preset.get("icon"),
            "description": preset.get("description"),
            "models": preset.get("models", []),
            "requires_api_key": preset.get("requires_api_key", True),
            "base_url_editable": preset.get("base_url_editable", False),
            "is_active": db_preset.status if db_preset else False,
            "db_id": db_preset.id if db_preset else None,
        })
    
    return presets_with_status


@router.post("/test-config")
async def test_provider_config(
    request: Request,
    user = Depends(get_current_user),
):
    try:
        body = await request.json()
    except (ValueError, json.JSONDecodeError):
        body = {}

    return await _test_provider_by_format(
        provider_name=body.get("provider_name") or body.get("provider_key") or "LLM 提供商",
        base_url=body.get("base_url"),
        api_key=body.get("api_key"),
        model_name=body.get("model_name"),
        api_format=body.get("api_format") or "openai",
        headers=body.get("headers") or {},
    )


@router.post("/from-preset/{provider_key}")
async def create_from_preset(
    provider_key: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user = Depends(get_current_user),
):
    from src.shared.llm.preset_providers import PRESET_PROVIDERS
    
    preset = next((p for p in PRESET_PROVIDERS if p["provider_key"] == provider_key), None)
    if not preset:
        raise HTTPException(status_code=404, detail="预设提供商不存在")
    
    try:
        body = await request.json()
    except (ValueError, json.JSONDecodeError):
        body = {}
    
    api_key = body.get("api_key")
    selected_model = body.get("model_name", preset["model_name"])
    selected_base_url = body.get("base_url") or preset["base_url"]
    try:
        await assert_safe_llm_endpoint(selected_base_url, preset["api_format"])
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    
    existing_result = await db.execute(
        select(CustomLLMProvider)
        .where(CustomLLMProvider.provider_key == provider_key)
        .where(CustomLLMProvider.user_id == user.id)
    )
    existing = existing_result.scalar_one_or_none()
    if existing:
        existing.provider_name = preset["provider_name"]
        existing.base_url = selected_base_url
        existing.api_format = preset["api_format"]
        existing.model_name = selected_model
        existing.icon = preset.get("icon")
        existing.is_preset = True
        existing.status = True
        if api_key:
            existing.api_key = encrypt_value(api_key)
        await db.commit()
        await db.refresh(existing)
        payload = _provider_payload(existing)
        payload["message"] = "预设提供商已更新"
        return payload
    
    provider = CustomLLMProvider(
        id=generate_id(),
        user_id=user.id,
        provider_key=provider_key,
        provider_name=preset["provider_name"],
        base_url=selected_base_url,
        api_format=preset["api_format"],
        model_name=selected_model,
        api_key=encrypt_value(api_key) if api_key else None,
        icon=preset.get("icon"),
        is_preset=True,
        headers={},
        status=True,
    )
    
    db.add(provider)
    await db.commit()
    await db.refresh(provider)
    
    return {
        "id": provider.id,
        "provider_name": provider.provider_name,
        "provider_key": provider.provider_key,
        "base_url": provider.base_url,
        "model_name": provider.model_name,
        "api_format": provider.api_format,
        "status": provider.status,
        "message": "预设提供商已保存",
    }


@router.post("/{provider_key}/test")
async def test_saved_provider(
    provider_key: str,
    db: AsyncSession = Depends(get_db),
    user = Depends(get_current_user),
):
    result = await db.execute(
        select(CustomLLMProvider)
        .where(CustomLLMProvider.provider_key == provider_key)
        .where(CustomLLMProvider.user_id == user.id)
    )
    provider = result.scalar_one_or_none()
    if not provider:
        raise HTTPException(status_code=404, detail="LLM 提供商不存在")

    return await _test_provider_by_format(
        provider_name=provider.provider_name,
        base_url=provider.base_url,
        api_key=decrypt_value(provider.api_key) if provider.api_key else None,
        model_name=provider.model_name,
        api_format=provider.api_format,
        headers=decrypt_header_values(provider.headers),
    )


@router.get("/{provider_key}")
async def get_custom_llm_provider(
    provider_key: str,
    db: AsyncSession = Depends(get_db),
    user = Depends(get_current_user),
):
    result = await db.execute(
        select(CustomLLMProvider)
        .where(CustomLLMProvider.provider_key == provider_key)
        .where(CustomLLMProvider.user_id == user.id)
    )
    provider = result.scalar_one_or_none()
    if not provider:
        raise HTTPException(status_code=404, detail="LLM 提供商不存在")
    
    return {
        "id": provider.id,
        "provider_name": provider.provider_name,
        "provider_key": provider.provider_key,
        "base_url": provider.base_url,
        "model_name": provider.model_name,
        "api_format": provider.api_format,
        "headers": decrypt_header_values(provider.headers),
        "status": provider.status,
        "created_at": provider.created_at,
        "updated_at": provider.updated_at,
    }


@router.put("/{provider_key}")
async def update_custom_llm_provider(
    provider_key: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user = Depends(get_current_user),
):
    try:
        body = await request.json()
    except (ValueError, json.JSONDecodeError):
        body = {}
    
    result = await db.execute(
        select(CustomLLMProvider)
        .where(CustomLLMProvider.provider_key == provider_key)
        .where(CustomLLMProvider.user_id == user.id)
    )
    provider = result.scalar_one_or_none()
    if not provider:
        raise HTTPException(status_code=404, detail="LLM 提供商不存在")
    
    if body.get("provider_name"):
        existing = await db.execute(
            select(CustomLLMProvider)
            .where(CustomLLMProvider.provider_name == body["provider_name"])
            .where(CustomLLMProvider.user_id == user.id)
        )
        existing_provider = existing.scalar_one_or_none()
        if existing_provider and existing_provider.id != provider.id:
            raise HTTPException(status_code=400, detail="提供商名称已存在")
        provider.provider_name = body["provider_name"]
    
    if body.get("base_url"):
        try:
            await assert_safe_llm_endpoint(
                body["base_url"],
                body.get("api_format") or provider.api_format,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        provider.base_url = body["base_url"]
    if "api_key" in body:
        provider.api_key = encrypt_value(body["api_key"]) if body["api_key"] else None
    if "model_name" in body:
        provider.model_name = body["model_name"]
    if body.get("api_format"):
        provider.api_format = body["api_format"]
    if "headers" in body:
        provider.headers = encrypt_header_values(body["headers"])
    if "status" in body:
        provider.status = body["status"]
    
    await db.commit()
    await db.refresh(provider)
    
    return {
        "id": provider.id,
        "provider_name": provider.provider_name,
        "provider_key": provider.provider_key,
        "status": "updated",
        "message": "LLM 提供商已更新",
    }


@router.delete("/{provider_key}")
async def delete_custom_llm_provider(
    provider_key: str,
    db: AsyncSession = Depends(get_db),
    user = Depends(get_current_user),
):
    result = await db.execute(
        select(CustomLLMProvider)
        .where(CustomLLMProvider.provider_key == provider_key)
        .where(CustomLLMProvider.user_id == user.id)
    )
    provider = result.scalar_one_or_none()
    if not provider:
        raise HTTPException(status_code=404, detail="LLM 提供商不存在")
    
    agent_check = await db.execute(
        select(AgentProfile)
        .where(AgentProfile.custom_provider_key == provider_key)
        .where(AgentProfile.user_id == user.id)
    )
    if agent_check.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="该提供商正在被智能体使用，不能删除")
    
    await db.delete(provider)
    await db.commit()
    
    return {"status": "deleted"}
