"""Anonymous, local-only capability-slice evaluation for the V2 second brain."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping


SECOND_BRAIN_EVAL_FIELDS = frozenset({
    "observation_id",
    "temporal_correct",
    "correction_correct",
    "relationship_correct",
    "open_loop_correct",
    "citation_correct",
    "reflection_support_complete",
    "reminder_sent",
    "reminder_useful",
    "reminder_closed",
    "reminder_repeated_within_7d",
})


@dataclass(frozen=True)
class SecondBrainEvalObservation:
    """One opaque human label; deliberately contains no user content or IDs."""

    observation_id: str
    temporal_correct: bool | None = None
    correction_correct: bool | None = None
    relationship_correct: bool | None = None
    open_loop_correct: bool | None = None
    citation_correct: bool | None = None
    reflection_support_complete: bool | None = None
    reminder_sent: bool | None = None
    reminder_useful: bool | None = None
    reminder_closed: bool | None = None
    reminder_repeated_within_7d: bool | None = None

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "SecondBrainEvalObservation":
        unsupported = sorted(set(value).difference(SECOND_BRAIN_EVAL_FIELDS))
        if unsupported:
            raise ValueError(f"unsupported fields: {', '.join(unsupported)}")
        observation_id = value.get("observation_id")
        if not isinstance(observation_id, str) or not observation_id.strip():
            raise ValueError("observation_id must be a non-empty string")
        fields: dict[str, bool | None] = {}
        for field in SECOND_BRAIN_EVAL_FIELDS - {"observation_id"}:
            raw = value.get(field)
            if raw is not None and not isinstance(raw, bool):
                raise ValueError(f"{field} must be true, false, or omitted")
            fields[field] = raw
        if fields["reminder_sent"] is False and any(
            fields[field] is not None
            for field in ("reminder_useful", "reminder_closed", "reminder_repeated_within_7d")
        ):
            raise ValueError("reminder outcome labels require reminder_sent=true")
        return cls(observation_id=observation_id, **fields)


@dataclass(frozen=True)
class SecondBrainEvalMetrics:
    observation_count: int
    temporal_accuracy: float | None
    correction_accuracy: float | None
    relationship_accuracy: float | None
    open_loop_accuracy: float | None
    citation_accuracy: float | None
    reflection_support_coverage: float | None
    reminder_usefulness: float | None
    reminder_close_rate: float | None
    reminder_seven_day_repeat_rate: float | None

    def to_dict(self) -> dict[str, float | int | None]:
        return {
            "observation_count": self.observation_count,
            "temporal_accuracy": self.temporal_accuracy,
            "correction_accuracy": self.correction_accuracy,
            "relationship_accuracy": self.relationship_accuracy,
            "open_loop_accuracy": self.open_loop_accuracy,
            "citation_accuracy": self.citation_accuracy,
            "reflection_support_coverage": self.reflection_support_coverage,
            "reminder_usefulness": self.reminder_usefulness,
            "reminder_close_rate": self.reminder_close_rate,
            "reminder_seven_day_repeat_rate": self.reminder_seven_day_repeat_rate,
        }


def _mean(items: Iterable[bool | None]) -> float | None:
    labels = [item for item in items if item is not None]
    return sum(labels) / len(labels) if labels else None


def compute_second_brain_eval_metrics(
    observations: Iterable[SecondBrainEvalObservation],
) -> SecondBrainEvalMetrics:
    items = tuple(observations)
    sent = tuple(item for item in items if item.reminder_sent is True)
    return SecondBrainEvalMetrics(
        observation_count=len(items),
        temporal_accuracy=_mean(item.temporal_correct for item in items),
        correction_accuracy=_mean(item.correction_correct for item in items),
        relationship_accuracy=_mean(item.relationship_correct for item in items),
        open_loop_accuracy=_mean(item.open_loop_correct for item in items),
        citation_accuracy=_mean(item.citation_correct for item in items),
        reflection_support_coverage=_mean(item.reflection_support_complete for item in items),
        reminder_usefulness=_mean(item.reminder_useful for item in sent),
        reminder_close_rate=_mean(item.reminder_closed for item in sent),
        reminder_seven_day_repeat_rate=_mean(item.reminder_repeated_within_7d for item in sent),
    )
