"""Gen 3 / Cognitive OS Pydantic v2 schemas.

覆盖端点:
- /api/os/context/route
- /api/os/tasks (CRUD)
- /api/os/tasks/{id}/link-memory|decision
- /api/os/tasks/auto-extract
- /api/os/tasks/{id}/decompose|assign|complete
- /api/os/timeline (rebuild/get)
"""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Context Router
# ---------------------------------------------------------------------------


class ContextRouteRequest(BaseModel):
    message: str = Field(..., min_length=1)
    recent_history: Optional[List[dict]] = None
    persona: Optional[dict] = None
    memory_summary: Optional[List[str]] = None
    task_context: Optional[List[dict]] = None


class SelectedMemory(BaseModel):
    memory_id: str
    title: str
    importance: float
    relevance_reason: str


class SelectedAgent(BaseModel):
    agent_id: str
    agent_name: str
    agent_role: str
    reason: str


class ToolPermission(BaseModel):
    tool_name: str
    scope: str
    reason: str


class ExecutionStrategy(BaseModel):
    mode: str = "single"
    priority_order: List[str] = []
    estimated_steps: int = 1


class ContextRouteResponse(BaseModel):
    user_id: str
    message: str
    intent: str
    recall_level: str
    suggested_agent_type: str
    confidence: float
    rationale: str
    selected_memories: List[SelectedMemory] = []
    selected_agents: List[SelectedAgent] = []
    tool_permissions: List[ToolPermission] = []
    context_window_budget: int = 4000
    execution_strategy: ExecutionStrategy = ExecutionStrategy()
    blocked_info: List[str] = []
    meta: dict


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------


VALID_TASK_STATUSES = {"todo", "doing", "blocked", "done", "abandoned"}
VALID_TASK_PRIORITIES = {"P0", "P1", "P2", "P3"}


class TaskCreateRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = None
    priority: str = "P2"
    project_id: Optional[str] = None
    parent_task_id: Optional[str] = None
    linked_memory_ids: Optional[List[str]] = None
    linked_decision_ids: Optional[List[str]] = None
    due_at: Optional[datetime] = None


class TaskUpdateRequest(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    priority: Optional[str] = None
    status: Optional[str] = None
    project_id: Optional[str] = None
    due_at: Optional[datetime] = None


class TaskResponse(BaseModel):
    id: str
    user_id: str
    title: str
    description: Optional[str] = None
    status: str
    priority: str
    project_id: Optional[str] = None
    parent_task_id: Optional[str] = None
    linked_memory_ids: List[str] = []
    linked_decision_ids: List[str] = []
    due_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    assigned_agent_id: Optional[str] = None
    priority_score: float = 0.5
    sub_tasks_count: int = 0


class TaskListResponse(BaseModel):
    tasks: List[TaskResponse]
    total: int


class TaskAutoExtractRequest(BaseModel):
    days: int = Field(default=7, ge=1, le=90)
    limit: int = Field(default=10, ge=1, le=50)
    dry_run: bool = False


class TaskAutoExtractResponse(BaseModel):
    user_id: str
    days: int
    scanned: int
    created: int
    tasks: List[TaskResponse]


class TaskDecomposeRequest(BaseModel):
    max_sub_tasks: int = Field(default=5, ge=1, le=10)


class TaskDecomposeResponse(BaseModel):
    parent_task_id: str
    sub_tasks: List[TaskResponse]
    decomposition_rationale: str


class TaskCompleteRequest(BaseModel):
    result_summary: str = Field(..., min_length=1)


class TaskCompleteResponse(BaseModel):
    task: TaskResponse
    memory_id: str


# ---------------------------------------------------------------------------
# Timeline
# ---------------------------------------------------------------------------


VALID_TIMELINE_KINDS = {"memory", "decision", "task", "review", "event"}


class TimelineRebuildRequest(BaseModel):
    since_date: Optional[str] = None
    until_date: Optional[str] = None


class TimelineEntry(BaseModel):
    id: str
    user_id: str
    entry_date: str
    entry_kind: str
    ref_id: str
    title: str
    snippet: Optional[str] = None
    importance: float
    project_id: Optional[str] = None
    created_at: Optional[datetime] = None


class TimelineRebuildResponse(BaseModel):
    user_id: str
    entry_count: int
    by_date: dict
    highlights: List[TimelineEntry]


class TimelineListResponse(BaseModel):
    entries: List[TimelineEntry]
    total: int
