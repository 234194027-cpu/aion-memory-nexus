"""Explicit runtime tool contracts."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Mapping


ToolHandler = Callable[[str, Mapping[str, Any]], Awaitable[Mapping[str, Any]]]


@dataclass(frozen=True, slots=True)
class RuntimeTool:
    name: str
    description: str
    parameters_schema: Mapping[str, Any]
    handler: ToolHandler
    read_only: bool = True
    timeout_seconds: float = 15.0
    max_result_chars: int = 4_000


@dataclass(frozen=True, slots=True)
class ToolCall:
    name: str
    arguments: Mapping[str, Any]
    call_id: str | None = None


@dataclass(frozen=True, slots=True)
class ToolExecutionResult:
    tool_name: str
    ok: bool
    data: Mapping[str, Any] = field(default_factory=dict)
    error_code: str | None = None
    summary: str = ""
    duration_ms: int = 0
