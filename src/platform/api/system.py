"""系统级 API 端点"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from src.shared.db.database import get_db, async_session
from src.shared.security.dependencies import get_current_user
from src.shared.config import get_system_api_token, settings
from src.execution.models.agent_profile import AgentProfile, AgentType, RecallLevel, LLMProvider
from src.memory.models.committed_memory import CommittedMemory, CommittedStatus
from src.memory.models.raw_event import RawEvent
from datetime import datetime
import secrets
from src.shared.security.auth import hash_token
from src.shared.ids.id_generator import generate_id
from src.shared.version import get_product_version
from pydantic import BaseModel

router = APIRouter(prefix="/system", tags=["system"])


def _database_backend_label(database_url: str) -> str:
    """Return a safe backend label without exposing connection details."""
    normalized = (database_url or "").lower()
    if normalized.startswith("sqlite"):
        return "sqlite"
    if normalized.startswith("postgresql"):
        return "postgresql"
    return "unknown"


class CreateAgentFromPresetRequest(BaseModel):
    preset_name: str


@router.get("/token")
async def get_system_token(user=Depends(get_current_user)):
    """获取系统级 API Token"""
    token = get_system_api_token()
    return {
        "system_api_token": token,
        "message": "系统级 Token，任何外部 Agent 都可以使用此 Token 接入系统"
    }


@router.post("/token/regenerate")
async def regenerate_system_token(user=Depends(get_current_user)):
    """重新生成系统级 API Token"""
    from src.shared.config import _save_system_token_to_env
    new_token = secrets.token_urlsafe(32)
    _save_system_token_to_env(new_token)
    return {
        "system_api_token": new_token,
        "message": "系统级 Token 已重新生成；如需持久化，请同步更新部署环境变量"
    }


@router.get("/presets")
async def get_presets(user=Depends(get_current_user)):
    """获取预设 Agent 模板"""
    return {
        "presets": [
            {
                "name": "Codex 编程助手",
                "agent_type": "codex",
                "role": "高级软件工程师",
                "mission": "帮助用户完成编程任务，自动记录代码决策和技术上下文",
                "default_recall_level": "task_only",
                "instructions": """你是一个连接到 Aion Memory Nexus（永识中枢）的编程助手。

核心职责：
1. 在每次编程任务开始前，调用 before-start 获取相关项目记忆
2. 在任务完成后，调用 after-end 保存技术决策和代码上下文
3. 主动搜索相关记忆辅助编程决策

记忆类型：
- decision: 技术选型、架构决策
- fact: 代码片段、配置参数
- project_context: 项目背景、技术栈
- task: 待办事项、进度跟踪
- insight: 编程经验、最佳实践""",
                "goals": [
                    "提供高质量的编程建议",
                    "自动记录技术决策",
                    "积累项目上下文知识"
                ],
                "constraints": [
                    "不保存敏感信息（密码、密钥）",
                    "只记录有价值的技术决策",
                    "尊重用户隐私"
                ]
            },
            {
                "name": "OpenClaw 浏览器自动化",
                "agent_type": "openclaw",
                "role": "浏览器自动化专家",
                "mission": "帮助用户完成浏览器自动化任务，记录操作流程和关键信息",
                "default_recall_level": "task_only",
                "instructions": """你是一个连接到 Aion Memory Nexus（永识中枢）的浏览器自动化助手。

核心职责：
1. 在每次任务开始前，调用 before-start 获取相关操作记忆
2. 在任务完成后，调用 after-end 保存操作流程和关键发现
3. 主动搜索相关记忆辅助自动化决策

记忆类型：
- fact: 网页结构、元素定位
- task: 操作步骤、流程记录
- insight: 自动化经验、最佳实践""",
                "goals": [
                    "高效完成浏览器自动化任务",
                    "记录可复用的操作流程",
                    "积累网页结构知识"
                ],
                "constraints": [
                    "不保存敏感信息",
                    "只记录有价值的操作信息",
                    "尊重用户隐私"
                ]
            },
            {
                "name": "Claude Code 编程助手",
                "agent_type": "claude_code",
                "role": "全栈开发工程师",
                "mission": "帮助用户完成全栈开发任务，自动记录代码决策和技术上下文",
                "default_recall_level": "work_context",
                "instructions": """你是一个连接到 Aion Memory Nexus（永识中枢）的全栈开发助手。

核心职责：
1. 在每次开发任务开始前，调用 before-start 获取相关项目记忆
2. 在任务完成后，调用 after-end 保存技术决策和代码上下文
3. 主动搜索相关记忆辅助开发决策

