"""Schema, profile, permission, timeout and result-size enforcement."""
from __future__ import annotations

import asyncio
import hashlib
import json
from time import monotonic
from typing import Any, Awaitable, Callable, Mapping

from src.execution.services.tool_permission import ToolPermissionService
from src.shared.errors.error_classification import ErrorClass, classify_exception

from .base import RuntimeTool, ToolCall, ToolExecutionResult


DomainAuthorizer = Callable[[RuntimeTool, Mapping[str, Any]], Awaitable[bool]]


def arguments_hash(arguments: Mapping[str, Any]) -> str:
    payload = json.dumps(arguments, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _validate(schema: Mapping[str, Any], arguments: Mapping[str, Any]) -> str | None:
    if schema.get("type", "object") != "object" or not isinstance(arguments, Mapping):
        return "invalid_arguments"
    properties = schema.get("properties", {})
    for name in schema.get("required", []):
        if name not in arguments:
            return "invalid_arguments"
    for name, value in arguments.items():
        expected = properties.get(name, {}).get("type")
        if expected == "string" and not isinstance(value, str):
            return "invalid_arguments"
        if expected == "integer" and (not isinstance(value, int) or isinstance(value, bool)):
            return "invalid_arguments"
        if expected == "number" and (not isinstance(value, (int, float)) or isinstance(value, bool)):
            return "invalid_arguments"
        if expected == "boolean" and not isinstance(value, bool):
            return "invalid_arguments"
    return None


class RuntimeToolExecutor:
    def __init__(self, *, tools: Mapping[str, RuntimeTool], permission_service: ToolPermissionService | None = None, domain_authorizer: DomainAuthorizer | None = None) -> None:
        self._tools = tools
        self._permission_service = permission_service
        self._domain_authorizer = domain_authorizer

    async def execute(self, *, user_id: str, agent_id: str | None, call: ToolCall) -> ToolExecutionResult:
        started = monotonic()
        tool = self._tools.get(call.name)
        if tool is None:
            return ToolExecutionResult(call.name, False, error_code="tool_not_allowed", summary="Tool is not available for this profile.")
        validation_error = _validate(tool.parameters_schema, call.arguments)
        if validation_error:
            return ToolExecutionResult(call.name, False, error_code=validation_error, summary="Tool arguments are invalid.")
        if self._permission_service is not None and agent_id:
            permission = await self._permission_service.check(user_id, agent_id, call.name)
            if not permission["allowed"]:
                return ToolExecutionResult(call.name, False, error_code="permission_denied", summary="Tool permission denied.")
        if self._domain_authorizer is not None and not await self._domain_authorizer(tool, call.arguments):
            return ToolExecutionResult(call.name, False, error_code="policy_denied", summary="Domain policy denied this tool action.")
        try:
            data = await asyncio.wait_for(tool.handler(user_id, call.arguments), timeout=tool.timeout_seconds)
            # The model receives structured data in-memory, but persistent trace must not
            # retain memory bodies, messages, tokens, or other raw tool payload values.
            keys = ", ".join(sorted(str(key) for key in data.keys())[:20])
            summary = f"Tool completed; result fields: {keys}."[: tool.max_result_chars]
            return ToolExecutionResult(call.name, True, data=dict(data), summary=summary, duration_ms=int((monotonic() - started) * 1000))
        except TimeoutError:
            return ToolExecutionResult(call.name, False, error_code="tool_timeout", summary="Tool timed out.", duration_ms=int((monotonic() - started) * 1000))
        except Exception as exc:
            return ToolExecutionResult(call.name, False, error_code=classify_exception(exc).value, summary="Tool execution failed.", duration_ms=int((monotonic() - started) * 1000))
