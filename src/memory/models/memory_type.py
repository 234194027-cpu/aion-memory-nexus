"""Stable memory type vocabulary shared by work cases and formal memories."""

from enum import Enum as PyEnum


class MemoryType(PyEnum):
    DECISION = "decision"
    PREFERENCE = "preference"
    FACT = "fact"
    INSIGHT = "insight"
    TASK = "task"
    PROJECT_CONTEXT = "project_context"
    PRINCIPLE = "principle"
    CORRECTION = "correction"
    TIMELINE_EVENT = "timeline_event"
    PERSONA_HYPOTHESIS = "persona_hypothesis"