记忆类型：
- decision: 技术选型、架构决策
- fact: 代码片段、配置参数
- project_context: 项目背景、技术栈
- task: 待办事项、进度跟踪
- insight: 开发经验、最佳实践""",
                "goals": [
                    "提供高质量的全栈开发建议",
                    "自动记录技术决策",
                    "积累项目上下文知识"
                ],
                "constraints": [
                    "不保存敏感信息（密码、密钥）",
                    "只记录有价值的技术决策",
                    "尊重用户隐私"
                ]
            },
            {
                "name": "通用记忆助手",
                "agent_type": "custom",
                "role": "智能助手",
                "mission": "帮助用户完成各种任务，自动记录和管理重要信息",
                "default_recall_level": "work_context",
                "instructions": """你是一个连接到 Aion Memory Nexus（永识中枢）的通用助手。

核心职责：
1. 在每次任务开始前，调用 before-start 获取相关记忆
2. 在任务完成后，调用 after-end 保存关键信息
3. 主动搜索相关记忆辅助决策

记忆类型：
- decision: 重要决策
- fact: 事实信息
- project_context: 项目上下文
- task: 任务信息
- insight: 经验洞察
- preference: 用户偏好""",
                "goals": [
                    "提供高质量的帮助",
                    "自动记录重要信息",
                    "积累用户知识"
                ],
                "constraints": [
                    "不保存敏感信息",
                    "只记录有价值的信息",
                    "尊重用户隐私"
                ]
            }
        ]
    }


@router.post("/agents/create-from-preset")
async def create_agent_from_preset(
    request: CreateAgentFromPresetRequest,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user)
):
    """从预设模板创建 Agent"""
    presets_response = await get_presets(user)
    presets = presets_response["presets"]
    
    preset = next((p for p in presets if p["name"] == request.preset_name), None)
    if not preset:
        raise HTTPException(status_code=404, detail=f"预设模板 '{request.preset_name}' 不存在")
    
    # 生成 API Token
    api_token = secrets.token_urlsafe(32)
    token_hash = hash_token(api_token)
    
    # 创建 Agent
    agent_id = generate_id("agent")
    agent = AgentProfile(
        id=agent_id,
        user_id=user.id,
        agent_name=preset["name"],
        agent_type=AgentType(preset["agent_type"]),
        role=preset["role"],
        mission=preset["mission"],
        default_recall_level=RecallLevel(preset["default_recall_level"]),
        instructions=preset["instructions"],
        goals=preset["goals"],
        constraints=preset["constraints"],
        token_hash=token_hash,
        api_token_hash=token_hash,
        status=True,
        llm_provider=LLMProvider.DEEPSEEK,
        llm_model="deepseek-chat",
        llm_temperature=0.7,
        llm_max_tokens=4096,
    )
    
    db.add(agent)
    await db.commit()
    await db.refresh(agent)
    
    return {
        "agent_id": agent.id,
        "agent_name": agent.agent_name,
        "api_token": api_token,
        "message": f"Agent '{request.preset_name}' 已创建，请保存此 Token"
    }


@router.get("/info")
async def get_system_info(user=Depends(get_current_user)):
    """获取系统信息"""
    return {
        "version": get_product_version(),
        "environment": settings.ENVIRONMENT,
        "database": _database_backend_label(settings.POSTGRES_URL),
        "features": {
            "memory_governance": True,
            "cognitive_advisor": True,
            "persona_engine": True,
            "obsidian_sync": True,
            "multi_agent": True,
        }
    }


@router.get("/stats")
async def get_system_stats(
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user)
):
    """获取系统统计信息"""
    # 统计记忆数量
    memory_count_result = await db.execute(
        select(func.count(CommittedMemory.id)).where(
            CommittedMemory.user_id == user.id,
            CommittedMemory.status == CommittedStatus.ACTIVE
        )
    )
    memory_count = memory_count_result.scalar() or 0
    
    # 统计今日记忆
    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    today_memory_result = await db.execute(
        select(func.count(CommittedMemory.id)).where(
            CommittedMemory.user_id == user.id,
            CommittedMemory.status == CommittedStatus.ACTIVE,
            CommittedMemory.created_at >= today_start
        )
    )
    today_memory_count = today_memory_result.scalar() or 0
    
    # 统计 Agent 数量
    agent_count_result = await db.execute(
        select(func.count(AgentProfile.id)).where(
            AgentProfile.user_id == user.id,
            AgentProfile.status.is_(True)
        )
    )
    agent_count = agent_count_result.scalar() or 0
    
    # 统计事件数量
    event_count_result = await db.execute(
        select(func.count(RawEvent.id)).where(
            RawEvent.user_id == user.id
        )
    )
    event_count = event_count_result.scalar() or 0

    return {
        "memory_count": memory_count,
        "today_memory_count": today_memory_count,
        "agent_count": agent_count,
        "event_count": event_count,
        "storage_used_mb": 0,  # 可选：计算实际存储
    }


@router.get("/health")
async def get_system_health():
    """系统健康检查"""
    db_ok = True
    try:
        async with async_session() as session:
            from sqlalchemy import text
            await session.execute(text("SELECT 1"))
    except Exception:
        db_ok = False
    
    return {
        "status": "healthy" if db_ok else "degraded",
        "database": "connected" if db_ok else "disconnected",
        "timestamp": datetime.utcnow().isoformat()
    }
