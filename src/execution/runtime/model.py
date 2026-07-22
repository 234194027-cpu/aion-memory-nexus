"""Model adapters. JSON compatibility is strict and never parses free-form commands."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol

from src.shared.llm.providers import LLMProvider
from src.shared.llm.model_gateway import ModelGateway

from .tools.base import ToolCall
from .model_routing import route_model


@dataclass(frozen=True, slots=True)
class RuntimeModelResponse:
    text: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    input_tokens: int = 0
    output_tokens: int = 0
    cost: float = 0.0
    model: str | None = None
    response_mode: str | None = None
    confidence: str | None = None
    citations: tuple[str, ...] = ()


class RuntimeModel(Protocol):
    async def complete(self, *, system_prompt: str, messages: tuple[Mapping[str, Any], ...], tools: Mapping[str, Any]) -> RuntimeModelResponse: ...


class JsonCompatibilityModel:
    """Adapter for existing string-only providers until native tool calling is enabled."""
    def __init__(self, provider: LLMProvider, *, model_name: str | None = None, temperature: float = 0.3, max_tokens: int = 2048, gateway: ModelGateway | None = None, role: str = "conversational") -> None:
        self.provider = provider
        self.model_name = model_name
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.gateway = gateway or ModelGateway(provider)
        self.role = role

    async def complete(self, *, system_prompt: str, messages: tuple[Mapping[str, Any], ...], tools: Mapping[str, Any]) -> RuntimeModelResponse:
        route = route_model(role=self.role, structured_output=bool(tools))
        protocol = {
            "response": "Return exactly one JSON object. Either {\"final\": string, \"response_mode\": \"ANSWER|CLARIFY|SEARCH|REFLECT|PLAN|CONFIRM|SAFE_REFUSAL\", \"confidence\": \"HIGH|MEDIUM|LOW\", \"citations\": [\"stable-id\"]} or {\"tool_calls\":[{\"name\": string, \"arguments\": object}]}. Do not put commands in prose.",
            "tools": {name: {"description": tool.description, "parameters": tool.parameters_schema} for name, tool in tools.items()},
            "messages": list(messages),
        }
        prompt = f"{system_prompt}\n\n{json.dumps(protocol, ensure_ascii=False, default=str)}"
        gateway_result = await self.gateway.generate(
            prompt,
            model_name=self.model_name,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            prompt_id=f"runtime-json-compatibility:{route.tier.value}",
            prompt_version="v1",
        )
        raw = gateway_result.text
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError):
            # Some compatible providers occasionally return a normal assistant
            # sentence despite the JSON instruction.  Treat it as a final
            # no-tool reply, never as an executable command.  Evidence guards
            # in the runtime still reject unsupported memory assertions.
            plain_text = raw.strip() if isinstance(raw, str) else ""
            return RuntimeModelResponse(
                text=plain_text[:8_000],
                model=self.model_name,
                response_mode="ANSWER" if plain_text else None,
                confidence="LOW" if plain_text else None,
            )
        if not isinstance(payload, dict):
            return RuntimeModelResponse(text="", model=self.model_name)
        if self.role == "working" and payload.get("business_state") in {
            "MEMORY_READY",
            "DISCARDED",
            "NEEDS_MORE_EVIDENCE",
            "CONFLICT_REVIEW",
            "USER_CONFIRMATION_REQUIRED",
        }:
            # The Working profile asks for this business object directly. Some
            # compatible providers follow that schema instead of wrapping it
            # in the generic {"final": "..."} transport envelope. Treat only
            # the allow-listed business state as final text; it still passes
            # through WorkingCoordinator governance before any write.
            return RuntimeModelResponse(
                text=json.dumps(payload, ensure_ascii=False),
                model=self.model_name,
                response_mode="PLAN",
                confidence="MEDIUM",
            )
        if isinstance(payload.get("final"), str):
            mode = payload.get("response_mode")
            confidence = payload.get("confidence")
            citations = payload.get("citations", [])
            if mode is not None and mode not in {"ANSWER", "CLARIFY", "SEARCH", "REFLECT", "PLAN", "CONFIRM", "SAFE_REFUSAL"}:
                return RuntimeModelResponse(text="", model=self.model_name)
            if confidence is not None and confidence not in {"HIGH", "MEDIUM", "LOW"}:
                return RuntimeModelResponse(text="", model=self.model_name)
            if not isinstance(citations, list) or not all(isinstance(item, str) and len(item) <= 128 for item in citations):
                return RuntimeModelResponse(text="", model=self.model_name)
            return RuntimeModelResponse(text=payload["final"], model=self.model_name, response_mode=mode, confidence=confidence, citations=tuple(citations[:20]))
        calls = payload.get("tool_calls")
        if not isinstance(calls, list):
            return RuntimeModelResponse(text="", model=self.model_name)
        parsed: list[ToolCall] = []
        for call in calls:
            if not isinstance(call, dict) or not isinstance(call.get("name"), str) or not isinstance(call.get("arguments"), dict):
                return RuntimeModelResponse(text="", model=self.model_name)
            parsed.append(ToolCall(name=call["name"], arguments=call["arguments"]))
        return RuntimeModelResponse(tool_calls=tuple(parsed), model=self.model_name)
