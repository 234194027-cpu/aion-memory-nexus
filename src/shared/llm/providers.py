import hashlib
import asyncio
import httpx
from abc import ABC, abstractmethod
from collections import OrderedDict
from sqlalchemy import select
from src.shared.config import settings
from src.shared.security.encryption import decrypt_header_values, decrypt_value
from src.shared.security.outbound_url import assert_safe_llm_endpoint
from src.shared.utils.runtime_metrics import runtime_metrics
import logging

logger = logging.getLogger(__name__)

# ── LLM / Embedding 结果缓存 ──────────────────────────────────────────
# 简单 OrderedDict LRU 缓存，避免相同 prompt / text 重复请求外部 API。
# 流式响应不缓存；fallback 结果不缓存。可用 settings.ENABLE_LLM_CACHE 关闭。
_LLM_CACHE_ENABLED = settings.ENABLE_LLM_CACHE
_LLM_CACHE_MAX_SIZE = settings.LLM_CACHE_MAX_SIZE
_EMBED_CACHE_MAX_SIZE = settings.LLM_CACHE_MAX_SIZE

_llm_cache: "OrderedDict[str, str]" = OrderedDict()
_embed_cache: "OrderedDict[str, list]" = OrderedDict()


def clear_llm_runtime_caches() -> None:
    """Clear process-local caches after a user data deletion request.

    Prompt cache keys are hashed and cannot be mapped back to one user, so a
    privacy deletion deliberately clears the small shared caches in full.
    """
    _llm_cache.clear()
    _embed_cache.clear()
    instances = globals().get("_provider_instances")
    if isinstance(instances, dict):
        instances.clear()


def _llm_cache_key(prompt: str, model_name, temperature: float) -> str:
    raw = f"{prompt}|{model_name or ''}|{temperature}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _embed_cache_key(text: str, model_name) -> str:
    raw = f"{text}|{model_name or ''}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _cache_get(cache: "OrderedDict", key: str):
    if key not in cache:
        return None
    cache.move_to_end(key)
    return cache[key]


def _cache_set(cache: "OrderedDict", key: str, value, max_size: int):
    cache[key] = value
    cache.move_to_end(key)
    while len(cache) > max_size:
        cache.popitem(last=False)


def _is_configured_secret(value: str | None) -> bool:
    if not value:
        return False
    normalized = value.strip().lower()
    if not normalized:
        return False
    placeholder_markers = (
        "your-",
        "<your",
        "replace-with",
        "changeme",
        "placeholder",
    )
    return not any(marker in normalized for marker in placeholder_markers)


def _standardize_embedding_dimension(vector: list, target_dim: int | None = None) -> list:
    """将 embedding 向量标准化到指定维度（零填充或截断）。

    用于统一不同 embedding 提供方（API / BGE-M3 / fallback hash）的维度，
    避免 dimension 与 memory_embeddings.embedding_vector 列定义不一致导致检索失效。
    - vector 短于 target_dim: 末尾零填充
    - vector 长于 target_dim: 截断并记录 warning（可能损失语义质量）
    - 维度已一致: 原样返回
    """
    if target_dim is None:
        target_dim = settings.EMBEDDING_DIMENSION
    if not isinstance(vector, list) or len(vector) == 0:
        return vector
    if len(vector) == target_dim:
        return vector
    if len(vector) < target_dim:
        return vector + [0.0] * (target_dim - len(vector))
    logger.warning(
        f"Embedding dimension {len(vector)} > target {target_dim}, truncating. "
        f"This may degrade retrieval quality."
    )
    return vector[:target_dim]

def _extract_event_content_from_prompt(prompt: str) -> str:
    marker = "Event content:"
    end_marker = "Event metadata:"
    if marker not in prompt:
        return prompt
    content = prompt.split(marker, 1)[1]
    if end_marker in content:
        content = content.split(end_marker, 1)[0]
    return content.strip()

def _fallback_memory_json(prompt: str, title: str) -> str:
    import json
    content = _extract_event_content_from_prompt(prompt)
    if len(content) < 20:
        return "[]"
    return json.dumps([{
        "type": "fact",
        "title": title,
        "content": content[:500],
        "importance": 0.6,
        "confidence": 0.4,
        "sensitivity": "normal",
        "entities": [],
        "reason": "LLM unavailable; generated as low-confidence candidate for manual review only",
    }], ensure_ascii=False)

