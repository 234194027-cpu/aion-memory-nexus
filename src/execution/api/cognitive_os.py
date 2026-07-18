"""Gen 3 / Cognitive OS API.

- /api/os/context/route      -> 调用 ContextRouter
- /api/os/tasks              -> 任务 CRUD
- /api/os/tasks/...          -> 任务链接 / auto-extract / decompose / assign / complete
- /api/os/timeline           -> 重建 / 读取时间线

权限: get_current_user; 跨 user 操作一律 403。
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from src.shared.db.database import get_db
from src.shared.security.dependencies import get_current_user
from src.memory.models.committed_memory import CommittedMemory
from src.cognition.models.decision_record import DecisionRecord
from src.execution.models.user import User
from src.execution.schemas.os import (
    ContextRouteRequest,
    ContextRouteResponse,
    TaskAutoExtractRequest,
    TaskAutoExtractResponse,
    TaskCompleteRequest,
    TaskCompleteResponse,
    TaskCreateRequest,
    TaskDecomposeRequest,
    TaskDecomposeResponse,
    TaskListResponse,
    TaskResponse,
    TaskUpdateRequest,
    TimelineEntry,
    TimelineListResponse,
    TimelineRebuildRequest,
    TimelineRebuildResponse,
)
from src.execution.services.context_router import ContextRouter
from src.execution.services.life_timeline import LifeTimeline
from src.execution.services.task_system import TaskSystem, task_to_response

logger = logging.getLogger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# /api/os/context/route
# ---------------------------------------------------------------------------


@router.post("/context/route", response_model=ContextRouteResponse)
async def route_context(
    request: ContextRouteRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """根据用户消息返回 routing 决策 (v3)。"""
    router_svc = ContextRouter(db)
    result = await router_svc.route(
        user_id=user.id,
        message=request.message,
        recent_history=request.recent_history,
        persona=request.persona,
        memory_summary=request.memory_summary,
        task_context=request.task_context,
    )
    return ContextRouteResponse(**result)


# ---------------------------------------------------------------------------
# /api/os/tasks
# ---------------------------------------------------------------------------


@router.post("/tasks", response_model=TaskResponse)
async def create_task(
    request: TaskCreateRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    svc = TaskSystem(db)
    try:
        task = await svc.create_task(
            user_id=user.id,
            title=request.title,
            description=request.description,
            priority=request.priority,
            project_id=request.project_id,
            parent_task_id=request.parent_task_id,
            linked_memory_ids=request.linked_memory_ids,
            linked_decision_ids=request.linked_decision_ids,
            due_at=request.due_at,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    return TaskResponse(**task_to_response(task))


@router.get("/tasks", response_model=TaskListResponse)
async def list_tasks(
    status: Optional[str] = Query(None),
    project_id: Optional[str] = Query(None),
    priority: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    svc = TaskSystem(db)
    try:
        tasks = await svc.list_tasks(
            user.id, status=status, project_id=project_id,
            priority=priority, limit=limit,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return TaskListResponse(
        tasks=[TaskResponse(**task_to_response(t)) for t in tasks],
        total=len(tasks),
    )


@router.get("/tasks/{task_id}", response_model=TaskResponse)
async def get_task(
    task_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    svc = TaskSystem(db)
    try:
        task = await svc.get_task(user.id, task_id)
    except LookupError:
        raise HTTPException(status_code=404, detail="Task not found")
    except PermissionError:
        raise HTTPException(status_code=403, detail="Not authorized")
    return TaskResponse(**task_to_response(task))


@router.patch("/tasks/{task_id}", response_model=TaskResponse)
async def update_task(
    task_id: str,
    request: TaskUpdateRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    svc = TaskSystem(db)
    try:
        existing = await svc.get_task(user.id, task_id)
    except LookupError:
        raise HTTPException(status_code=404, detail="Task not found")
    except PermissionError:
        raise HTTPException(status_code=403, detail="Not authorized")

    try:
        if request.status is not None:
            task = await svc.update_status(user.id, task_id, request.status)
        else:
            task = existing
        if any(
            v is not None for v in (
                request.title, request.description, request.priority,
                request.project_id, request.due_at,
            )
        ):
            task = await svc.update_task(
                user.id, task_id,
                title=request.title,
                description=request.description,
                priority=request.priority,
                project_id=request.project_id,
                due_at=request.due_at,
            )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except PermissionError:
        raise HTTPException(status_code=403, detail="Not authorized")
    return TaskResponse(**task_to_response(task))


@router.post("/tasks/{task_id}/link-memory/{memory_id}", response_model=TaskResponse)
async def link_task_to_memory(
    task_id: str,
    memory_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    # owner 校验
    mem_res = await db.execute(
        select(CommittedMemory).where(CommittedMemory.id == memory_id)
    )
    mem = mem_res.scalar_one_or_none()
    if mem is None:
        raise HTTPException(status_code=404, detail="Memory not found")
    if mem.user_id != user.id:
        raise HTTPException(status_code=403, detail="Not authorized")

    svc = TaskSystem(db)
    try:
        task = await svc.link_to_memory(user.id, task_id, memory_id)
    except LookupError:
        raise HTTPException(status_code=404, detail="Task not found")
    except PermissionError as e:
        msg = str(e)
        if "do not belong" in msg:
            raise HTTPException(status_code=403, detail=msg)
        raise HTTPException(status_code=400, detail=msg)
    return TaskResponse(**task_to_response(task))


@router.post(
    "/tasks/{task_id}/link-decision/{decision_id}", response_model=TaskResponse
)
async def link_task_to_decision(
    task_id: str,
    decision_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    dec_res = await db.execute(
        select(DecisionRecord).where(DecisionRecord.id == decision_id)
    )
    dec = dec_res.scalar_one_or_none()
    if dec is None:
        raise HTTPException(status_code=404, detail="Decision not found")
    if dec.user_id != user.id:
        raise HTTPException(status_code=403, detail="Not authorized")

    svc = TaskSystem(db)
    try:
        task = await svc.link_to_decision(user.id, task_id, decision_id)
    except LookupError:
        raise HTTPException(status_code=404, detail="Task not found")
    except PermissionError as e:
        msg = str(e)
        if "do not belong" in msg:
            raise HTTPException(status_code=403, detail=msg)
        raise HTTPException(status_code=400, detail=msg)
    return TaskResponse(**task_to_response(task))


@router.post("/tasks/auto-extract", response_model=TaskAutoExtractResponse)
async def auto_extract_tasks(
    request: TaskAutoExtractRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    svc = TaskSystem(db)
    tasks = await svc.auto_extract_tasks_from_recent_memories(
        user.id, days=request.days, limit=request.limit,
    )
    return TaskAutoExtractResponse(
        user_id=user.id,
        days=request.days,
        scanned=-1,
        created=len(tasks),
        tasks=[TaskResponse(**task_to_response(t)) for t in tasks],
    )


# ---------------------------------------------------------------------------
# /api/os/tasks v3 endpoints: decompose / assign / complete
# ---------------------------------------------------------------------------


@router.post("/tasks/{task_id}/decompose", response_model=TaskDecomposeResponse)
async def decompose_task(
    task_id: str,
    request: TaskDecomposeRequest = TaskDecomposeRequest(),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """将任务拆解为子任务。"""
    svc = TaskSystem(db)
    try:
        result = await svc.decompose_task(
            user.id, task_id, max_sub_tasks=request.max_sub_tasks,
        )
    except LookupError:
        raise HTTPException(status_code=404, detail="Task not found")
    except PermissionError:
        raise HTTPException(status_code=403, detail="Not authorized")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return TaskDecomposeResponse(
        parent_task_id=result["parent_task_id"],
        sub_tasks=[TaskResponse(**task_to_response(t)) for t in result["sub_tasks"]],
        decomposition_rationale=result["decomposition_rationale"],
    )


@router.post("/tasks/{task_id}/assign/{agent_id}", response_model=TaskResponse)
async def assign_task_to_agent(
    task_id: str,
    agent_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """将任务分配给指定 agent。"""
    svc = TaskSystem(db)
    try:
        task = await svc.assign_agent(user.id, task_id, agent_id)
    except LookupError:
        raise HTTPException(status_code=404, detail="Task or agent not found")
    except PermissionError:
        raise HTTPException(status_code=403, detail="Not authorized")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return TaskResponse(**task_to_response(task))


@router.post("/tasks/{task_id}/complete", response_model=TaskCompleteResponse)
async def complete_task(
    task_id: str,
    request: TaskCompleteRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """完成任务并将结果回写为记忆。"""
    svc = TaskSystem(db)
    try:
        result = await svc.complete_task_with_memory(
            user.id, task_id, result_summary=request.result_summary,
        )
    except LookupError:
        raise HTTPException(status_code=404, detail="Task not found")
    except PermissionError:
        raise HTTPException(status_code=403, detail="Not authorized")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return TaskCompleteResponse(
        task=TaskResponse(**task_to_response(result["task"])),
        memory_id=result["memory_id"],
    )


# ---------------------------------------------------------------------------
# /api/os/timeline
# ---------------------------------------------------------------------------


@router.post("/timeline/rebuild", response_model=TimelineRebuildResponse)
async def rebuild_timeline(
    request: TimelineRebuildRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    svc = LifeTimeline(db)
    result = await svc.rebuild(
        user.id, since_date=request.since_date, until_date=request.until_date,
    )
    return TimelineRebuildResponse(
        user_id=result["user_id"],
        entry_count=result["entry_count"],
        by_date=result["by_date"],
        highlights=[TimelineEntry(**h) for h in result["highlights"]],
    )


@router.get("/timeline", response_model=TimelineListResponse)
async def get_timeline(
    since_date: Optional[str] = Query(None),
    until_date: Optional[str] = Query(None),
    kind: Optional[str] = Query(None),
    project_id: Optional[str] = Query(None),
    limit: int = Query(200, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    svc = LifeTimeline(db)
    try:
        entries = await svc.get_timeline(
            user.id, since_date=since_date, until_date=until_date,
            kind=kind, project_id=project_id, limit=limit,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return TimelineListResponse(
        entries=[TimelineEntry(**e) for e in entries],
        total=len(entries),
    )


# ---------------------------------------------------------------------------
# /api/os/timeline/advanced  (decision-chains / project-evolution /
#                              cognitive-shifts / behavior-trends)
# ---------------------------------------------------------------------------


@router.get("/timeline/decision-chains")
async def get_decision_chains(
    project_id: Optional[str] = Query(None),
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """返回决策链: 决策之间的因果关系。"""
    svc = LifeTimeline(db)
    try:
        chains = await svc.get_decision_chains(
            user.id, project_id=project_id, limit=limit,
        )
    except Exception as e:
        logger.exception(f"get_decision_chains failed: {e}")
        raise HTTPException(status_code=500, detail="internal_error")
    return {"user_id": user.id, "chains": chains, "total": len(chains)}


@router.get("/timeline/project-evolution")
async def get_project_evolution(
    project_id: Optional[str] = Query(None),
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """返回项目演化: 项目随时间的里程碑和阶段变化。"""
    svc = LifeTimeline(db)
    try:
        projects = await svc.get_project_evolution(
            user.id, project_id=project_id, limit=limit,
        )
    except Exception as e:
        logger.exception(f"get_project_evolution failed: {e}")
        raise HTTPException(status_code=500, detail="internal_error")
    return {"user_id": user.id, "projects": projects, "total": len(projects)}


@router.get("/timeline/cognitive-shifts")
async def get_cognitive_shifts(
    days: int = Query(90, ge=1, le=365),
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """返回认知变化: 用户观点/偏好的转变。"""
    svc = LifeTimeline(db)
    try:
        shifts = await svc.get_cognitive_shifts(
            user.id, days=days, limit=limit,
        )
    except Exception as e:
        logger.exception(f"get_cognitive_shifts failed: {e}")
        raise HTTPException(status_code=500, detail="internal_error")
    return {"user_id": user.id, "shifts": shifts, "total": len(shifts)}


@router.get("/timeline/behavior-trends")
async def get_behavior_trends(
    days: int = Query(90, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """返回行为趋势: 统计指标。"""
    svc = LifeTimeline(db)
    try:
        trends = await svc.get_behavior_trends(user.id, days=days)
    except Exception as e:
        logger.exception(f"get_behavior_trends failed: {e}")
        raise HTTPException(status_code=500, detail="internal_error")
    return trends
