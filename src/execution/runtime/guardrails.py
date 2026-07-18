"""Loop circuit breakers that never inspect or store model hidden reasoning."""
from __future__ import annotations

from collections import Counter

from src.shared.errors.error_classification import ClassifiedError, ErrorClass


class RuntimeGuardrails:
    def __init__(self, *, max_same_failed_call: int = 2, max_same_call: int = 3) -> None:
        self.max_same_failed_call = max_same_failed_call
        self.max_same_call = max_same_call
        self._calls: Counter[str] = Counter()
        self._failures: Counter[str] = Counter()

    def observe_tool(self, signature: str, *, success: bool) -> None:
        self._calls[signature] += 1
        if not success:
            self._failures[signature] += 1
        if self._failures[signature] >= self.max_same_failed_call:
            raise ClassifiedError(ErrorClass.POLICY, "repeated failed tool call blocked")
        if self._calls[signature] > self.max_same_call:
            raise ClassifiedError(ErrorClass.POLICY, "repeated tool call blocked")
