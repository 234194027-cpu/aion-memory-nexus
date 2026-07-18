"""Versioned policy constants for memory read, provenance, and auto-commit decisions."""

from __future__ import annotations

from src.memory.models.memory_type import MemoryType
from src.memory.models.raw_event import SensitivityLevel, VisibilityScope


POLICY_VERSION = "memory-governance-v2.4"
DEFAULT_RECALL_LEVEL = "work_context"
VALID_RECALL_LEVELS = ("task_only", "work_context", "personal_context", "full_trusted")
RECALL_LEVEL_RANK = {level: index for index, level in enumerate(VALID_RECALL_LEVELS)}

RECALL_LEVEL_FILTER = {
    "task_only": [MemoryType.TASK, MemoryType.FACT, MemoryType.PROJECT_CONTEXT],
    "work_context": [
        MemoryType.DECISION, MemoryType.INSIGHT, MemoryType.FACT, MemoryType.PROJECT_CONTEXT,
        MemoryType.PRINCIPLE, MemoryType.PREFERENCE,
    ],
    "personal_context": [
        MemoryType.DECISION, MemoryType.INSIGHT, MemoryType.FACT, MemoryType.PROJECT_CONTEXT,
        MemoryType.PRINCIPLE, MemoryType.PREFERENCE, MemoryType.PERSONA_HYPOTHESIS,
    ],
    "full_trusted": None,
}

SENSITIVITY_BY_RECALL = {
    "task_only": [SensitivityLevel.PUBLIC],
    "work_context": [SensitivityLevel.PUBLIC, SensitivityLevel.NORMAL],
    "personal_context": [SensitivityLevel.PUBLIC, SensitivityLevel.NORMAL, SensitivityLevel.PRIVATE],
    "full_trusted": [SensitivityLevel.PUBLIC, SensitivityLevel.NORMAL, SensitivityLevel.PRIVATE, SensitivityLevel.SENSITIVE],
}

# Visibility is independent from sensitivity. A caller must satisfy both dimensions.
VISIBILITY_BY_RECALL = {
    "task_only": [VisibilityScope.PUBLIC, VisibilityScope.PROJECT],
    "work_context": [VisibilityScope.PUBLIC, VisibilityScope.PROJECT],
    "personal_context": [VisibilityScope.PUBLIC, VisibilityScope.PROJECT, VisibilityScope.PERSONAL],
    "full_trusted": [VisibilityScope.PUBLIC, VisibilityScope.PROJECT, VisibilityScope.PERSONAL, VisibilityScope.PRIVATE],
}

# AI/agent supplied material can remain case evidence, but it must never
# become a user fact solely from a model score.
FORMAL_MEMORY_BLOCKED_EPISTEMIC_STATUSES = frozenset({
    "agent_assertion",
    "assistant_supplied",
    "external_claim",
    "model_inference",
})

# These labels describe provenance, not factual truth. They must never turn an LLM output into a user fact.
SOURCE_TRUST_CLASS = {
    "manual": "user_assertion",
    "codex": "assistant_supplied",
    "chatgpt": "assistant_supplied",
    "openclaw": "agent_assertion",
    "agent_api": "agent_assertion",
    "conversation": "user_assertion",
    "obsidian": "user_imported",
    "file_import": "user_imported",
}

EPISTEMIC_STATUS_BY_TRUST = {
    "user_assertion": "user_assertion",
    "user_imported": "user_imported",
    "agent_assertion": "agent_assertion",
    "assistant_supplied": "assistant_supplied",
}


def allowed_read_scope_ceiling(
    allowed_read_scopes: object,
    *,
    default_recall_level: object,
) -> str:
    """Return the highest explicitly permitted recall level for an Agent.

    Existing profiles have an empty ``allowed_read_scopes`` value and historically
    relied on ``default_recall_level`` alone, so an empty value deliberately keeps
    that behavior. Once a profile contains a policy, malformed or unknown entries
    fail closed to ``task_only`` instead of silently granting a wider read scope.

    Supported stored forms are ``["work_context"]`` and
    ``[{"recall_level": "work_context", "enabled": true}]``. This is an
    internal interpretation of the pre-existing JSON column; no API payload is
    changed.
    """
    default = normalize_recall_level(default_recall_level, fallback="task_only")
    if not allowed_read_scopes:
        return default

    entries = [allowed_read_scopes] if isinstance(allowed_read_scopes, dict) else allowed_read_scopes
    if not isinstance(entries, list):
        return "task_only"

    allowed_levels: list[str] = []
    for entry in entries:
        if isinstance(entry, str):
            candidate, enabled = entry, True
        elif isinstance(entry, dict):
            candidate = entry.get("recall_level", entry.get("level"))
            enabled = entry.get("enabled", True) is not False
        else:
            continue
        normalized = str(candidate or "").strip().lower()
        if enabled and normalized in VALID_RECALL_LEVELS:
            allowed_levels.append(normalized)

    if not allowed_levels:
        return "task_only"
    configured_ceiling = max(allowed_levels, key=RECALL_LEVEL_RANK.__getitem__)
    return clamp_recall_level(configured_ceiling, default)


def normalize_recall_level(value: object, *, fallback: str = DEFAULT_RECALL_LEVEL) -> str:
    candidate = str(value or "").strip().lower()
    return candidate if candidate in VALID_RECALL_LEVELS else fallback


def clamp_recall_level(requested: object, allowed: object | None) -> str:
    requested_value = normalize_recall_level(requested)
    allowed_value = normalize_recall_level(allowed, fallback="task_only")
    return requested_value if RECALL_LEVEL_RANK[requested_value] <= RECALL_LEVEL_RANK[allowed_value] else allowed_value


def source_trust_class(source_type: object) -> str:
    value = getattr(source_type, "value", source_type)
    return SOURCE_TRUST_CLASS.get(str(value or "").lower(), "unclassified")


def derive_epistemic_status(
    source_type: object,
    *,
    memory_type: object = None,
    direct_user_confirmation: bool = False,
) -> str:
    if direct_user_confirmation:
        return "user_confirmed"
    memory_type_value = getattr(memory_type, "value", memory_type)
    if memory_type_value == "persona_hypothesis":
        return "model_inference"
    return EPISTEMIC_STATUS_BY_TRUST.get(source_trust_class(source_type), "legacy_unclassified")