class LLMProvider(ABC):
    # 复用的 httpx.AsyncClient（lazy 初始化；实例首次赋值后变为实例属性）
    _http_client: "httpx.AsyncClient | None" = None

    def _get_client(self) -> "httpx.AsyncClient":
        """获取复用的 httpx.AsyncClient（lazy 初始化，asyncio 单线程无需锁）。"""
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient(timeout=60)
        return self._http_client

    async def aclose(self):
        """关闭复用的 httpx client。应用 shutdown 时应调用 close_all_providers()。"""
        if self._http_client is not None and not self._http_client.is_closed:
            await self._http_client.aclose()
        self._http_client = None

    @abstractmethod
    async def generate(self, prompt: str, model_name: str | None = None, temperature: float = 0.3, max_tokens: int = 4096) -> str:
        pass

    async def generate_stream(self, prompt: str, *, model_name: str | None = None, temperature: float = 0.3, max_tokens: int = 2000):
        """异步生成器，逐 token yield。默认实现：整块返回。"""
        full = await self.generate(prompt, model_name=model_name, temperature=temperature, max_tokens=max_tokens)
        yield full

    @abstractmethod
    async def embed(self, text: str) -> list:
        pass

class DeepSeekProvider(LLMProvider):
    def __init__(self):
        self.api_key = settings.DEEPSEEK_API_KEY
        self.api_url = settings.DEEPSEEK_API_URL

    async def generate(self, prompt: str, model_name: str = None, temperature: float = 0.3, max_tokens: int = 4096) -> str:
        if not _is_configured_secret(self.api_key):
            runtime_metrics.record_external_call("llm_generate", failed=True)
            return self._fallback_generate(prompt)

        # 缓存检查（非流式）
        cache_key = None
        if _LLM_CACHE_ENABLED:
            cache_key = _llm_cache_key(prompt, model_name, temperature)
            cached = _cache_get(_llm_cache, cache_key)
            if cached is not None:
                return cached

        client = self._get_client()
        try:
            response = await client.post(
                f"{self.api_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model_name or "deepseek-chat",
                    "messages": [
                        {"role": "system", "content": "You are a helpful assistant."},
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                },
                timeout=60,
            )
            response.raise_for_status()
            data = response.json()
            result = data["choices"][0]["message"]["content"]
            if _LLM_CACHE_ENABLED and cache_key is not None:
                _cache_set(_llm_cache, cache_key, result, _LLM_CACHE_MAX_SIZE)
            runtime_metrics.record_external_call("llm_generate")
            return result
        except Exception:
            runtime_metrics.record_external_call("llm_generate", failed=True)
            logger.warning("DeepSeek generate failed", exc_info=True)
            return self._fallback_generate(prompt)

    async def embed(self, text: str) -> list:
        if not _is_configured_secret(self.api_key):
            runtime_metrics.record_external_call("embedding", failed=True)
            return self._fallback_embed(text)

        # 缓存检查
        cache_key = None
        if _LLM_CACHE_ENABLED:
            cache_key = _embed_cache_key(text, "deepseek-embed")
            cached = _cache_get(_embed_cache, cache_key)
            if cached is not None:
                return cached

        client = self._get_client()
        try:
            response = await client.post(
                f"{self.api_url}/embeddings",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "deepseek-embed",
                    "input": text,
                },
                timeout=60,
            )
            response.raise_for_status()
            data = response.json()
            result = _standardize_embedding_dimension(data["data"][0]["embedding"])
            if _LLM_CACHE_ENABLED and cache_key is not None:
                _cache_set(_embed_cache, cache_key, result, _EMBED_CACHE_MAX_SIZE)
            runtime_metrics.record_external_call("embedding")
            return result
        except Exception:
            runtime_metrics.record_external_call("embedding", failed=True)
            logger.warning("DeepSeek embed failed", exc_info=True)
            return self._fallback_embed(text)

    def _fallback_generate(self, prompt: str) -> str:
        if "Return ONLY a valid JSON array" in prompt:
            return _fallback_memory_json(prompt, "待工作 Agent 治理的事件摘要")
        return "LLM provider is not configured or unavailable. Please configure a working model provider."

    def _fallback_embed(self, text: str) -> list:
        import hashlib
        hash_val = int(hashlib.md5(text.encode()).hexdigest(), 16)
        return [(hash_val >> (i * 8)) & 0xFF for i in range(settings.EMBEDDING_DIMENSION)]

