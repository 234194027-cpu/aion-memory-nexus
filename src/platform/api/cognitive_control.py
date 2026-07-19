"""Cognitive Control API — 认知控制平面接口 (Gen 3 Cognitive OS).

暴露 ControlPlane、BeliefEngine、ConflictGraphEngine 的核心能力。

权限: get_current_user; 请求体中的 user_id 必须与认证用户一致，否则 403。
"""
import logging
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from src.shared.db.database import get_db
from src.shared.security.dependencies import get_current_user
from src.execution.models.user import User
from src.execution.services.control_plane import ControlPlane
from src.cognition.services.belief_engine import BeliefEngine
from src.cognition.services.conflict_graph_engine import ConflictGraphEngine

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/cognitive/control", tags=["Cognitive Control"])


def _assert_user_id_matches(authenticated_user: User, requested_user_id: str) -> None:
    """确保请求体中的 user_id 与认证用户一致，防止 IDOR。"""
    if not requested_user_id or requested_user_id != authenticated_user.id:
        raise HTTPException(status_code=403, detail="user_id does not match authenticated user")


# ── Request/Response Models ─────────────────────────────────────────────


class CheckWritePermissionRequest(BaseModel):
    user_id: str = Field(..., description="用户 ID")
    agent_id: str = Field(..., description="Agent ID")
    write_type: str = Field(..., description="写入类型: raw_event / committed_memory / decision / task")


class CheckWritePermissionResponse(BaseModel):
    allowed: bool = Field(..., description="是否允许写入")
    write_type: str
    agent_id: str
    policy: Dict = Field(default_factory=dict, description="写入策略详情")


class RouteWithControlRequest(BaseModel):
    user_id: str
    message: str
    agent_id: Optional[str] = None
    recent_history: Optional[List[Dict]] = None
    persona: Optional[Dict] = None
    memory_summary: Optional[List[str]] = None
    task_context: Optional[List[Dict]] = None


class RouteWithControlResponse(BaseModel):
    intent: str
    recall_level: str
    selected_agents: List[str]
    tool_permissions: List[Dict]
    control_plane: Dict = Field(default_factory=dict, description="控制平面元数据")
    warnings: List[str] = Field(default_factory=list)


class BuildContextRequest(BaseModel):
    user_id: str
    question: str
    project_id: Optional[str] = None
    recall_level: str = "work_context"
    include_persona: bool = True
    include_conflicts: bool = True
    include_decisions: bool = True


class BuildContextResponse(BaseModel):
    user_id: str
    question: str
    retrieval_context: Dict
    persona: Optional[Dict]
    conflicts: List[Dict]
    decisions: List[Dict]
    meta: Dict


class ExtractBeliefsRequest(BaseModel):
    user_id: str
    memory_ids: Optional[List[str]] = None


class ExtractBeliefsResponse(BaseModel):
    beliefs_created: List[str]
    beliefs_updated: List[str]
    beliefs_challenged: List[str]
    warnings: List[str]


class GetBeliefsResponse(BaseModel):
    beliefs: List[Dict]


class ChallengeBeliefRequest(BaseModel):
    belief_id: str
    challenge_reason: str
    new_evidence_memory_id: Optional[str] = None


class ChallengeBeliefResponse(BaseModel):
    belief_id: str
    old_confidence: float
    new_confidence: float
    status: str
    challenged_at: str


class DetectConflictsRequest(BaseModel):
    user_id: str
    memory_id: str


class DetectConflictsResponse(BaseModel):
    memory_id: str
    conflicts_detected: List[Dict]
    transitive_conflicts: List[Dict]
    warnings: List[str]


class GetConflictsResponse(BaseModel):
    conflicts: List[Dict]


class ResolveConflictRequest(BaseModel):
    edge_id: str
    resolution_status: str
    resolution_note: Optional[str] = None


class ResolveConflictResponse(BaseModel):
    edge_id: str
    old_status: str
    new_status: str
    resolved_at: str


class GetConflictClustersResponse(BaseModel):
    clusters: List[Dict]


class GetConflictGraphResponse(BaseModel):
    """前端图谱可视化所需的 nodes / edges 数据。"""
    user_id: str
    focus_memory_id: Optional[str] = None
    nodes: List[Dict] = Field(default_factory=list)
    edges: List[Dict] = Field(default_factory=list)
    clusters: List[List[str]] = Field(default_factory=list)
    stats: Dict = Field(default_factory=dict)
    warnings: List[str] = Field(default_factory=list)


# ── ControlPlane Endpoints ──────────────────────────────────────────────


