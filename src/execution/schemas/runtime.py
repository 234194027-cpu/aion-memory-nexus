"""Read-only API schemas for V2 runtime observability."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class RuntimeStatusResponse(BaseModel):
    runtime_enabled: bool
    conversational_enabled: bool
    working_shadow_enabled: bool
    working_active_enabled: bool
    profiles: list[str]


class RuntimeRunItem(BaseModel):
    id: str
    session_id: str
    trigger_type: str
    trigger_id: str | None = None
    model: str | None = None
    status: str
    step_count: int
    model_call_count: int
    tool_call_count: int
    input_tokens: int
    output_tokens: int
    cost: float | None = None
    error_code: str | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None


class RuntimeRunListResponse(BaseModel):
    runs: list[RuntimeRunItem]
    total: int


class RuntimeStepItem(BaseModel):
    step_no: int
    step_type: str
    tool_name: str | None = None
    status: str
    error_code: str | None = None
    duration_ms: int | None = None
    result_summary: str | None = None


class RuntimeRunDetailResponse(RuntimeRunItem):
    steps: list[RuntimeStepItem] = Field(default_factory=list)


class ShadowReportResponse(BaseModel):
    total_shadow_runs: int
    business_state_counts: dict[str, int] = Field(default_factory=dict)
    shadow_memory_proposal_count: int
    formal_memory_count_for_compared_events: int
    compared_event_count: int
    shadow_failed_run_count: int
    shadow_conflict_run_count: int
    governed_decision_count_for_compared_events: int
    conflict_decision_count: int
    duplicate_metric_available: bool
    duplicate_formal_memory_count: int | None = None


class ConversationTurnRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    session_key: str = Field(min_length=1, max_length=128)
    message_id: str | None = Field(default=None, min_length=1, max_length=128)


class ConversationCitationEvidence(BaseModel):
    memory_id: str
    source_event_ids: list[str] = Field(default_factory=list)
    epistemic_status: str
    valid_from: datetime | None = None
    valid_until: datetime | None = None


class ConversationTurnResponse(BaseModel):
    text: str
    run_id: str
    turn_id: str
    session_id: str
    response_mode: str
    confidence: str
    citations: list[str] = Field(default_factory=list)
    citation_evidence: list[ConversationCitationEvidence] = Field(default_factory=list)


class ConversationStateResponse(BaseModel):
    summary: str | None = None
    open_items: list[dict] = Field(default_factory=list)
    last_reflected_at: datetime | None = None
    proactive_sent_today: int
    proactive_daily_limit: int
    proactive_remaining_today: int


class AttentionItemResponse(BaseModel):
    source_type: str
    source_id: str
    priority: int
    prompt: str


class AttentionListResponse(BaseModel):
    items: list[AttentionItemResponse] = Field(default_factory=list)


class RuntimeMetricsResponse(BaseModel):
    run_count: int
    status_counts: dict[str, int] = Field(default_factory=dict)
    error_counts: dict[str, int] = Field(default_factory=dict)
    tool_call_count: int
    blocked_tool_call_count: int
    tool_duration_p95_ms: int | None = None
    input_tokens: int
    output_tokens: int
    cost: float


class OpenLoopItemResponse(BaseModel):
    source_type: str
    source_id: str
    title: str
    next_step: str
    priority: int
    due_at: datetime | None = None


class OpenLoopListResponse(BaseModel):
    items: list[OpenLoopItemResponse] = Field(default_factory=list)


class InsightProposalItem(BaseModel):
    id: str
    title: str
    summary: str
    support_memory_ids: list[str] = Field(default_factory=list)
    counter_memory_ids: list[str] = Field(default_factory=list)
    confidence: float
    invalidation_condition: str
    status: str


class InsightStatusRequest(BaseModel):
    status: str = Field(pattern="^(accepted|corrected|ignored|closed)$")