class MockProvider(LLMProvider):
    async def generate(self, prompt: str, model_name: str = None, temperature: float = 0.3, max_tokens: int = 4096) -> str:
        runtime_metrics.record_external_call("llm_generate", failed=True)
        if "Return ONLY a valid JSON array" in prompt:
            return _fallback_memory_json(prompt, "待工作 Agent 治理的事件摘要")
        return "LLM provider is not configured. Configure a provider before using chat."

    async def embed(self, text: str) -> list:
        runtime_metrics.record_external_call("embedding", failed=True)
        import hashlib
        hash_val = int(hashlib.md5(text.encode()).hexdigest(), 16)
        return [(hash_val >> (i * 8)) & 0xFF for i in range(settings.EMBEDDING_DIMENSION)]


class CustomProvider(LLMProvider):
    def __init__(self, base_url: str, api_key: str = None, model_name: str = None, api_format: str = "openai", headers: dict = None):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model_name = model_name
        self.api_format = api_format.lower()
        self.headers = headers or {}
    
    async def generate(self, prompt: str, model_name: str = None, temperature: float = 0.3, max_tokens: int = 4096) -> str:
        runtime_metrics.record_external_call("llm_generate")
        await assert_safe_llm_endpoint(self.base_url, self.api_format)
        if self.api_format == "openai":
            return await self._generate_openai(prompt, model_name, temperature, max_tokens)
        elif self.api_format == "ollama":
            return await self._generate_ollama(prompt, model_name, temperature, max_tokens)
        elif self.api_format == "baidu":
            return await self._generate_baidu(prompt, model_name, temperature, max_tokens)
        elif self.api_format == "tencent":
            return await self._generate_tencent(prompt, model_name, temperature, max_tokens)
        elif self.api_format == "anthropic":
            return await self._generate_anthropic(prompt, model_name, temperature, max_tokens)
        else:
            return self._fallback_generate(prompt)
    
    async def _generate_openai(self, prompt: str, model_name: str = None, temperature: float = 0.3, max_tokens: int = 4096) -> str:
        # 缓存检查（非流式）
        cache_key = None
        if _LLM_CACHE_ENABLED:
            cache_key = _llm_cache_key(prompt, model_name or self.model_name, temperature)
            cached = _cache_get(_llm_cache, cache_key)
            if cached is not None:
                return cached

        headers = {
            "Content-Type": "application/json",
            **self.headers,
        }

        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
            if "xiaomimimo.com" in self.base_url:
                headers["api-key"] = self.api_key

        client = self._get_client()
        try:
            response = await client.post(
                f"{self.base_url}/chat/completions",
                headers=headers,
                json={
                    "model": model_name or self.model_name or "default",
                    "messages": [
                        {"role": "system", "content": "You are a helpful assistant."},
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                },
                timeout=60,
            )
            response.raise_for_status()
            data = response.json()
            result = data["choices"][0]["message"]["content"]
            if _LLM_CACHE_ENABLED and cache_key is not None:
                _cache_set(_llm_cache, cache_key, result, _LLM_CACHE_MAX_SIZE)
            return result
        except Exception:
            logger.warning("OpenAI generate failed", exc_info=True)
            return self._fallback_generate(prompt)

    async def _generate_ollama(self, prompt: str, model_name: str = None, temperature: float = 0.3, max_tokens: int = 4096) -> str:
        cache_key = None
        selected_model = model_name or self.model_name or "llama3.1"
        if _LLM_CACHE_ENABLED:
            cache_key = _llm_cache_key(prompt, selected_model, temperature)
            cached = _cache_get(_llm_cache, cache_key)
            if cached is not None:
                return cached

        client = self._get_client()
        try:
            response = await client.post(
                f"{self.base_url}/api/chat",
                headers={
                    "Content-Type": "application/json",
                    **self.headers,
                },
                json={
                    "model": selected_model,
                    "messages": [
                        {"role": "system", "content": "You are a helpful assistant."},
                        {"role": "user", "content": prompt},
                    ],
                    "stream": False,
                    "options": {
                        "temperature": temperature,
                        "num_predict": max_tokens,
                    },
                },
                timeout=120,
            )
            response.raise_for_status()
            data = response.json()
            result = (
                (data.get("message") or {}).get("content")
                or data.get("response")
                or ""
            )
            if not result:
                return self._fallback_generate(prompt)
            if _LLM_CACHE_ENABLED and cache_key is not None:
                _cache_set(_llm_cache, cache_key, result, _LLM_CACHE_MAX_SIZE)
            return result
        except Exception:
            logger.warning("Ollama generate failed", exc_info=True)
            return self._fallback_generate(prompt)
    
    async def _generate_baidu(self, prompt: str, model_name: str = None, temperature: float = 0.3, max_tokens: int = 4096) -> str:
        # 缓存检查（非流式）
        cache_key = None
        if _LLM_CACHE_ENABLED:
            cache_key = _llm_cache_key(prompt, model_name or self.model_name, temperature)
            cached = _cache_get(_llm_cache, cache_key)
            if cached is not None:
                return cached

        selected_model = model_name or self.model_name or "eb-instant"

        client = self._get_client()
        try:
            response = await client.post(
                f"{self.base_url}/{selected_model}",
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self.api_key}",
                    **self.headers,
                },
                json={
                    "messages": [
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": temperature,
                    "max_output_tokens": max_tokens,
                },
                timeout=60,
            )
            response.raise_for_status()
            data = response.json()
            result = data.get("result", {}).get("content")
            if result is None:
                return self._fallback_generate(prompt)
            if _LLM_CACHE_ENABLED and cache_key is not None:
                _cache_set(_llm_cache, cache_key, result, _LLM_CACHE_MAX_SIZE)
            return result
        except Exception:
            logger.warning("Baidu generate failed", exc_info=True)
            return self._fallback_generate(prompt)
    
    async def _generate_tencent(self, prompt: str, model_name: str = None, temperature: float = 0.3, max_tokens: int = 4096) -> str:
        # 缓存检查（非流式）
        cache_key = None
        if _LLM_CACHE_ENABLED:
            cache_key = _llm_cache_key(prompt, model_name or self.model_name, temperature)
            cached = _cache_get(_llm_cache, cache_key)
            if cached is not None:
                return cached

        selected_model = model_name or self.model_name or "hunyuan-pro"

        client = self._get_client()
        try:
            response = await client.post(
                f"{self.base_url}/chat/completions",
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self.api_key}",
                    **self.headers,
                },
                json={
                    "model": selected_model,
                    "messages": [
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                },
                timeout=60,
            )
            response.raise_for_status()
            data = response.json()
            result = data["choices"][0]["message"]["content"]
            if _LLM_CACHE_ENABLED and cache_key is not None:
                _cache_set(_llm_cache, cache_key, result, _LLM_CACHE_MAX_SIZE)
            return result
        except Exception:
            logger.warning("Tencent generate failed", exc_info=True)
            return self._fallback_generate(prompt)
    
    async def _generate_anthropic(self, prompt: str, model_name: str = None, temperature: float = 0.3, max_tokens: int = 4096) -> str:
        # 缓存检查（非流式）
        cache_key = None
        if _LLM_CACHE_ENABLED:
            cache_key = _llm_cache_key(prompt, model_name or self.model_name, temperature)
            cached = _cache_get(_llm_cache, cache_key)
            if cached is not None:
                return cached

        selected_model = model_name or self.model_name or "claude-3-sonnet"

        client = self._get_client()
        try:
            response = await client.post(
                f"{self.base_url}/messages",
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self.api_key}",
                    "anthropic-version": "2023-06-01",
                    **self.headers,
                },
                json={
                    "model": selected_model,
                    "messages": [
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                },
                timeout=60,
            )
            response.raise_for_status()
            data = response.json()
            result = data["content"][0]["text"]
            if _LLM_CACHE_ENABLED and cache_key is not None:
                _cache_set(_llm_cache, cache_key, result, _LLM_CACHE_MAX_SIZE)
            return result
        except Exception:
            logger.warning("Anthropic generate failed", exc_info=True)
            return self._fallback_generate(prompt)
    
    async def embed(self, text: str) -> list:
        runtime_metrics.record_external_call("embedding")
        await assert_safe_llm_endpoint(self.base_url, self.api_format)
        if self.api_format == "openai":
            return await self._embed_openai(text)
        elif self.api_format == "ollama":
            return await self._embed_ollama(text)
        else:
            return self._fallback_embed(text)
    
    async def _embed_openai(self, text: str) -> list:
        # 缓存检查
        cache_key = None
        if _LLM_CACHE_ENABLED:
            cache_key = _embed_cache_key(text, self.model_name)
            cached = _cache_get(_embed_cache, cache_key)
            if cached is not None:
                return cached

        headers = {
            "Content-Type": "application/json",
            **self.headers,
        }

        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
            if "xiaomimimo.com" in self.base_url:
                headers["api-key"] = self.api_key

        client = self._get_client()
        try:
            response = await client.post(
                f"{self.base_url}/embeddings",
                headers=headers,
                json={
                    "model": self.model_name or "text-embedding-ada-002",
                    "input": text,
                },
                timeout=60,
            )
            response.raise_for_status()
            data = response.json()
            result = _standardize_embedding_dimension(data["data"][0]["embedding"])
            if _LLM_CACHE_ENABLED and cache_key is not None:
                _cache_set(_embed_cache, cache_key, result, _EMBED_CACHE_MAX_SIZE)
            return result
        except Exception:
            logger.warning("OpenAI embed failed", exc_info=True)
            return self._fallback_embed(text)

    async def _embed_ollama(self, text: str) -> list:
        cache_key = None
        selected_model = self.model_name or "llama3.1"
        if _LLM_CACHE_ENABLED:
            cache_key = _embed_cache_key(text, selected_model)
            cached = _cache_get(_embed_cache, cache_key)
            if cached is not None:
                return cached

        client = self._get_client()
        try:
            response = await client.post(
                f"{self.base_url}/api/embed",
                headers={
                    "Content-Type": "application/json",
                    **self.headers,
                },
                json={
                    "model": selected_model,
                    "input": text,
                },
                timeout=120,
            )
            response.raise_for_status()
            data = response.json()
            embeddings = data.get("embeddings") or []
            vector = embeddings[0] if embeddings else data.get("embedding")
            if not vector:
                return self._fallback_embed(text)
            result = _standardize_embedding_dimension(vector)
            if _LLM_CACHE_ENABLED and cache_key is not None:
                _cache_set(_embed_cache, cache_key, result, _EMBED_CACHE_MAX_SIZE)
            return result
        except Exception:
            logger.warning("Ollama embed failed", exc_info=True)
            return self._fallback_embed(text)
    
    def _fallback_generate(self, prompt: str) -> str:
        if "Return ONLY a valid JSON array" in prompt:
            return _fallback_memory_json(prompt, "待工作 Agent 治理的事件摘要")
        return "Custom LLM provider is unavailable. Please verify the provider configuration."
    
    def _fallback_embed(self, text: str) -> list:
        import hashlib
        hash_val = int(hashlib.md5(text.encode()).hexdigest(), 16)
        return [(hash_val >> (i * 8)) & 0xFF for i in range(settings.EMBEDDING_DIMENSION)]

