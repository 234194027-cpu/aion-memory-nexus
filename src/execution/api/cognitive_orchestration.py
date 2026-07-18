"""Cognitive Orchestration API (Gen 3).

端点:
- POST   /api/orchestration/multi-agent/run       → MultiAgentOrchestrator.run
- GET    /api/orchestration/simulations           → list SimulationRun
- POST   /api/orchestration/simulate              → create & run SimulationEngine
- GET    /api/orchestration/simulations/{id}      → detail
- POST   /api/orchestration/permissions           → grant
- DELETE /api/orchestration/permissions           → revoke
- GET    /api/orchestration/permissions           → list
- POST   /api/orchestration/permissions/check     → check

权限: get_current_user, 只能操作自己; agent 必须属于 user。
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response, WebSocket, WebSocketDisconnect
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.shared.config import settings
from src.shared.db.database import get_db, async_session
from src.shared.security.dependencies import get_current_user
from src.execution.models.agent_permission import AgentPermission
from src.execution.models.agent_profile import AgentProfile
from src.execution.models.simulation_run import SimulationRun
from src.execution.models.user import User
from src.execution.schemas.orchestration import (
    MultiAgentDraft,
    MultiAgentRunRequest,
    MultiAgentRunResponse,
    PermissionCheckRequest,
    PermissionCheckResponse,
    PermissionGrantRequest,
    PermissionItem,
    PermissionListResponse,
    SimulateRequest,
    SimulateResponse,
    SimulationDetailResponse,
    SimulationListItem,
    SimulationListResponse,
    ToolExecuteRequest,
    ToolExecuteResponse,
    ToolListResponse,
)
from src.execution.services.multi_agent_orchestrator import MultiAgentOrchestrator
from src.execution.services.simulation_engine import SimulationEngine
from src.execution.services.tool_permission import AVAILABLE_TOOLS, ToolPermissionService
from src.execution.services.ws_manager import ws_manager


router = APIRouter()


# ---------------------------------------------------------------------------
# Multi-Agent Orchestration
# ---------------------------------------------------------------------------


@router.post("/multi-agent/run", response_model=MultiAgentRunResponse)
async def run_multi_agent(
    request: MultiAgentRunRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """让多个 agent 协同回答一个问题。"""
    if request.agent_ids:
        result_q = await db.execute(
            select(AgentProfile).where(
                AgentProfile.id.in_(request.agent_ids),
                AgentProfile.user_id == user.id,
            )
        )
        valid_agents = {a.id for a in result_q.scalars().all()}
        invalid = [aid for aid in request.agent_ids if aid not in valid_agents]
        if invalid:
            raise HTTPException(
                status_code=400,
                detail=f"agent_ids 不属于当前用户或不存在: {invalid}",
            )

    orchestrator = MultiAgentOrchestrator(db)
    result = await orchestrator.run(
        user_id=user.id,
        question=request.question,
        agent_ids=request.agent_ids,
        max_agents=request.max_agents,
        recall_level=request.recall_level,
        execution_mode=request.execution_mode,
        roles_to_activate=request.roles_to_activate,
        writeback_to_memory=request.writeback_to_memory,
    )
    return MultiAgentRunResponse(
        user_id=result["user_id"],
        question=result["question"],
        drafts=[MultiAgentDraft(**d) for d in result.get("drafts", [])],
        final_advice=result["final_advice"],
        confidence=result["confidence"],
        warnings=result["warnings"],
        meta=result["meta"],
        execution_mode=result.get("execution_mode"),
        role_outputs=result.get("role_outputs"),
        writeback_results=result.get("writeback_results"),
    )


# ---------------------------------------------------------------------------
# Simulation Engine
# ---------------------------------------------------------------------------


def _sim_to_item(s: SimulationRun) -> SimulationListItem:
    return SimulationListItem(
        id=s.id,
        user_id=s.user_id,
        question=s.question,
        counterfactual=s.counterfactual,
        outcome=s.outcome,
        confidence=s.confidence or 0.0,
        horizon_days=s.horizon_days,
        created_at=s.created_at,
    )


@router.post("/simulate", response_model=SimulateResponse)
async def run_simulation(
    request: SimulateRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """执行一次反事实推演, 默认 dry_run=False 即写入 simulation_runs。"""
    engine = SimulationEngine(db)
    result = await engine.simulate(
        user_id=user.id,
        question=request.question,
        horizon_days=request.horizon_days,
    )
    return SimulateResponse(
        user_id=result["user_id"],
        question=result["question"],
        baseline=result["baseline"],
        counterfactual=result["counterfactual"],
        predicted_outcome=result["predicted_outcome"],
        supporting_memories=result["supporting_memories"],
        supporting_decisions=result["supporting_decisions"],
        confidence=result["confidence"],
        warnings=result["warnings"],
        horizon_days=result["horizon_days"],
        run_id=result.get("run_id"),
        created_at=result["created_at"],
    )


@router.get("/simulations", response_model=SimulationListResponse)
async def list_simulations(
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """列出当前用户的 simulation_runs (按 created_at 倒序)。"""
    engine = SimulationEngine(db)
    runs = await engine.list_runs(user.id, limit=limit)
    return SimulationListResponse(
        simulations=[_sim_to_item(s) for s in runs],
        total=len(runs),
    )


@router.get("/simulations/{run_id}", response_model=SimulationDetailResponse)
async def get_simulation(
    run_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    engine = SimulationEngine(db)
    run = await engine.get_run(user.id, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Simulation not found")
    return SimulationDetailResponse(
        id=run.id,
        user_id=run.user_id,
        question=run.question,
        counterfactual=run.counterfactual,
        outcome=run.outcome,
        confidence=run.confidence or 0.0,
        horizon_days=run.horizon_days,
        created_at=run.created_at,
        baseline_summary=run.baseline_summary,
        linked_memory_ids=run.linked_memory_ids,
    )


# ---------------------------------------------------------------------------
# Tool Permission
# ---------------------------------------------------------------------------


def _perm_to_item(p: AgentPermission) -> PermissionItem:
    return PermissionItem(
        id=p.id,
        user_id=p.user_id,
        agent_id=p.agent_id,
        tool_name=p.tool_name,
        scope=p.scope,
        created_at=p.created_at,
    )


async def _ensure_agent_belongs_to_user(
    db: AsyncSession, user_id: str, agent_id: str
) -> None:
    result = await db.execute(
        select(AgentProfile).where(
            AgentProfile.id == agent_id, AgentProfile.user_id == user_id
        )
    )
    if result.scalar_one_or_none() is None:
        raise HTTPException(
            status_code=400,
            detail=f"agent '{agent_id}' 不属于当前用户或不存在",
        )


@router.post("/permissions", response_model=PermissionItem)
async def grant_permission(
    request: PermissionGrantRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Grant / upsert 一个 agent 的某 tool 权限。scope=allow|deny。"""
    if request.tool_name not in AVAILABLE_TOOLS:
        raise HTTPException(
            status_code=400,
            detail=f"unknown tool_name: {request.tool_name}",
        )
    if request.scope not in ("allow", "deny"):
        raise HTTPException(
            status_code=400,
            detail=f"invalid scope: {request.scope}; must be 'allow' or 'deny'",
        )

    await _ensure_agent_belongs_to_user(db, user.id, request.agent_id)

    service = ToolPermissionService(db)
    perm = await service.grant(
        user_id=user.id,
        agent_id=request.agent_id,
        tool_name=request.tool_name,
        scope=request.scope,
    )
    return _perm_to_item(perm)


