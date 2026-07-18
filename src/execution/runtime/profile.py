"""Fixed internal runtime profiles; distinct from user-managed AgentProfile rows."""
from __future__ import annotations

from dataclasses import dataclass
from typing import FrozenSet

from src.execution.models.agent_runtime import AgentRole
from .personality import CONVERSATIONAL_PERSONALITY_V1
from .prompt_registry import get_prompt


@dataclass(frozen=True, slots=True)
class AgentProfileSpec:
    name: str
    role: AgentRole
    system_prompt: str
    allowed_tools: FrozenSet[str]
    max_steps: int
    max_model_calls: int
    max_tool_calls: int
    max_wall_time_seconds: float
    max_total_tokens: int
    max_cost: float | None
    may_reply_to_user: bool
    may_propose_memory: bool
    prompt_id: str = ""
    prompt_version: str = ""


CONVERSATIONAL_PROFILE = AgentProfileSpec(
    name="conversational",
    role=AgentRole.CONVERSATIONAL,
    system_prompt=CONVERSATIONAL_PERSONALITY_V1.text,
    allowed_tools=frozenset({
        "retrieve_memories",
        "get_persona",
        "get_conflicts",
        "get_tasks",
        "get_timeline",
        "get_attention",
        "search_source_documents",
        "get_unconfirmed_memory_clues",
    }),
    max_steps=8,
    max_model_calls=8,
    max_tool_calls=12,
    max_wall_time_seconds=45,
    max_total_tokens=32_000,
    max_cost=None,
    may_reply_to_user=True,
    may_propose_memory=False,
    prompt_id=CONVERSATIONAL_PERSONALITY_V1.prompt_id,
    prompt_version=CONVERSATIONAL_PERSONALITY_V1.version,
)

WORKING_PROFILE = AgentProfileSpec(
    name="working",
    role=AgentRole.WORKING,
    system_prompt=get_prompt("working-agent-core").text,
    allowed_tools=frozenset({
        "route_memory_case",
        "attach_case_evidence",
        "search_related_context",
        "evaluate_duplicate",
        "evaluate_conflict",
        "evaluate_governance",
        "request_evidence",
        "close_memory_case",
    }),
    max_steps=12,
    max_model_calls=12,
    max_tool_calls=20,
    max_wall_time_seconds=120,
    max_total_tokens=48_000,
    max_cost=None,
    may_reply_to_user=False,
    may_propose_memory=True,
    prompt_id=get_prompt("working-agent-core").prompt_id,
    prompt_version=get_prompt("working-agent-core").version,
)
