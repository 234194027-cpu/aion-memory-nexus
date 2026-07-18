"""Composition root for the V2 conversational and working runtimes."""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from src.execution.services.builtin_runtime_permission import BuiltinRuntimePermissionService

from .model import RuntimeModel
from .profile import CONVERSATIONAL_PROFILE
from .runtime import AgentRuntime
from .tools.conversation import build_conversation_tools
from .tools.memory_work import build_memory_work_tools
from .tools.registry import ToolRegistry
from .trace import SqlAlchemyTraceStore


def build_conversational_runtime(db: AsyncSession, model: RuntimeModel, *, source_message: str | None = None, channel: str = "system") -> AgentRuntime:
    return AgentRuntime(
        model=model,
        registry=ToolRegistry(build_conversation_tools(db, source_message=source_message, channel=channel)),
        trace_store=SqlAlchemyTraceStore(db),
        permission_service=BuiltinRuntimePermissionService(db, allowed_tools=CONVERSATIONAL_PROFILE.allowed_tools),
    )


def build_working_runtime(db: AsyncSession, model: RuntimeModel, *, shadow: bool) -> AgentRuntime:
    from .profile import WORKING_PROFILE

    return AgentRuntime(
        model=model,
        registry=ToolRegistry(build_memory_work_tools(db, shadow=shadow)),
        trace_store=SqlAlchemyTraceStore(db),
        permission_service=BuiltinRuntimePermissionService(db, allowed_tools=WORKING_PROFILE.allowed_tools),
    )
