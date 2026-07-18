"""Narrow model gateway for new Runtime paths; legacy callers remain compatible."""
from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass
from time import monotonic

from src.shared.errors.error_classification import ClassifiedError, ErrorClass, classify_exception
from src.shared.llm.providers import LLMProvider
from src.shared.utils.retry import with_retry


@dataclass(frozen=True, slots=True)
class ModelGatewayResult:
    text: str
    model: str | None
    latency_ms: int
    prompt_id: str | None
    prompt_version: str | None


class ModelGateway:
    """Provider-neutral text gateway with stable failures and no secret/raw-prompt logging."""

    def __init__(
        self,
        provider: LLMProvider,
        *,
        timeout_seconds: float = 60.0,
        max_retries: int = 2,
        retry_initial_delay: float = 0.1,
    ) -> None:
        self.provider = provider
        self.timeout_seconds = timeout_seconds
        self.max_retries = max(0, max_retries)
        self.retry_initial_delay = max(0.0, retry_initial_delay)

    async def _provider_generate(
        self,
        prompt: str,
        *,
        model_name: str | None,
        temperature: float,
        max_tokens: int,
    ) -> object:
        """Call current and legacy text providers without inventing a new ABI."""
        generate = self.provider.generate
        kwargs = {
            "model_name": model_name,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        try:
            parameters = tuple(inspect.signature(generate).parameters.values())
            accepts_var_kwargs = any(parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in parameters)
            accepts_positional_prompt = any(
                parameter.kind
                in {
                    inspect.Parameter.POSITIONAL_ONLY,
                    inspect.Parameter.POSITIONAL_OR_KEYWORD,
                    inspect.Parameter.VAR_POSITIONAL,
                }
                for parameter in parameters
            )
            supported_names = {parameter.name for parameter in parameters}
            if not accepts_var_kwargs:
                kwargs = {name: value for name, value in kwargs.items() if name in supported_names}
            if not accepts_positional_prompt and ("prompt" in supported_names or accepts_var_kwargs):
                return await generate(prompt=prompt, **kwargs)
        except (TypeError, ValueError):
            # Callable signature introspection is not guaranteed for every
            # third-party adapter.  Modern provider implementations accept the
            # documented keyword arguments, so retain that default path.
            pass
        return await generate(prompt, **kwargs)

    async def generate(
        self,
        prompt: str,
        *,
        model_name: str | None,
        temperature: float,
        max_tokens: int,
        prompt_id: str | None = None,
        prompt_version: str | None = None,
    ) -> ModelGatewayResult:
        started = monotonic()

        async def _attempt() -> object:
            return await asyncio.wait_for(
                self._provider_generate(
                    prompt,
                    model_name=model_name,
                    temperature=temperature,
                    max_tokens=max_tokens,
                ),
                timeout=self.timeout_seconds,
            )

        try:
            text = await with_retry(
                _attempt,
                max_retries=self.max_retries,
                initial_delay=self.retry_initial_delay,
            )()
        except asyncio.TimeoutError as exc:
            raise ClassifiedError(ErrorClass.TIMEOUT, "model gateway timeout", retryable=True) from exc
        except Exception as exc:
            error_class = classify_exception(exc)
            raise ClassifiedError(error_class, "model gateway request failed") from exc
        return ModelGatewayResult(
            text=text if isinstance(text, str) else "",
            model=model_name,
            latency_ms=int((monotonic() - started) * 1000),
            prompt_id=prompt_id,
            prompt_version=prompt_version,
        )

    async def generate_text(
        self,
        prompt: str,
        *,
        model_name: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 2048,
        prompt_id: str | None = None,
        prompt_version: str | None = None,
    ) -> str:
        """Compatibility facade for legacy text-only callers.

        Existing services retain their prompt parsing and product-specific
        fallbacks, while timeout and exception classification are centralized.
        Streaming, embeddings, and provider-specific protocols remain on their
        dedicated adapters.
        """
        result = await self.generate(
            prompt,
            model_name=model_name,
            temperature=temperature,
            max_tokens=max_tokens,
            prompt_id=prompt_id,
            prompt_version=prompt_version,
        )
        return result.text