@router.post("/permission/check", response_model=CheckWritePermissionResponse)
async def check_write_permission(
    request: CheckWritePermissionRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """检查 Agent 是否有权限执行指定类型的写入操作。"""
    _assert_user_id_matches(user, request.user_id)
    try:
        plane = ControlPlane(db)
        allowed = await plane.check_write_permission(
            user_id=request.user_id,
            agent_id=request.agent_id,
            write_type=request.write_type,
        )
        policy = await plane.get_memory_write_policy(request.user_id)

        return CheckWritePermissionResponse(
            allowed=allowed,
            write_type=request.write_type,
            agent_id=request.agent_id,
            policy=policy,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"check_write_permission failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="internal_error")


@router.post("/route", response_model=RouteWithControlResponse)
async def route_with_control(
    request: RouteWithControlRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """带控制检查的路由请求 — ContextRouter 的增强版。"""
    _assert_user_id_matches(user, request.user_id)
    try:
        plane = ControlPlane(db)
        result = await plane.route_with_control(
            user_id=request.user_id,
            message=request.message,
            agent_id=request.agent_id,
            recent_history=request.recent_history,
            persona=request.persona,
            memory_summary=request.memory_summary,
            task_context=request.task_context,
        )

        return RouteWithControlResponse(
            intent=result.get("intent", "unknown"),
            recall_level=result.get("recall_level", "work_context"),
            selected_agents=result.get("selected_agents", []),
            tool_permissions=result.get("tool_permissions", []),
            control_plane=result.get("control_plane", {}),
            warnings=result.get("warnings", []),
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"route_with_control failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="internal_error")


@router.post("/context/build", response_model=BuildContextResponse)
async def build_context_for_advisor(
    request: BuildContextRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """为 AdvisorEngine 组装完整上下文。"""
    _assert_user_id_matches(user, request.user_id)
    try:
        plane = ControlPlane(db)
        context = await plane.build_context_for_advisor(
            user_id=request.user_id,
            question=request.question,
            project_id=request.project_id,
            recall_level=request.recall_level,
            include_persona=request.include_persona,
            include_conflicts=request.include_conflicts,
            include_decisions=request.include_decisions,
        )

        return BuildContextResponse(**context)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"build_context_for_advisor failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="internal_error")


# ── BeliefEngine Endpoints ──────────────────────────────────────────────


@router.post("/beliefs/extract", response_model=ExtractBeliefsResponse)
async def extract_beliefs(
    request: ExtractBeliefsRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """从记忆中提取信念。"""
    _assert_user_id_matches(user, request.user_id)
    try:
        engine = BeliefEngine(db)
        result = await engine.extract_beliefs_from_memories(
            user_id=request.user_id,
            memory_ids=request.memory_ids,
        )

        return ExtractBeliefsResponse(**result)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"extract_beliefs failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="internal_error")


@router.get("/beliefs", response_model=GetBeliefsResponse)
async def get_beliefs(
    user_id: str = Query(..., description="用户 ID"),
    categories: Optional[str] = Query(None, description="信念分类 (逗号分隔)"),
    min_confidence: float = Query(0.0, description="最小置信度"),
    limit: int = Query(50, description="返回数量限制"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """查询用户信念。"""
    _assert_user_id_matches(user, user_id)
    try:
        engine = BeliefEngine(db)
        category_list = categories.split(",") if categories else None
        beliefs = await engine.get_beliefs_for_user(
            user_id=user_id,
            categories=category_list,
            min_confidence=min_confidence,
            limit=limit,
        )

        return GetBeliefsResponse(beliefs=beliefs)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"get_beliefs failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="internal_error")


@router.post("/beliefs/challenge", response_model=ChallengeBeliefResponse)
async def challenge_belief(
    request: ChallengeBeliefRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """挑战一个信念 (当新证据与信念冲突时)。"""
    try:
        engine = BeliefEngine(db)
        result = await engine.challenge_belief(
            belief_id=request.belief_id,
            challenge_reason=request.challenge_reason,
            new_evidence_memory_id=request.new_evidence_memory_id,
        )

        return ChallengeBeliefResponse(**result)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"challenge_belief failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="internal_error")


# ── ConflictGraphEngine Endpoints ──────────────────────────────────────


@router.post("/conflicts/detect", response_model=DetectConflictsResponse)
async def detect_conflicts(
    request: DetectConflictsRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """检测一条记忆与其他记忆的冲突。"""
    _assert_user_id_matches(user, request.user_id)
    try:
        engine = ConflictGraphEngine(db)
        result = await engine.detect_conflicts_for_memory(
            user_id=request.user_id,
            memory_id=request.memory_id,
        )

        return DetectConflictsResponse(**result)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"detect_conflicts failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="internal_error")


@router.get("/conflicts", response_model=GetConflictsResponse)
async def get_conflicts(
    user_id: str = Query(..., description="用户 ID"),
    resolution_status: Optional[str] = Query(None, description="解析状态 (逗号分隔)"),
    limit: int = Query(50, description="返回数量限制"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """查询用户的冲突关系。"""
    _assert_user_id_matches(user, user_id)
    try:
        engine = ConflictGraphEngine(db)
        status_list = resolution_status.split(",") if resolution_status else None
        conflicts = await engine.get_conflicts_for_user(
            user_id=user_id,
            resolution_status=status_list,
            limit=limit,
        )

        return GetConflictsResponse(conflicts=conflicts)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"get_conflicts failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="internal_error")


@router.post("/conflicts/resolve", response_model=ResolveConflictResponse)
async def resolve_conflict(
    request: ResolveConflictRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """解析一个冲突边。"""
    try:
        engine = ConflictGraphEngine(db)
        result = await engine.resolve_conflict(
            edge_id=request.edge_id,
            resolution_status=request.resolution_status,
            resolution_note=request.resolution_note,
        )

        return ResolveConflictResponse(**result)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"resolve_conflict failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="internal_error")


@router.get("/conflicts/clusters", response_model=GetConflictClustersResponse)
async def get_conflict_clusters(
    user_id: str = Query(..., description="用户 ID"),
    min_cluster_size: int = Query(3, description="最小聚类大小"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """查找冲突聚类 (多个记忆相互冲突的群组)。"""
    _assert_user_id_matches(user, user_id)
    try:
        engine = ConflictGraphEngine(db)
        clusters = await engine.get_conflict_clusters(
            user_id=user_id,
            min_cluster_size=min_cluster_size,
        )

        return GetConflictClustersResponse(clusters=clusters)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"get_conflict_clusters failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="internal_error")