@router.delete("/permissions", status_code=204)
async def revoke_permission(
    agent_id: str = Query(...),
    tool_name: str = Query(...),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Revoke 一个 agent 的某 tool 权限。不存在不报错 (返回 204)。"""
    await _ensure_agent_belongs_to_user(db, user.id, agent_id)

    service = ToolPermissionService(db)
    await service.revoke(user_id=user.id, agent_id=agent_id, tool_name=tool_name)
    return Response(status_code=204)


@router.get("/permissions", response_model=PermissionListResponse)
async def list_permissions(
    agent_id: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """列出当前用户的权限记录。可选 agent_id 过滤。"""
    if agent_id:
        await _ensure_agent_belongs_to_user(db, user.id, agent_id)

    service = ToolPermissionService(db)
    perms = await service.list_permissions(user.id, agent_id=agent_id)
    return PermissionListResponse(
        permissions=[_perm_to_item(p) for p in perms],
        total=len(perms),
    )


@router.post("/permissions/check", response_model=PermissionCheckResponse)
async def check_permission(
    request: PermissionCheckRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """检查某 agent 是否被允许使用某 tool。默认 deny。"""
    await _ensure_agent_belongs_to_user(db, user.id, request.agent_id)

    service = ToolPermissionService(db)
    result = await service.check(
        user_id=user.id,
        agent_id=request.agent_id,
        tool_name=request.tool_name,
    )
    return PermissionCheckResponse(**result)


# ---------------------------------------------------------------------------
# Tool Layer
# ---------------------------------------------------------------------------


@router.get("/tools", response_model=ToolListResponse)
async def list_tools(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """列出可用工具。"""
    from src.execution.services.tool_executor import ToolExecutor
    executor = ToolExecutor(db)
    return ToolListResponse(tools=executor.list_tools())


@router.post("/tools/execute", response_model=ToolExecuteResponse)
async def execute_tool(
    request: ToolExecuteRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """执行指定工具。"""
    from src.execution.services.tool_executor import ToolExecutor
    await _ensure_agent_belongs_to_user(db, user.id, request.agent_id)

    permission_service = ToolPermissionService(db)
    permission = await permission_service.check(
        user_id=user.id,
        agent_id=request.agent_id,
        tool_name=request.tool_name,
    )
    if not permission["allowed"]:
        raise HTTPException(
            status_code=403,
            detail={
                "error": "tool_permission_denied",
                "agent_id": request.agent_id,
                "tool_name": request.tool_name,
                "source": permission["source"],
            },
        )

    executor = ToolExecutor(db)
    result = await executor.execute(
        user_id=user.id,
        tool_name=request.tool_name,
        params=request.params,
    )
    return ToolExecuteResponse(
        status=result.get("status", "error"),
        result=result.get("result"),
        error=result.get("error"),
    )


# ---------------------------------------------------------------------------
# WebSocket
# ---------------------------------------------------------------------------


@router.websocket("/ws/multi-agent/{user_id}")
async def websocket_multi_agent(websocket: WebSocket, user_id: str):
    """WebSocket 实时多 Agent 协同（逐步输出各 agent draft）。需要 JWT 鉴权。"""
    # Authenticate via query parameter or subprotocol
    token = websocket.query_params.get("token")
    if not token:
        sec_ws_protocol = websocket.headers.get("sec-websocket-protocol", "")
        if sec_ws_protocol:
            token = sec_ws_protocol.split(",")[0].strip()

    if not token and not settings.SOLO_MODE:
        await websocket.close(code=4001, reason="Authentication required")
        return

    if not settings.SOLO_MODE:
        from src.shared.security.auth import decode_access_token
        payload = decode_access_token(token)
        if payload is None or payload.get("user_id") != user_id:
            await websocket.close(code=4001, reason="Invalid token")
            return

    await ws_manager.connect(user_id, websocket)
    try:
        while True:
            data = await websocket.receive_json()
            question = data.get("question", "")
            max_agents = data.get("max_agents", 3)
            recall_level = data.get("recall_level", "work_context")

            if not question:
                await ws_manager.send_error(user_id, "question is required")
                continue

            try:
                async with async_session() as db:
                    orchestrator = MultiAgentOrchestrator(db)

                    await ws_manager.send_json(user_id, {
                        "event": "status",
                        "message": "正在选择 agents...",
                    })

                    result = await orchestrator.run(
                        user_id=user_id,
                        question=question,
                        max_agents=max_agents,
                        recall_level=recall_level,
                    )

                    for i, draft in enumerate(result.get("drafts", [])):
                        await ws_manager.send_json(user_id, {
                            "event": "draft",
                            "index": i,
                            "data": draft,
                        })

                    await ws_manager.send_json(user_id, {
                        "event": "done",
                        "data": result,
                    })

            except Exception as e:
                await ws_manager.send_error(user_id, str(e))

    except (WebSocketDisconnect, Exception):
        ws_manager.disconnect(user_id)
