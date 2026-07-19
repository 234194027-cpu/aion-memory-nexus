"""Content-free V2.5.1 quality observations and aggregate metrics."""
from __future__ import annotations

from dataclasses import dataclass
from statistics import quantiles
from typing import Any, Iterable, Mapping


ALLOWED_FIELDS = frozenset({
    "observation_id",
    "scenario_type",
    "context_continuous",
    "memory_hit",
    "source_covered",
    "assistant_fact_leak",
    "wrong_merge",
    "correction_correct",
    "proactive_relevant",
    "response_latency_ms",
    "working_model_calls",
    "retained_memory_tokens",
    "cleanup_safe",
})


@dataclass(frozen=True, slots=True)
class ConversationQualityObservation:
    observation_id: str
    scenario_type: str
    context_continuous: bool | None = None
    memory_hit: bool | None = None
    source_covered: bool | None = None
    assistant_fact_leak: bool | None = None
    wrong_merge: bool | None = None
    correction_correct: bool | None = None
    proactive_relevant: bool | None = None
    response_latency_ms: int | None = None
    working_model_calls: int | None = None
    retained_memory_tokens: int | None = None
    cleanup_safe: bool | None = None

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ConversationQualityObservation":
        extra = sorted(set(value).difference(ALLOWED_FIELDS))
        if extra:
            raise ValueError(f"unsupported fields: {', '.join(extra)}")
        observation_id = value.get("observation_id")
        scenario_type = value.get("scenario_type")
        if not isinstance(observation_id, str) or not observation_id.strip():
            raise ValueError("observation_id must be a non-empty string")
        if not isinstance(scenario_type, str) or not scenario_type.strip():
            raise ValueError("scenario_type must be a non-empty string")

        def optional_bool(name: str) -> bool | None:
            item = value.get(name)
            if item is not None and not isinstance(item, bool):
                raise ValueError(f"{name} must be boolean or omitted")
            return item

        def optional_int(name: str) -> int | None:
            item = value.get(name)
            if item is not None and (not isinstance(item, int) or item < 0):
                raise ValueError(f"{name} must be a non-negative integer or omitted")
            return item

        return cls(
            observation_id=observation_id,
            scenario_type=scenario_type,
            context_continuous=optional_bool("context_continuous"),
            memory_hit=optional_bool("memory_hit"),
            source_covered=optional_bool("source_covered"),
            assistant_fact_leak=optional_bool("assistant_fact_leak"),
            wrong_merge=optional_bool("wrong_merge"),
            correction_correct=optional_bool("correction_correct"),
            proactive_relevant=optional_bool("proactive_relevant"),
            response_latency_ms=optional_int("response_latency_ms"),
            working_model_calls=optional_int("working_model_calls"),
            retained_memory_tokens=optional_int("retained_memory_tokens"),
            cleanup_safe=optional_bool("cleanup_safe"),
        )


def compute_quality_metrics(observations: Iterable[ConversationQualityObservation]) -> dict[str, Any]:
    items = tuple(observations)

    def rate(field: str, *, invert: bool = False) -> float | None:
        values = [getattr(item, field) for item in items if getattr(item, field) is not None]
        if not values:
            return None
        score = sum(bool(value) for value in values) / len(values)
        return round(1.0 - score if invert else score, 4)

    latencies = sorted(item.response_latency_ms for item in items if item.response_latency_ms is not None)
    p95 = None
    if latencies:
        p95 = latencies[-1] if len(latencies) < 20 else round(quantiles(latencies, n=20, method="inclusive")[18])
    calls = [item.working_model_calls for item in items if item.working_model_calls is not None]
    tokens = [item.retained_memory_tokens for item in items if item.retained_memory_tokens is not None]
    return {
        "schema": "conversation-quality/v2.5.1",
        "observation_count": len(items),
        "scenario_count": len({item.scenario_type for item in items}),
        "context_continuity_rate": rate("context_continuous"),
        "memory_hit_rate": rate("memory_hit"),
        "source_coverage": rate("source_covered"),
        "assistant_fact_leak_rate": rate("assistant_fact_leak"),
        "wrong_merge_rate": rate("wrong_merge"),
        "correction_accuracy": rate("correction_correct"),
        "proactive_relevance": rate("proactive_relevant"),
        "response_p95_ms": p95,
        "average_working_model_calls": round(sum(calls) / len(calls), 3) if calls else None,
        "average_tokens_per_retained_memory": round(sum(tokens) / len(tokens), 3) if tokens else None,
        "cleanup_safety_rate": rate("cleanup_safe"),
    }
