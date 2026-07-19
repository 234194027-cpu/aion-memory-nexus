"""Gen 2 Governance Schemas — Conflict / bounded dedup / relations.

风格与项目其他 schema (memories.py / agents.py) 保持一致:
- BaseModel from pydantic
- Optional / List 显式标注
- 字段命名 snake_case
"""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Conflict Check
# ---------------------------------------------------------------------------


class ConflictCheckRequest(BaseModel):
    """POST /api/memory/conflicts/check 请求体。

    提案必须包含 body, 可选包含 memory_type 与 title。
    """

    body: str = Field(..., description="拟治理的记忆正文")
    title: Optional[str] = Field(default=None, description="拟治理的记忆标题")
    memory_type: Optional[str] = Field(
        default=None,
        description="拟治理的记忆类型 (decision / preference / fact / insight / ...)",
    )
    recall_level: Optional[str] = Field(
        default="work_context",
        description="召回级别，控制检索范围",
    )
    top_k: Optional[int] = Field(
        default=8,
        description="参与冲突比对的最相关 memory 数量",
    )


class ConflictItem(BaseModel):
    memory_id: str
    title: str
    memory_type: str
    severity: str = Field(..., description="high | medium | low")
    explanation: str
    suggested_resolution: str = Field(
        ...,
        description="supersede_old | merge | keep_both | needs_user_review",
    )


class SimilarMemoryItem(BaseModel):
    memory_id: str
    title: str
    similarity: float = Field(..., ge=0.0, le=1.0)


class ConflictCheckResponse(BaseModel):
    user_id: str
    has_conflict: bool
    conflicts: List[ConflictItem] = []
    similar_memories: List[SimilarMemoryItem] = []
    warnings: List[str] = []
    checked_at: Optional[str] = None


# ---------------------------------------------------------------------------
# Deduplicate
# ---------------------------------------------------------------------------


class DuplicateFindRequest(BaseModel):
    memory_id: Optional[str] = None
    similarity_threshold: float = Field(default=0.85, ge=0.0, le=1.0)
    top_k: int = Field(default=20, ge=1, le=200)


class DuplicatePair(BaseModel):
    memory_id_a: str
    memory_id_b: str
    similarity: float
    suggested_action: str = Field(..., description="merge | supersede | keep_both")


class DuplicateFindResponse(BaseModel):
    user_id: str
    pairs: List[DuplicatePair] = []
    scanned: int = 0
    warnings: List[str] = []


# ---------------------------------------------------------------------------
# Memory Relations
# ---------------------------------------------------------------------------


class MemoryRelationCreateRequest(BaseModel):
    source_memory_id: str
    target_memory_id: str
    relation_type: str
    reason: Optional[str] = None
    confidence: Optional[float] = 0.5
    valid_from: Optional[datetime] = None
    valid_until: Optional[datetime] = None


class MemoryRelationResponse(BaseModel):
    id: str
    source_memory_id: str
    target_memory_id: str
    relation_type: str
    reason: Optional[str]
    confidence: float
    valid_from: Optional[str] = None
    valid_until: Optional[str] = None
    created_at: str


# ---------------------------------------------------------------------------
# Conflict Records
# ---------------------------------------------------------------------------


class ConflictRecordResponse(BaseModel):
    id: str
    user_id: str
    conflict_type: str
    current_statement: str
    past_statement: Optional[str]
    severity: str
    interpretation: str
    recommended_action: str
    confidence: float
    status: str
    created_at: str


# ---------------------------------------------------------------------------
# Persona Feedback
# ---------------------------------------------------------------------------


class PersonaFeedbackRequest(BaseModel):
    snapshot_id: str
    feedback_type: str  # accurate/inaccurate/needs_update/delete
    comment: Optional[str] = None


# ---------------------------------------------------------------------------
# Decision Tracker (P1)
# ---------------------------------------------------------------------------


class DecisionTrackRequest(BaseModel):
    title: str = Field(..., min_length=1)
    context: str = ""
    decision: str = Field(..., min_length=1)
    rationale: str = ""
    expected_outcome: Optional[str] = None
    project_id: Optional[str] = None
    linked_memory_id: Optional[str] = None


class DecisionUpdateRequest(BaseModel):
    actual_outcome: str
    status: str = Field(default="resolved", description="open | resolved | abandoned")


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
    decided_at: Optional[str] = None
    resolved_at: Optional[str] = None
    review_count: int = 0
    confidence: Optional[float] = 0.5
    importance: Optional[float] = 0.5
    decision_type: Optional[str] = "other"


class DecisionAutoTrackRequest(BaseModel):
    memory_id: str = Field(..., description="触发自动跟踪的 committed memory id")


# ---------------------------------------------------------------------------
# Weekly Review (P1)
# ---------------------------------------------------------------------------


class WeeklyReviewRequest(BaseModel):
    week_start: Optional[str] = Field(
        default=None,
        description="YYYY-MM-DD，留空 = 上一个周一",
    )
    dry_run: bool = Field(default=True, description="True = 不写库，只返回结构")


class WeeklyReviewResponse(BaseModel):
    id: Optional[str] = None
    user_id: str
    week_start: str
    week_end: str
    summary: str
    key_decisions: List[dict] = []
    important_insights: List[str] = []
    repeated_themes: List[str] = []
    conflicts_or_changes: List[dict] = []
    risks_to_watch: List[str] = []
    suggested_focus_next_week: List[str] = []
    persona_observations: List[str] = []
    open_loops: List[str] = []
    cited_memories: List[str] = []
    cited_decisions: List[str] = []
    new_memories_count: int = 0
    decisions_count: int = 0
    word_count: int = 0
    warnings: List[str] = []
    persisted: bool = False
    generated_at: Optional[str] = None


# ---------------------------------------------------------------------------
# Conflict Graph (P1 visualization)
# ---------------------------------------------------------------------------


class ConflictGraphNode(BaseModel):
    memory_id: str
    title: str
    memory_type: Optional[str] = None
    degree: int = 0  # 连接数
    is_user_focus: bool = False


class ConflictGraphEdgeItem(BaseModel):
    id: str
    source: str
    target: str
    conflict_type: str
    severity: str
    resolution_status: str
    confidence: float
    explanation: Optional[str] = None
    statement_a: Optional[str] = None
    statement_b: Optional[str] = None


class ConflictGraphResponse(BaseModel):
    user_id: str
    focus_memory_id: Optional[str] = None
    nodes: List[ConflictGraphNode] = []
    edges: List[ConflictGraphEdgeItem] = []
    clusters: List[List[str]] = []  # 连通分量
    stats: dict = {}
    warnings: List[str] = []
