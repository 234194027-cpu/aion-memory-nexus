"""Gen 2 Governance Schemas — Conflict / Dedup / Rewrite 请求与响应模型.

风格与项目其他 schema (memories.py / agents.py) 保持一致:
- BaseModel from pydantic
- Optional / List 显式标注
- 字段命名 snake_case
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, List, Optional

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


class DuplicateMergeRequest(BaseModel):
    primary_memory_id: str
    secondary_memory_id: str
    merged_body: Optional[str] = None


class DuplicateMergeResponse(BaseModel):
    primary_memory_id: str
    secondary_memory_id: str
    secondary_status: str
    merged_body_preview: Optional[str] = None
    merged_at: Optional[str] = None


# ---------------------------------------------------------------------------
# Memory Rewriter
# ---------------------------------------------------------------------------


class RewriterRunRequest(BaseModel):
    target_types: Optional[List[str]] = Field(
        default=None,
        description="只重写指定 memory_type 的零碎 memory，留空 = 全部",
    )
    max_clusters: int = Field(default=20, ge=1, le=200)
    dry_run: bool = Field(
        default=True,
        description="True = 只生成 proposals，不写库；False = 立即 apply",
    )


class RewriteProposal(BaseModel):
    action: str = Field(..., description="merge | rewrite | archive | link")
    memory_ids: Optional[List[str]] = None
    memory_id: Optional[str] = None
    reason: str
    merged_draft: Optional[str] = None
    draft_body: Optional[str] = None
    relation_type: Optional[str] = None


class RewriterRunResponse(BaseModel):
    user_id: str
    rewritten_count: int
    merges_proposed: int
    proposals: List[RewriteProposal] = []
    applied: bool
    generated_at: str
    warnings: List[str] = []


class RewriterApplyRequest(BaseModel):
    proposals: List[RewriteProposal]


class RewriterApplyResponse(BaseModel):
    user_id: str
    applied_count: int
    failed: List[dict] = []
    applied_at: str


# ---------------------------------------------------------------------------
# Hygiene Review
# ---------------------------------------------------------------------------


class HygieneRunRequest(BaseModel):
    dedup_threshold: float = Field(default=0.9, ge=0.0, le=1.0)
    importance_floor: float = Field(default=0.4, ge=0.0, le=1.0)
    max_pairs_per_user: int = Field(default=20, ge=1, le=200)


class HygieneSuggestion(BaseModel):
    type: str = Field(..., min_length=1)
    priority: Optional[str] = None
    memory_ids: List[str] = []
    conflict_id: Optional[str] = None
    tag: Optional[str] = None
    reason: Optional[str] = None
    proposal: dict[str, Any] = {}
    auto_apply: bool = False


class HygieneRunResponse(BaseModel):
    user_id: str
    duplicate_pairs: List[dict] = []
    stale_conflicts: List[dict] = []
    memory_evolution: dict[str, Any] = {}
    hygiene_suggestions: List[HygieneSuggestion] = []
    ran_at: str
    stats: dict[str, Any] = {}
    warnings: List[str] = []


class HygieneApplyRequest(BaseModel):
    suggestions: List[HygieneSuggestion]
    approved: bool = Field(
        default=False,
        description="Must be true before any hygiene suggestion is applied.",
    )
    dry_run: bool = Field(
        default=False,
        description="True converts suggestions to proposals without modifying memory.",
    )


class HygieneApplyResponse(BaseModel):
    user_id: str
    approved: bool
    dry_run: bool
    proposals: List[dict] = []
    unsupported: List[dict] = []
    applied_count: int = 0
    failed: List[dict] = []
    applied_at: str


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
