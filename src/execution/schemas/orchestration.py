"""Pydantic v2 schemas for Gen 3 Orchestration (Multi-Agent + Simulation + Tool Permission)."""
from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Multi-Agent Orchestration
# ---------------------------------------------------------------------------


class MultiAgentRunRequest(BaseModel):
    question: str = Field(..., min_length=1)
    agent_ids: Optional[List[str]] = None
    max_agents: int = Field(default=3, ge=1, le=10)
    recall_level: str = Field(default="work_context")
    execution_mode: str = Field(default="parallel")
    roles_to_activate: Optional[List[str]] = None
    writeback_to_memory: bool = Field(default=True)


class MultiAgentDraft(BaseModel):
    agent_id: str
    agent_name: str
    agent_type: Optional[str] = None
    draft: str
    confidence: float
    warnings: List[str] = Field(default_factory=list)


class MultiAgentRunResponse(BaseModel):
    user_id: str
    question: str
    drafts: List[MultiAgentDraft]
    final_advice: str
    confidence: float
    warnings: List[str]
    meta: Dict
    # v3 新增
    execution_mode: Optional[str] = None
    role_outputs: Optional[Dict] = None
    writeback_results: Optional[Dict] = None


# ---------------------------------------------------------------------------
# Simulation Engine
# ---------------------------------------------------------------------------


class SimulateRequest(BaseModel):
    question: str = Field(..., min_length=1)
    horizon_days: int = Field(default=90, ge=1, le=365)


class SimulateResponse(BaseModel):
    user_id: str
    question: str
    baseline: str
    counterfactual: str
    predicted_outcome: str
    supporting_memories: List[str]
    supporting_decisions: List[str]
    confidence: float
    warnings: List[str]
    horizon_days: int
    run_id: Optional[str] = None
    created_at: str
    # v3 新增
    risk_factors: List[str] = Field(default_factory=list)
    risk_level: str = "medium"
    similar_past_decisions: List[Dict] = Field(default_factory=list)
    historical_pattern_match: Dict = Field(default_factory=dict)


class SimulationListItem(BaseModel):
    id: str
    user_id: str
    question: str
    counterfactual: Optional[str] = None
    outcome: Optional[str] = None
    confidence: float
    horizon_days: Optional[str] = None
    created_at: Optional[datetime] = None


class SimulationListResponse(BaseModel):
    simulations: List[SimulationListItem]
    total: int


class SimulationDetailResponse(SimulationListItem):
    baseline_summary: Optional[str] = None
    linked_memory_ids: Optional[str] = None


# ---------------------------------------------------------------------------
# Tool Permission
# ---------------------------------------------------------------------------


class PermissionGrantRequest(BaseModel):
    agent_id: str = Field(..., min_length=1)
    tool_name: str = Field(..., min_length=1)
    scope: str = Field(default="allow")


class PermissionRevokeRequest(BaseModel):
    agent_id: str = Field(..., min_length=1)
    tool_name: str = Field(..., min_length=1)


class PermissionItem(BaseModel):
    id: str
    user_id: str
    agent_id: str
    tool_name: str
    scope: str
    created_at: Optional[datetime] = None


class PermissionListResponse(BaseModel):
    permissions: List[PermissionItem]
    total: int


class PermissionCheckRequest(BaseModel):
    agent_id: str = Field(..., min_length=1)
    tool_name: str = Field(..., min_length=1)


class PermissionCheckResponse(BaseModel):
    user_id: str
    agent_id: str
    tool_name: str
    allowed: bool
    source: str


# ---------------------------------------------------------------------------
# Tool Layer
# ---------------------------------------------------------------------------


class ToolExecuteRequest(BaseModel):
    agent_id: str = Field(..., min_length=1)
    tool_name: str
    params: dict = {}


class ToolExecuteResponse(BaseModel):
    status: str
    result: Optional[dict | str | list] = None
    error: Optional[str] = None


class ToolListResponse(BaseModel):
    tools: List[dict]
