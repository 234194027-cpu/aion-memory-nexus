"""Process-local, privacy-safe operational counters without external dependencies."""

from __future__ import annotations

from collections import Counter
import re
from threading import Lock


_SAFE_METRIC_NAME = re.compile(r"^[a-z0-9_]{1,64}$")


def _safe_name(value: str) -> str:
    return value if _SAFE_METRIC_NAME.fullmatch(value) else "other"


class RuntimeMetrics:
    def __init__(self) -> None:
        self._lock = Lock()
        self.requests_total = 0
        self.requests_errors_total = 0
        self.request_durations: list[float] = []
        self.external_calls = Counter()
        self.external_failures = Counter()
        self.tasks_completed = Counter()
        self.tasks_failed = Counter()

    def record_request(self, duration: float, error: bool = False) -> None:
        with self._lock:
            self.requests_total += 1
            if error:
                self.requests_errors_total += 1
            self.request_durations.append(duration)
            if len(self.request_durations) > 1000:
                self.request_durations = self.request_durations[-1000:]

    def record_external_call(self, name: str, *, failed: bool = False) -> None:
        name = _safe_name(name)
        with self._lock:
            self.external_calls[name] += 1
            if failed:
                self.external_failures[name] += 1

    def record_task(self, name: str, *, failed: bool = False) -> None:
        name = _safe_name(name)
        with self._lock:
            (self.tasks_failed if failed else self.tasks_completed)[name] += 1

    def format_prometheus(self) -> str:
        with self._lock:
            average = sum(self.request_durations) / len(self.request_durations) if self.request_durations else 0.0
            lines = [
                "# HELP requests_total Total HTTP requests",
                "# TYPE requests_total counter",
                f"requests_total {self.requests_total}",
                "# HELP requests_errors_total Total HTTP errors (5xx or unhandled)",
                "# TYPE requests_errors_total counter",
                f"requests_errors_total {self.requests_errors_total}",
                "# HELP request_duration_seconds Average request duration over recent samples",
                "# TYPE request_duration_seconds gauge",
                f"request_duration_seconds {average}",
                "# HELP life_memory_external_calls_total External calls by safe operation name",
                "# TYPE life_memory_external_calls_total counter",
            ]
            for name, count in sorted(self.external_calls.items()):
                lines.append(f'life_memory_external_calls_total{{operation="{name}"}} {count}')
            lines.extend([
                "# HELP life_memory_external_failures_total Failed external calls by safe operation name",
                "# TYPE life_memory_external_failures_total counter",
            ])
            for name, count in sorted(self.external_failures.items()):
                lines.append(f'life_memory_external_failures_total{{operation="{name}"}} {count}')
            lines.extend([
                "# HELP life_memory_tasks_completed_total Completed background tasks by safe task name",
                "# TYPE life_memory_tasks_completed_total counter",
            ])
            for name, count in sorted(self.tasks_completed.items()):
                lines.append(f'life_memory_tasks_completed_total{{task="{name}"}} {count}')
            lines.extend([
                "# HELP life_memory_tasks_failed_total Failed background tasks by safe task name",
                "# TYPE life_memory_tasks_failed_total counter",
            ])
            for name, count in sorted(self.tasks_failed.items()):
                lines.append(f'life_memory_tasks_failed_total{{task="{name}"}} {count}')
            return "\n".join(lines) + "\n"


runtime_metrics = RuntimeMetrics()
