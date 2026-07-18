from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


class DecisionTrackRequest(BaseModel):
    title: str
    context: str
    decision: str
    rationale: str
    expected_outcome: Optional[str] = None
    project_id: Optional[str] = None
    linked_memory_id: Optional[str] = None


class DecisionOutcomeRequest(BaseModel):
    actual_outcome: str
    status: Optional[str] = "resolved"


class DecisionResponse(BaseModel):
    id: str
    user_id: str
    title: str
    context: str
    decision: str
    rationale: str
    expected_outcome: Optional[str] = None
    actual_outcome: Optional[str] = None
    status: str
    linked_memory_id: Optional[str] = None
    project_id: Optional[str] = None
    decided_at: Optional[datetime] = None
    resolved_at: Optional[datetime] = None
    review_count: int = 0
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class DecisionListResponse(BaseModel):
    decisions: List[DecisionResponse]
    total: int


class AdvisorAskRequest(BaseModel):
    question: str
    mode: Optional[str] = "explain"
    recall_level: Optional[str] = "work_context"
    project_id: Optional[str] = None
    decision_ids: Optional[List[str]] = None


class AdvisorAskResponse(BaseModel):
    user_id: str
    question: str
    mode: str
    advice: str
    supporting_decisions: List[dict]
    conflicts: List[dict]
    persona_used: bool
    confidence: float
    warnings: List[str]
    meta: dict


class WeeklyReviewGenerateRequest(BaseModel):
    week_start: Optional[str] = None
    dry_run: Optional[bool] = True


class WeeklyReviewResponse(BaseModel):
    id: str
    user_id: str
    week_start: str
    week_end: str
    new_memories: List[dict] = []
    decisions: List[dict] = []
    highlights: List[str] = []
    open_questions: List[str] = []
    summary: str
    word_count: int = 0
    new_memories_count: int = 0
    decisions_count: int = 0
    created_at: Optional[datetime] = None
    persisted: bool = True


class WeeklyReviewHistoryResponse(BaseModel):
    reviews: List[WeeklyReviewResponse]
    total: int


# ── v2.0 Schemas ──────────────────────────────────────────────────────────────


class AdvisorAskResponseV2(BaseModel):
    """v2.0 Advisor 回答结构。"""
    answer: str = ""
    direct_recommendation: Optional[str] = ""
    historical_basis: List[dict] = Field(default_factory=list)
    risk_points: List[dict] = Field(default_factory=list)
    conflicts_or_changes: List[dict] = Field(default_factory=list)
    suggested_next_steps: List[dict] = Field(default_factory=list)
    uncertainty: Optional[str] = ""
    cited_memories: List[dict] = Field(default_factory=list)
    cited_decisions: List[dict] = Field(default_factory=list)
    advisor_mode: str = "decision"
    confidence: float = 0.5
    meta: dict = Field(default_factory=dict)


class DecisionReviewRequest(BaseModel):
    """决策复盘请求。"""
    review_notes: str
    lessons_learned: Optional[str] = None
    outcome_rating: Optional[str] = None  # e.g. "good", "neutral", "bad"


class DecisionReviewResponse(BaseModel):
    """决策复盘响应。"""
    id: str
    decision_id: str
    user_id: str
    review_notes: str
    lessons_learned: Optional[str] = None
    outcome_rating: Optional[str] = None
    created_at: Optional[datetime] = None


class ConflictRecordResponse(BaseModel):
    """冲突记录响应。"""
    id: str
    user_id: str
    conflict_type: str
    interpretation: str
    severity: str
    status: str
    current_content: Optional[str] = None
    past_content: Optional[str] = None
    current_memory_id: Optional[str] = None
    past_memory_id: Optional[str] = None
    explanation: Optional[str] = None
    suggested_resolution: Optional[str] = None
    project_id: Optional[str] = None
    detected_at: Optional[datetime] = None
    acknowledged_at: Optional[datetime] = None
    resolved_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class ConflictRecordListResponse(BaseModel):
    """冲突记录列表响应。"""
    conflicts: List[ConflictRecordResponse]
    total: int


class ConflictStatusUpdateRequest(BaseModel):
    """更新冲突记录状态。"""
    status: str = Field(..., description="acknowledged / resolved / ignored")


class AdvisorSessionResponse(BaseModel):
    """Advisor 会话响应。"""
    id: str
    user_id: str
    question: str
    advisor_mode: str
    answer: str
    direct_recommendation: Optional[str] = None
    confidence: float = 0.5
    uncertainty: Optional[str] = None
    historical_basis: List[dict] = Field(default_factory=list)
    risk_points: List[dict] = Field(default_factory=list)
    conflicts_or_changes: List[dict] = Field(default_factory=list)
    suggested_next_steps: List[dict] = Field(default_factory=list)
    cited_memories: List[dict] = Field(default_factory=list)
    cited_decisions: List[dict] = Field(default_factory=list)
    meta: dict = Field(default_factory=dict)
    project_id: Optional[str] = None
    created_at: Optional[datetime] = None


class AdvisorSessionListResponse(BaseModel):
    """Advisor 会话列表响应。"""
    sessions: List[AdvisorSessionResponse]
    total: int