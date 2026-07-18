"""Per-run immutable-limit budget accounting."""
from __future__ import annotations

from dataclasses import dataclass, field
from time import monotonic

from src.shared.errors.error_classification import ClassifiedError, ErrorClass


@dataclass(slots=True)
class RuntimeBudget:
    max_steps: int
    max_model_calls: int
    max_tool_calls: int
    max_wall_time_seconds: float
    max_total_tokens: int
    max_cost: float | None = None
    started_monotonic: float = field(default_factory=monotonic)
    steps: int = 0
    model_calls: int = 0
    tool_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost: float = 0.0

    def _assert_time(self) -> None:
        if monotonic() - self.started_monotonic > self.max_wall_time_seconds:
            raise ClassifiedError(ErrorClass.BUDGET, "runtime wall-time budget exhausted")

    def before_model(self) -> None:
        self._assert_time()
        if self.steps >= self.max_steps or self.model_calls >= self.max_model_calls:
            raise ClassifiedError(ErrorClass.BUDGET, "runtime model-step budget exhausted")

    def record_model(self, *, input_tokens: int = 0, output_tokens: int = 0, cost: float = 0.0) -> None:
        self.steps += 1
        self.model_calls += 1
        self.input_tokens += max(0, input_tokens)
        self.output_tokens += max(0, output_tokens)
        self.cost += max(0.0, cost)
        self._assert_limits()

    def before_tool(self) -> None:
        self._assert_time()
        if self.tool_calls >= self.max_tool_calls:
            raise ClassifiedError(ErrorClass.BUDGET, "runtime tool-call budget exhausted")

    def record_tool(self) -> None:
        self.tool_calls += 1
        self._assert_limits()

    def _assert_limits(self) -> None:
        if self.input_tokens + self.output_tokens > self.max_total_tokens:
            raise ClassifiedError(ErrorClass.BUDGET, "runtime token budget exhausted")
        if self.max_cost is not None and self.cost > self.max_cost:
            raise ClassifiedError(ErrorClass.BUDGET, "runtime cost budget exhausted")
