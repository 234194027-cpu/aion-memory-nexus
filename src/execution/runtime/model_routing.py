"""Explainable model tier selection for V2; it never changes permissions or governance."""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ModelTier(StrEnum):
    RULES = "rules"
    LIGHT = "light"
    PRIMARY = "primary"
    STRONG_REVIEW = "strong_review"


@dataclass(frozen=True, slots=True)
class ModelRoute:
    tier: ModelTier
    reason: str
    requires_structured_output: bool


def route_model(*, role: str, high_impact: bool = False, conflict: bool = False, structured_output: bool = False) -> ModelRoute:
    if role == "working" and (high_impact or conflict):
        return ModelRoute(ModelTier.STRONG_REVIEW, "high-impact working result requires explicit review capability", True)
    if role == "working":
        return ModelRoute(ModelTier.PRIMARY, "working extraction requires governed structured output", True)
    if structured_output:
        return ModelRoute(ModelTier.PRIMARY, "tool calling requires structured output", True)
    if role == "conversational":
        return ModelRoute(ModelTier.PRIMARY, "conversational evidence synthesis", False)
    return ModelRoute(ModelTier.LIGHT, "low-risk classification", False)
