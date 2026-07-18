"""Versioned, server-owned conversational personality contract."""
from __future__ import annotations

from dataclasses import dataclass

from .prompt_registry import get_prompt


@dataclass(frozen=True, slots=True)
class PersonalityContract:
    prompt_id: str
    version: str
    text: str


_CONVERSATIONAL_PROMPT = get_prompt("conversational-agent-core")

CONVERSATIONAL_PERSONALITY_V1 = PersonalityContract(
    prompt_id=_CONVERSATIONAL_PROMPT.prompt_id,
    version=_CONVERSATIONAL_PROMPT.version,
    text=_CONVERSATIONAL_PROMPT.text,
)
