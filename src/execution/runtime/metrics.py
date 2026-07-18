"""Privacy-safe aggregate observability for persisted V2 runs."""
from __future__ import annotations

from collections import Counter

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.execution.models.agent_runtime import AgentRun, AgentStep, AgentStepStatus


def _value(value: object) -> str:
    return str(getattr(value, "value", value))


async def build_runtime_metrics(db: AsyncSession, *, user_id: str) -> dict[str, object]:
    runs = list((await db.execute(select(AgentRun).where(AgentRun.user_id == user_id))).scalars())
    run_ids = [run.id for run in runs]
    steps = list((await db.execute(select(AgentStep).where(AgentStep.run_id.in_(run_ids)))).scalars()) if run_ids else []
    status_counts = Counter(_value(run.status) for run in runs)
    error_counts = Counter(run.error_code for run in runs if run.error_code)
    tool_calls = [step for step in steps if step.tool_name]
    blocked_tools = [step for step in tool_calls if step.status == AgentStepStatus.BLOCKED]
    durations = sorted(step.duration_ms for step in tool_calls if step.duration_ms is not None)
    p95 = durations[min(len(durations) - 1, max(0, int(len(durations) * 0.95) - 1))] if durations else None
    return {
        "run_count": len(runs),
        "status_counts": dict(sorted(status_counts.items())),
        "error_counts": dict(sorted(error_counts.items())),
        "tool_call_count": len(tool_calls),
        "blocked_tool_call_count": len(blocked_tools),
        "tool_duration_p95_ms": p95,
        "input_tokens": sum(run.input_tokens or 0 for run in runs),
        "output_tokens": sum(run.output_tokens or 0 for run in runs),
        "cost": sum(run.cost or 0.0 for run in runs),
    }