_provider_instances = {}


def _provider_instance_cache_key(
    provider: LLMProvider,
    *,
    agent_id: str | None,
    temperature: float,
    max_tokens: int,
) -> str:
    try:
        loop_identity = id(asyncio.get_running_loop())
    except RuntimeError:
        loop_identity = 0
    api_key = getattr(provider, "api_key", None) or ""
    identity = (
        provider.__class__.__name__,
        agent_id or "",
        getattr(provider, "base_url", None) or getattr(provider, "api_url", None) or "",
        getattr(provider, "model_name", None) or "",
        getattr(provider, "api_format", None) or "",
        repr(sorted((getattr(provider, "headers", None) or {}).items())),
        hashlib.sha256(api_key.encode("utf-8")).hexdigest() if api_key else "",
        float(temperature),
        int(max_tokens),
        loop_identity,
    )
    return hashlib.sha256(repr(identity).encode("utf-8")).hexdigest()

def get_llm_provider(agent_id: str | None = None, llm_provider: str | None = None, custom_provider_key: str | None = None,
                     llm_model: str | None = None, llm_api_key: str | None = None, llm_api_base: str | None = None,
                     llm_temperature: float = 0.7, llm_max_tokens: int = 4096) -> LLMProvider:
    provider = None

    # Agent records store API keys encrypted. decrypt_value is deliberately
    # backward compatible with legacy/plaintext callers.
    llm_api_key = decrypt_value(llm_api_key)

    explicit_provider_requested = any(
        [
            llm_provider,
            custom_provider_key,
            llm_api_key,
            llm_api_base,
        ]
    )
    if settings.TESTING and not explicit_provider_requested:
        return MockProvider()
    
    if custom_provider_key:
        from src.shared.db.database import get_db_sync
        from src.execution.models.agent_profile import AgentProfile
        from src.execution.models.custom_llm_provider import CustomLLMProvider
        
        db_gen = get_db_sync()
        try:
            db = next(db_gen)
            owner_id = None
            if agent_id:
                owner_id = db.execute(
                    select(AgentProfile.user_id).where(AgentProfile.id == agent_id)
                ).scalar_one_or_none()

            provider_query = select(CustomLLMProvider).where(
                CustomLLMProvider.provider_key == custom_provider_key
            )
            if owner_id:
                provider_query = provider_query.where(CustomLLMProvider.user_id == owner_id)
                custom_provider = db.execute(provider_query).scalar_one_or_none()
            else:
                matches = db.execute(provider_query.limit(2)).scalars().all()
                custom_provider = matches[0] if len(matches) == 1 else None
                if len(matches) > 1:
                    logger.warning(
                        "Ambiguous custom provider key without agent ownership context"
                    )
            
            if custom_provider and custom_provider.status:
                provider = CustomProvider(
                    base_url=custom_provider.base_url,
                    api_key=decrypt_value(custom_provider.api_key),
                    model_name=llm_model or custom_provider.model_name,
                    api_format=custom_provider.api_format,
                    headers=decrypt_header_values(custom_provider.headers),
                )
        except Exception as e:
            logger.warning(f"Custom provider lookup failed: {e}")
        finally:
            db_gen.close()
    
    if provider is None:
        provider_type = llm_provider.lower() if llm_provider else ""
        
        if provider_type == "deepseek":
            provider = DeepSeekProvider()
        elif provider_type == "openai" and llm_api_key and llm_api_base:
            provider = CustomProvider(
                base_url=llm_api_base,
                api_key=llm_api_key,
                model_name=llm_model,
                api_format="openai",
            )
        elif provider_type == "qwen" and llm_api_key and llm_api_base:
            provider = CustomProvider(
                base_url=llm_api_base,
                api_key=llm_api_key,
                model_name=llm_model,
                api_format="openai",
            )
        elif provider_type == "together" and llm_api_key:
            provider = CustomProvider(
                base_url="https://api.together.xyz/v1",
                api_key=llm_api_key,
                model_name=llm_model,
                api_format="openai",
            )
        elif provider_type == "ollama":
            provider = CustomProvider(
                base_url=llm_api_base or "http://127.0.0.1:11434",
                model_name=llm_model,
                api_format="ollama",
            )
        elif llm_api_base:
            provider = CustomProvider(
                base_url=llm_api_base,
                api_key=llm_api_key,
                model_name=llm_model,
                api_format="openai",
            )
        elif settings.DEEPSEEK_API_KEY:
            provider = DeepSeekProvider()
        else:
            provider = MockProvider()
    
    cache_key = _provider_instance_cache_key(
        provider,
        agent_id=agent_id,
        temperature=llm_temperature,
        max_tokens=llm_max_tokens,
    )
    cached_provider = _provider_instances.get(cache_key)
    if cached_provider is not None:
        return cached_provider
    _provider_instances[cache_key] = provider

    return provider


async def close_all_providers():
    """关闭所有缓存的 provider 实例持有的 httpx client。

    应在应用 shutdown 钩子中调用，以正确释放复用的连接。
    """
    for provider in list(_provider_instances.values()):
        try:
            await provider.aclose()
        except Exception:
            logger.warning("Failed to close provider httpx client", exc_info=True)
    _provider_instances.clear()
