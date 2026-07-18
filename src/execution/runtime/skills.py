"""Controlled cognitive Skills: versioned declarations, disabled until approved in code review."""
from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping


@dataclass(frozen=True, slots=True)
class CognitiveSkill:
    skill_id: str
    version: str
    applicable_roles: frozenset[str]
    allowed_tools: frozenset[str]
    input_contract: str
    output_contract: str
    evaluation_suite: str
    approved: bool = False


_SKILLS = MappingProxyType({
    "deep-interview": CognitiveSkill("deep-interview", "v1", frozenset({"conversational"}), frozenset({"retrieve_memories", "get_question_session"}), "one declared interview goal", "one user-safe question or no-op", "skill-deep-interview-v1"),
    "decision-review": CognitiveSkill("decision-review", "v1", frozenset({"conversational"}), frozenset({"retrieve_memories", "get_conflicts"}), "decision query", "evidence-grounded reflection", "skill-decision-review-v1"),
    "conflict-clarification": CognitiveSkill("conflict-clarification", "v1", frozenset({"conversational", "working"}), frozenset({"retrieve_memories", "get_conflicts", "detect_conflict"}), "conflict evidence", "clarification or review proposal", "skill-conflict-v1"),
    "weekly-review": CognitiveSkill("weekly-review", "v1", frozenset({"conversational"}), frozenset({"retrieve_memories", "get_tasks", "get_timeline"}), "time period", "review draft", "skill-weekly-review-v1"),
})


def list_skills() -> Mapping[str, CognitiveSkill]:
    return _SKILLS


def approved_skills_for(role: str) -> tuple[CognitiveSkill, ...]:
    return tuple(skill for skill in _SKILLS.values() if skill.approved and role in skill.applicable_roles)
