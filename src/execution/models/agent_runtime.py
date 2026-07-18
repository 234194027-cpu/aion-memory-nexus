"""Persistent, non-CoT execution records for the V2 Agent Runtime."""
from __future__ import annotations

from enum import Enum as PyEnum

from sqlalchemy import Column, DateTime, Enum, Float, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint, func

from src.shared.db.database import Base


class AgentRole(PyEnum):
    CONVERSATIONAL = "conversational"
    WORKING = "working"


class AgentSessionStatus(PyEnum):
    ACTIVE = "active"
    COMPLETED = "completed"
    FAILED = "failed"
    NEEDS_REVIEW = "needs_review"
    CANCELLED = "cancelled"


class AgentRunStatus(PyEnum):
    CREATED = "created"
    BUILDING_CONTEXT = "building_context"
    THINKING = "thinking"
    VALIDATING_TOOL_CALLS = "validating_tool_calls"
    AUTHORIZING = "authorizing"
    EXECUTING_TOOLS = "executing_tools"
    OBSERVING = "observing"
    FINALIZING = "finalizing"
    COMPLETED = "completed"
    NEEDS_RETRY = "needs_retry"
    NEEDS_REVIEW = "needs_review"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AgentStepType(PyEnum):
    MODEL = "model"
    TOOL = "tool"
    POLICY = "policy"
    FINAL = "final"


class AgentStepStatus(PyEnum):
    SUCCESS = "success"
    BLOCKED = "blocked"
    FAILED = "failed"


class AgentHandoffStatus(PyEnum):
    ACTIVE = "active"
    RESOLVED = "resolved"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


def _enum_values(enum_type: type[PyEnum]) -> list[str]:
    return [item.value for item in enum_type]


class AgentSession(Base):
    __tablename__ = "agent_sessions"

    id = Column(String(64), primary_key=True)
    user_id = Column(String(64), nullable=False, index=True)
    agent_role = Column(Enum(AgentRole, values_callable=_enum_values), nullable=False)
    channel = Column(String(32), nullable=False, default="system")
    channel_session_key = Column(String(128), nullable=True)
    status = Column(Enum(AgentSessionStatus, values_callable=_enum_values), nullable=False, default=AgentSessionStatus.ACTIVE)
    goal = Column(Text, nullable=True)
    context_payload = Column(JSON, nullable=True)
    context_version = Column(String(32), nullable=False, default="runtime-v1")
    started_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
    ended_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_agent_sessions_user_role_status", "user_id", "agent_role", "status"),
        Index("ix_agent_sessions_channel_key", "channel", "channel_session_key"),
    )


class AgentRun(Base):
    __tablename__ = "agent_runs"

    id = Column(String(64), primary_key=True)
    session_id = Column(String(64), ForeignKey("agent_sessions.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(String(64), nullable=False, index=True)
    trigger_type = Column(String(32), nullable=False)
    trigger_id = Column(String(128), nullable=True)
    model = Column(String(128), nullable=True)
    status = Column(Enum(AgentRunStatus, values_callable=_enum_values), nullable=False, default=AgentRunStatus.CREATED)
    step_count = Column(Integer, nullable=False, default=0)
    model_call_count = Column(Integer, nullable=False, default=0)
    tool_call_count = Column(Integer, nullable=False, default=0)
    input_tokens = Column(Integer, nullable=False, default=0)
    output_tokens = Column(Integer, nullable=False, default=0)
    cost = Column(Float, nullable=True)
    error_code = Column(String(64), nullable=True)
    evidence_payload = Column(JSON, nullable=True)
    started_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    ended_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_agent_runs_session_trigger", "session_id", "trigger_type", "trigger_id"),
        Index("ix_agent_runs_user_status", "user_id", "status"),
    )


class AgentStep(Base):
    __tablename__ = "agent_steps"

    id = Column(String(64), primary_key=True)
    run_id = Column(String(64), ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    step_no = Column(Integer, nullable=False)
    step_type = Column(Enum(AgentStepType, values_callable=_enum_values), nullable=False)
    tool_name = Column(String(96), nullable=True)
    arguments_hash = Column(String(64), nullable=True)
    result_summary = Column(Text, nullable=True)
    status = Column(Enum(AgentStepStatus, values_callable=_enum_values), nullable=False)
    error_code = Column(String(64), nullable=True)
    duration_ms = Column(Integer, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("run_id", "step_no", name="uq_agent_steps_run_step"),
    )


class AgentHandoff(Base):
    __tablename__ = "agent_handoffs"

    id = Column(String(64), primary_key=True)
    user_id = Column(String(64), nullable=False, index=True)
    source_run_id = Column(String(64), ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    source_event_id = Column(String(64), nullable=True, index=True)
    handoff_type = Column(String(48), nullable=False)
    mode = Column(String(16), nullable=False, default="shadow")
    priority = Column(Integer, nullable=False, default=0)
    question = Column(Text, nullable=False)
    evidence_payload = Column(JSON, nullable=True)
    case_id = Column(
        String(64),
        ForeignKey("memory_work_cases.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    evidence_requirements = Column(JSON, nullable=False, default=list)
    resolution_condition = Column(Text, nullable=True)
    sensitivity_limit = Column(String(16), nullable=False, default="normal")
    attempt_count = Column(Integer, nullable=False, default=0)
    next_eligible_at = Column(DateTime(timezone=True), nullable=True)
    asked_at = Column(DateTime(timezone=True), nullable=True)
    responded_at = Column(DateTime(timezone=True), nullable=True)
    status = Column(Enum(AgentHandoffStatus, values_callable=_enum_values), nullable=False, default=AgentHandoffStatus.ACTIVE)
    resolved_by_event_id = Column(String(64), nullable=True)
    expires_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("ix_agent_handoffs_user_mode_status", "user_id", "mode", "status"),
        Index("ix_agent_handoffs_event_type", "source_event_id", "handoff_type"),
    )
