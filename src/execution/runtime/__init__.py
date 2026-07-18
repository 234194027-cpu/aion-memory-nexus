"""V2 role-neutral Agent Runtime."""

from .profile import AgentProfileSpec, CONVERSATIONAL_PROFILE, WORKING_PROFILE
from .runtime import AgentRuntime, RuntimeContext, RuntimeResult
from .feature_flags import is_runtime_enabled, require_runtime_enabled
from .conversation_agent import ConversationAnswer, reset_conversational_session, run_conversational_turn

__all__ = [
    "AgentProfileSpec",
    "CONVERSATIONAL_PROFILE",
    "WORKING_PROFILE",
    "AgentRuntime",
    "RuntimeContext",
    "RuntimeResult",
    "is_runtime_enabled",
    "require_runtime_enabled",
    "ConversationAnswer",
    "run_conversational_turn",
    "reset_conversational_session",
]
