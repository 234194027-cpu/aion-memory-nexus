"""Centralized, deny-by-default rollout gates for V2 runtime paths."""
from __future__ import annotations

from src.execution.models.agent_runtime import AgentRole
from src.shared.config import settings
from src.shared.errors.error_classification import ClassifiedError, ErrorClass


def is_runtime_enabled(role: AgentRole) -> bool:
    if not settings.AGENT_RUNTIME_ENABLED:
        return False
    if role == AgentRole.CONVERSATIONAL:
        return settings.CONVERSATIONAL_AGENT_ENABLED
    return settings.WORKING_AGENT_SHADOW_ENABLED or settings.WORKING_AGENT_ACTIVE_ENABLED


def is_working_active_enabled() -> bool:
    return bool(settings.AGENT_RUNTIME_ENABLED and settings.WORKING_AGENT_ACTIVE_ENABLED)


def require_runtime_enabled(role: AgentRole) -> None:
    if not is_runtime_enabled(role):
        raise ClassifiedError(ErrorClass.POLICY, "agent runtime rollout is disabled")
