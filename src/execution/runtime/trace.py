"""Trace persistence with summaries only; never stores chain-of-thought."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from src.execution.models.agent_runtime import (
    AgentRun,
    AgentRunStatus,
    AgentSession,
    AgentStep,
    AgentStepStatus,
    AgentStepType,
)
from src.shared.ids.id_generator import generate_id


class RuntimeTraceStore(Protocol):
    async def start_session(self, *, session_id: str, user_id: str, role: str, channel: str, channel_session_key: str | None, goal: str | None, context_version: str = "runtime-v1") -> None: ...
    async def start_run(self, *, run_id: str, session_id: str, user_id: str, trigger_type: str, trigger_id: str | None, model: str | None) -> None: ...
    async def add_step(self, *, run_id: str, step_no: int, step_type: AgentStepType, status: AgentStepStatus, tool_name: str | None = None, arguments_hash: str | None = None, result_summary: str | None = None, error_code: str | None = None, duration_ms: int | None = None) -> None: ...
    async def finish_run(self, *, run_id: str, status: AgentRunStatus, error_code: str | None, step_count: int, model_calls: int, tool_calls: int, input_tokens: int, output_tokens: int, cost: float, evidence_payload: dict | None = None) -> None: ...


@dataclass(slots=True)
class InMemoryTraceStore:
    sessions: list[dict] = field(default_factory=list)
    runs: list[dict] = field(default_factory=list)
    steps: list[dict] = field(default_factory=list)

    async def start_session(self, **payload) -> None:
        self.sessions.append(payload)

    async def start_run(self, **payload) -> None:
        self.runs.append(payload)

    async def add_step(self, **payload) -> None:
        self.steps.append(payload)

    async def finish_run(self, **payload) -> None:
        for run in self.runs:
            if run["run_id"] == payload["run_id"]:
                run.update(payload)


class SqlAlchemyTraceStore:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def start_session(self, *, session_id: str, user_id: str, role: str, channel: str, channel_session_key: str | None, goal: str | None, context_version: str = "runtime-v1") -> None:
        existing = await self.db.get(AgentSession, session_id)
        if existing is None:
            self.db.add(AgentSession(id=session_id, user_id=user_id, agent_role=role, channel=channel, channel_session_key=channel_session_key, goal=goal, context_version=context_version))
            await self.db.flush()
        else:
            existing.context_version = context_version
            await self.db.flush()

    async def start_run(self, *, run_id: str, session_id: str, user_id: str, trigger_type: str, trigger_id: str | None, model: str | None) -> None:
        self.db.add(AgentRun(id=run_id, session_id=session_id, user_id=user_id, trigger_type=trigger_type, trigger_id=trigger_id, model=model, status=AgentRunStatus.BUILDING_CONTEXT))
        await self.db.flush()

    async def add_step(self, *, run_id: str, step_no: int, step_type: AgentStepType, status: AgentStepStatus, tool_name: str | None = None, arguments_hash: str | None = None, result_summary: str | None = None, error_code: str | None = None, duration_ms: int | None = None) -> None:
        self.db.add(AgentStep(id=generate_id("ast"), run_id=run_id, step_no=step_no, step_type=step_type, tool_name=tool_name, arguments_hash=arguments_hash, result_summary=result_summary, status=status, error_code=error_code, duration_ms=duration_ms))
        await self.db.flush()

    async def finish_run(self, *, run_id: str, status: AgentRunStatus, error_code: str | None, step_count: int, model_calls: int, tool_calls: int, input_tokens: int, output_tokens: int, cost: float, evidence_payload: dict | None = None) -> None:
        run = await self.db.get(AgentRun, run_id)
        if run is None:
            return
        run.status = status
        run.error_code = error_code
        run.step_count = step_count
        run.model_call_count = model_calls
        run.tool_call_count = tool_calls
        run.input_tokens = input_tokens
        run.output_tokens = output_tokens
        run.cost = cost
        run.evidence_payload = evidence_payload
        run.ended_at = datetime.now(timezone.utc)
        await self.db.flush()
