"""Immutable definitions and per-profile filtered snapshots."""
from __future__ import annotations

from types import MappingProxyType
from typing import Mapping

from .base import RuntimeTool
from ..profile import AgentProfileSpec


class ToolRegistry:
    def __init__(self, tools: list[RuntimeTool] | None = None) -> None:
        definitions: dict[str, RuntimeTool] = {}
        for tool in tools or []:
            if tool.name in definitions:
                raise ValueError(f"duplicate runtime tool: {tool.name}")
            definitions[tool.name] = tool
        self._definitions: Mapping[str, RuntimeTool] = MappingProxyType(definitions)

    def snapshot_for(self, profile: AgentProfileSpec) -> Mapping[str, RuntimeTool]:
        return MappingProxyType({name: tool for name, tool in self._definitions.items() if name in profile.allowed_tools})
