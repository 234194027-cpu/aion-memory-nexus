"""MemoryAnswerService — application service layer for memory-grounded LLM answers.

WP-0A-T04: 抽取 src/memory/api/memories.py 中的 LLM 调用逻辑，使 API 层
不再直接依赖 src.shared.llm.providers，配合 .importlinter 契约
`api_no_direct_llm` 强制 API 层通过 services 层访问 LLM。

设计要点（D18）:
  - 包含 retrieval trace：service 返回 provider_used 字段，便于审计与排障
  - 不引入 ModelGateway 抽象：保持与现有 get_llm_provider 接口一致
  - 行为契约：与原 memories.py 内联调用完全等价，不改变 LLM 入参/出参

Rollback:
  - 通过版本回滚或上层路由 Feature Flag 切换完整实现；本服务自身不声明
    一个无法恢复旧 inline import 的伪开关。
"""
from __future__ import annotations

import logging
from typing import Any, AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession

from src.shared.security.encryption import decrypt_value

logger = logging.getLogger(__name__)


class MemoryAnswerService:
    """Application service: memory-grounded non-streaming & streaming LLM answers.

    使用方式：
        service = MemoryAnswerService(db=db)
        result = await service.answer_question(
            prompt=full_prompt,
            agent_id=agent_id,
            agent_config=agent_config,
        )
        # result = {"answer": str, "provider_used": str, "error": str | None}

    Streaming:
        async for chunk in service.answer_question_stream(
            prompt=prompt,
            agent_id=agent_id,
        ):
            ...

    Note: 不持有任何 LLM client 状态；每次调用都通过 get_llm_provider 解析当前
    agent 的 provider 配置，保持与原内联调用一致的运行时语义。
    """

    def __init__(self, db: AsyncSession):
        self.db = db

    async def answer_question(
        self,
        *,
        prompt: str,
        agent_id: str | None = None,
        agent_config: Any = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> dict:
        """Generate a non-streaming answer from the configured LLM provider.

        Args:
            prompt: Fully-built prompt including system + history + user message.
            agent_id: Optional agent ID for provider selection.
            agent_config: Optional AgentProfile instance carrying provider config
                (llm_provider, llm_model, llm_api_key, llm_api_base, etc.).
            temperature: Optional temperature override (falls back to
                agent_config.llm_temperature or 0.7). Used by /ask endpoint
                which accepts per-request temperature.
            max_tokens: Optional max_tokens override (falls back to
                agent_config.llm_max_tokens or 4096). Used by /ask endpoint
                which accepts per-request max_tokens.

        Returns:
            dict with keys:
              - answer: str — LLM 生成的回复（失败时为降级文案）
              - provider_used: str — provider 类名（用于 retrieval trace）
              - error: str | None — 失败原因（成功时为 None）
        """
        from src.shared.llm.model_gateway import ModelGateway
        from src.shared.llm.providers import get_llm_provider

        llm_provider_str = None
        if agent_config and getattr(agent_config, "llm_provider", None):
            raw_provider = agent_config.llm_provider
            llm_provider_str = (
                raw_provider.value
                if hasattr(raw_provider, "value")
                else str(raw_provider)
            )

        # Resolve effective temperature / max_tokens: explicit override wins,
        # then agent_config, then sensible defaults.
        effective_temperature = (
            temperature if temperature is not None
            else (agent_config.llm_temperature if agent_config else 0.7)
        )
        effective_max_tokens = (
            max_tokens if max_tokens is not None
            else (agent_config.llm_max_tokens if agent_config else 4096)
        )

        provider = get_llm_provider(
            agent_id=agent_id,
            llm_provider=llm_provider_str,
            custom_provider_key=(
                agent_config.custom_provider_key if agent_config else None
            ),
            llm_model=agent_config.llm_model if agent_config else None,
            llm_api_key=(
                decrypt_value(agent_config.llm_api_key) if agent_config else None
            ),
            llm_api_base=agent_config.llm_api_base if agent_config else None,
            llm_temperature=effective_temperature,
            llm_max_tokens=effective_max_tokens,
        )

        provider_name = type(provider).__name__

        try:
            response = await ModelGateway(provider).generate_text(
                prompt,
                model_name=agent_config.llm_model if agent_config else None,
                temperature=effective_temperature,
                max_tokens=effective_max_tokens,
                prompt_id="memory-answer",
                prompt_version="v1",
            )
            return {
                "answer": response,
                "provider_used": provider_name,
                "error": None,
            }
        except Exception as exc:
            # 安全：仅记录内部日志，不向用户泄露异常细节
            logger.error(
                "MemoryAnswerService.answer_question failed "
                "(provider=%s, agent_id=%s): %s",
                provider_name,
                agent_id,
                exc,
            )
            return {
                "answer": "抱歉，生成回复时发生内部错误，请稍后重试。",
                "provider_used": provider_name,
                "error": str(exc),
            }

    async def answer_question_stream(
        self,
        *,
        prompt: str,
        agent_id: str | None = None,
    ) -> AsyncIterator[str]:
        """Stream answer tokens from the configured LLM provider.

        Args:
            prompt: Fully-built prompt.
            agent_id: Optional agent ID for provider selection (uses defaults
                when None — testing returns MockProvider).

        Yields:
            str: Each token/chunk from the LLM provider's generate_stream().
        """
        from src.shared.llm.providers import get_llm_provider

        provider = get_llm_provider(agent_id=agent_id)
        async for chunk in provider.generate_stream(prompt):
            yield chunk
