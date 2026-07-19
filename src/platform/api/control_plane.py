"""Control Plane API - 控制平面 API 端点

提供 Control Plane 的 REST API 接口

端点：
- GET /api/control-plane/stats - 获取系统统计信息
- POST /api/control-plane/decide/ingestion - 决策事件输入
- POST /api/control-plane/decide/admission - 决策记忆准入
- POST /api/control-plane/decide/write - 决策记忆写入
- POST /api/control-plane/decide/permission - 决策 Agent 权限
- POST /api/control-plane/decide/evolution - 决策系统进化
"""

import logging
from typing import Dict, Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from src.shared.db.database import get_db
from src.shared.security.dependencies import get_current_user
from src.platform.control_plane.router import ControlPlaneRouter
from src.platform.control_plane.memory_gate import MemoryGate

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/stats")
async def get_control_plane_stats(
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """获取 Control Plane 统计信息
    
    返回：
    - 记忆总数
    - 各状态记忆数量
    - 各类型记忆数量
    - 各敏感度记忆数量
    """
    try:
        gate = MemoryGate(db)
        stats = await gate.get_memory_gate_stats(user.id)
        
        return {
            "user_id": user.id,
            "memory_stats": stats,
        }
    except Exception as e:
        logger.exception(f"get_control_plane_stats failed: {e}")
        raise HTTPException(status_code=500, detail="get_stats_failed")


@router.post("/decide/ingestion")
async def decide_ingestion(
    event_data: Dict[str, Any],
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """决策事件是否允许进入 Event Layer
    
    请求体：
    {
        "content": "事件内容",
        "source": "chat|obsidian|agent",
        "agent_type": "codex|openclaw|chatgpt",
        "metadata": {}
    }
    
    返回：
    {
        "allowed": true/false,
        "action": "allow|reject|sanitize",
        "reason": "决策原因",
        "metadata": {},
        "decision_id": "决策ID"
    }
    """
    try:
        router_cp = ControlPlaneRouter(db)
        decision = await router_cp.decide_ingestion(event_data, user.id)
        
        return {
            "allowed": decision.action.value == "allow",
            "action": decision.action.value,
            "reason": decision.reason,
            "metadata": decision.metadata,
            "decision_id": decision.decision_id,
        }
    except Exception as e:
        logger.exception(f"decide_ingestion failed: {e}")
        raise HTTPException(status_code=500, detail="decide_ingestion_failed")


@router.post("/decide/admission")
async def decide_memory_admission(
    event_data: Dict[str, Any],
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """决策事件是否允许进入 Memory Agent
    
    请求体：
    {
        "content": "事件内容",
        "metadata": {}
    }
    
    返回：
    {
        "allowed": true/false,
        "action": "allow|reject|queue",
        "reason": "决策原因",
        "metadata": {},
        "decision_id": "决策ID"
    }
    """
    try:
        router_cp = ControlPlaneRouter(db)
        decision = await router_cp.decide_memory_admission(event_data, user.id)
        
        return {
            "allowed": decision.action.value == "allow",
            "action": decision.action.value,
            "reason": decision.reason,
            "metadata": decision.metadata,
            "decision_id": decision.decision_id,
        }
    except Exception as e:
        logger.exception(f"decide_memory_admission failed: {e}")
        raise HTTPException(status_code=500, detail="decide_admission_failed")


@router.post("/decide/write")
async def decide_memory_write(
    proposal_data: Dict[str, Any],
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """校验工作 Agent 正式记忆提案是否满足服务端治理规则
    
    请求体：
    {
        "importance": 0.8,
        "confidence": 0.9,
        "memory_type": "decision",
        "sensitivity": "normal",
        "title": "记忆标题",
        "body": "记忆内容"
    }
    
    返回：
    {
        "allowed": true/false,
        "action": "allow|reject|modify",
        "reason": "决策原因",
        "metadata": {},
        "decision_id": "决策ID"
    }
    """
    try:
        router_cp = ControlPlaneRouter(db)
        decision = await router_cp.decide_memory_write(proposal_data, user.id)
        
        return {
            "allowed": decision.action.value == "allow",
            "action": decision.action.value,
            "reason": decision.reason,
            "metadata": decision.metadata,
            "decision_id": decision.decision_id,
        }
    except Exception as e:
        logger.exception(f"decide_memory_write failed: {e}")
        raise HTTPException(status_code=500, detail="decide_write_failed")


@router.post("/decide/permission")
async def decide_agent_permission(
    permission_request: Dict[str, Any],
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """决策 Agent 是否允许执行工具
    
    请求体：
    {
        "agent_id": "codex",
        "tool_name": "read_memory"
    }
    
    返回：
    {
        "allowed": true/false,
        "action": "allow|reject",
        "reason": "决策原因",
        "metadata": {},
        "decision_id": "决策ID"
    }
    """
    try:
        agent_id = permission_request.get("agent_id")
        tool_name = permission_request.get("tool_name")
        
        if not agent_id or not tool_name:
            raise HTTPException(
                status_code=400,
                detail="agent_id and tool_name are required",
            )
        
        router_cp = ControlPlaneRouter(db)
        decision = await router_cp.decide_agent_permission(
            agent_id, tool_name, user.id
        )
        
        return {
            "allowed": decision.action.value == "allow",
            "action": decision.action.value,
            "reason": decision.reason,
            "metadata": decision.metadata,
            "decision_id": decision.decision_id,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"decide_agent_permission failed: {e}")
        raise HTTPException(status_code=500, detail="decide_permission_failed")


@router.post("/decide/evolution")
async def decide_system_evolution(
    evolution_request: Dict[str, Any],
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """决策系统进化操作是否允许
    
    请求体：
    {
        "operation_type": "memory_maintenance|persona_update|conflict_resolution|decision_archive",
        "actions": [],
        "coordinator_authorized": true,
        "evidence_count": 10,
        "confidence": 0.8,
        "decision_age_days": 60,
        "decision_status": "resolved",
        "approved": false
    }
    
    返回：
    {
        "allowed": true/false,
        "action": "allow|reject|route",
        "reason": "决策原因",
        "metadata": {},
        "decision_id": "决策ID"
    }
    """
    try:
        router_cp = ControlPlaneRouter(db)
        decision = await router_cp.decide_evolution(evolution_request, user.id)
        
        return {
            "allowed": decision.action.value == "allow",
            "action": decision.action.value,
            "reason": decision.reason,
            "metadata": decision.metadata,
            "decision_id": decision.decision_id,
        }
    except Exception as e:
        logger.exception(f"decide_system_evolution failed: {e}")
        raise HTTPException(status_code=500, detail="decide_evolution_failed")


@router.get("/memory/access/{memory_id}")
async def check_memory_access(
    memory_id: str,
    access_type: str = "read",
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """检查是否允许访问记忆
    
    参数：
    - memory_id: 记忆ID
    - access_type: 访问类型 (read/write/delete)
    
    返回：
    {
        "allowed": true/false,
        "reason": "决策原因",
        "access_type": "read",
        "sensitivity": "normal",
        "requires_confirmation": false
    }
    """
    try:
        gate = MemoryGate(db)
        result = await gate.can_access_memory(memory_id, user.id, access_type)
        
        return result
    except Exception as e:
        logger.exception(f"check_memory_access failed: {e}")
        raise HTTPException(status_code=500, detail="check_access_failed")
