from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from src.shared.db.database import get_db
from src.execution.models.agent_profile import AgentProfile, AgentType, RecallLevel, LLMProvider
from src.execution.models.custom_llm_provider import CustomLLMProvider
from src.memory.models.memory_source import MemorySource
from src.memory.models.raw_event import RawEvent, ProcessingStatus
from src.execution.models.memory_work import MemoryWorkCase, MemoryWorkEvidence
from src.shared.security.dependencies import get_current_user
from src.shared.ids.id_generator import generate_id
from src.shared.security.encryption import encrypt_value, decrypt_value
from src.shared.security.outbound_url import assert_safe_llm_endpoint
import secrets
import json
import logging

logger = logging.getLogger(__name__)

def _mask_api_key(key: str) -> str:
    """Mask API key for display: show first 3 and last 4 chars"""
    if not key:
        return None
    if len(key) <= 8:
        return '***'
    return f'{key[:3]}...{key[-4:]}'


def _enum_value(value):
    return value.value if hasattr(value, "value") else value


def _iso(value):
    return value.isoformat() if value else None


def _policy_summary(policy):
    return {
        "type": policy.get("type"),
        "enabled": bool(policy.get("enabled")),
        "description": policy.get("description", ""),
        "project_ids": policy.get("project_ids") or [],
        "memory_types": policy.get("memory_types") or [],
        "sensitivities": policy.get("sensitivities") or [],
        "metadata_any": policy.get("metadata_any") or [],
        "min_importance": policy.get("min_importance"),
        "min_confidence": policy.get("min_confidence"),
    }


def _agent_policy_summaries(agent: AgentProfile):
    scopes = agent.allowed_write_scopes or []
    if isinstance(scopes, dict):
        scopes = [scopes]
    if not isinstance(scopes, list):
        return []
    return [_policy_summary(scope) for scope in scopes if isinstance(scope, dict)]


def _mcp_config_snippet(agent: AgentProfile) -> str:
    return json.dumps(
        {
            "mcpServers": {
                "life-memory": {
                    "command": "python",
                    "args": ["-m", "src.mcp_server"],
                    "env": {
                        "LIFE_MEMORY_API_URL": "http://127.0.0.1:8000",
                        "LIFE_MEMORY_AGENT_ID": agent.id,
                        "LIFE_MEMORY_AGENT_TOKEN": "<shown-once-token>",
                    },
                }
            }
        },
        ensure_ascii=False,
        indent=2,
    )


def _external_agent_prompt(agent: AgentProfile) -> str:
    return f"""You are connected to Aion Memory Nexus through MCP.

Before starting project work, call memory_before_start or memory_search for relevant context.
Write new information only through memory_upload_event, memory_sync_existing, memory_upload_daily_delta, or memory_after_end.
Never write committed memories directly. The server routes RawEvent through the Working Agent and its evidence-governed commit service.
Use memory_policy_status to inspect your current write policy and memory_sync_status to verify sync health.

Agent ID: {agent.id}
Agent type: {agent.agent_type.value}
Default recall level: {agent.default_recall_level.value}
"""


def _mcp_test_prompt(agent: AgentProfile) -> str:
    return f"""Test this Life Memory MCP connection as agent {agent.id}.

1. Call tools/list and confirm memory_map, memory_sync_status, memory_policy_status, and memory_test_roundtrip are available.
2. Call memory_map and confirm recommended_flow, context_fields, and daily sync guidance exist.
3. Call memory_policy_status and confirm formal memory writes are autonomous and evidence-gated.
4. Call memory_upload_event with a low-risk test event and project_id life-memory-system.
5. Call memory_sync_existing with two test memories and stable external_id values.
6. Call memory_upload_daily_delta with one test memory, then repeat the same call to prove skipped_count increments.
7. Call memory_sync_status for project_id life-memory-system.
8. Call memory_test_roundtrip and then memory_search for the roundtrip content.
9. Report created/skipped/updated counts, event IDs, work-case and formal-memory IDs, MAP availability, and exact errors if any step fails.
"""


def _parse_json_list(value):
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        return parsed if isinstance(parsed, list) else []
    return []

router = APIRouter()

@router.get("")
async def list_agents(
    db: AsyncSession = Depends(get_db),
    user = Depends(get_current_user),
):
    result = await db.execute(select(AgentProfile).where(AgentProfile.user_id == user.id))
    agents = result.scalars().all()
    return [
        {
            "id": a.id,
            "agent_name": a.agent_name,
            "agent_type": a.agent_type.value,
            "default_recall_level": a.default_recall_level.value,
            "status": a.status,
            "is_default": a.is_default,
            "created_at": a.created_at,
            "last_seen_at": a.last_seen_at,
            "llm_provider": a.llm_provider.value if a.llm_provider else None,
            "llm_model": a.llm_model,
            "llm_temperature": a.llm_temperature,
            "llm_api_key": _mask_api_key(decrypt_value(a.llm_api_key)),
            "llm_max_tokens": a.llm_max_tokens,
            "mission": a.mission,
            "role": a.role,
            "goals": a.goals,
            "constraints": a.constraints,
            "instructions": a.instructions,
            "allowed_write_scopes": _agent_policy_summaries(a),
            "schedule_enabled": a.schedule_enabled,
            "event_extraction_interval": a.event_extraction_interval,
            "memory_organize_hour": a.memory_organize_hour,
            "weekly_summary_day": a.weekly_summary_day,
            "obsidian_sync_interval": a.obsidian_sync_interval,
        }
        for a in agents
    ]

@router.post("")
async def create_agent(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user = Depends(get_current_user),
):
    try:
        body = await request.json()
    except (ValueError, json.JSONDecodeError):
        body = {}
    
    agent_name = body.get("agent_name")
    agent_type = body.get("agent_type", "custom")
    default_recall_level = body.get("default_recall_level", "work_context")
    llm_provider = body.get("llm_provider")
    llm_model = body.get("llm_model")
    llm_api_key = body.get("llm_api_key")
    llm_api_base = body.get("llm_api_base")
    llm_temperature = body.get("llm_temperature", 0.7)
    llm_max_tokens = body.get("llm_max_tokens", 4096)
    custom_provider_key = body.get("custom_provider_key")
    mission = body.get("mission")
    role = body.get("role")
    goals = body.get("goals")
    constraints = body.get("constraints")
    instructions = body.get("instructions")
    
    if not agent_name:
        raise HTTPException(status_code=400, detail="agent_name is required")
    if llm_api_base:
        try:
            await assert_safe_llm_endpoint(
                llm_api_base,
                "ollama" if llm_provider == "ollama" else "openai",
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    
    if custom_provider_key:
        provider_check = await db.execute(
            select(CustomLLMProvider)
            .where(CustomLLMProvider.provider_key == custom_provider_key)
            .where(CustomLLMProvider.user_id == user.id)
        )
        if not provider_check.scalar_one_or_none():
            raise HTTPException(status_code=400, detail="custom_provider_key not found")
    
    token = secrets.token_urlsafe(32)
    from src.shared.security.auth import hash_token
    token_hash = hash_token(token)
    
    parsed_goals = _parse_json_list(goals)
    parsed_constraints = _parse_json_list(constraints)
    
    agent = AgentProfile(
        id=generate_id(),
        user_id=user.id,
        agent_name=agent_name,
        agent_type=AgentType(agent_type),
        default_recall_level=RecallLevel(default_recall_level),
        token_hash=token_hash,
        api_token_hash=token_hash,
        llm_provider=LLMProvider(llm_provider) if llm_provider else None,
        llm_model=llm_model,
        llm_api_key=encrypt_value(llm_api_key) if llm_api_key else None,
        llm_api_base=llm_api_base,
        llm_temperature=llm_temperature,
        llm_max_tokens=llm_max_tokens,
        custom_provider_key=custom_provider_key,
        mission=mission,
        role=role,
        goals=parsed_goals,
        constraints=parsed_constraints,
        instructions=instructions,
    )
    
    db.add(agent)
    await db.commit()
    await db.refresh(agent)
    
    return {
        "id": agent.id,
        "user_id": agent.user_id,
        "agent_name": agent.agent_name,
        "api_token": token,
        "message": "Save this token - it will only be shown once",
    }


@router.get("/{agent_id}")
async def get_agent(
    agent_id: str,
    db: AsyncSession = Depends(get_db),
    user = Depends(get_current_user),
):
    result = await db.execute(
        select(AgentProfile)
        .where(AgentProfile.id == agent_id)
        .where(AgentProfile.user_id == user.id)
    )
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    return {
        "id": agent.id,
        "agent_name": agent.agent_name,
        "agent_type": agent.agent_type.value,
        "default_recall_level": agent.default_recall_level.value,
        "status": agent.status,
        "is_default": agent.is_default,
        "created_at": agent.created_at,
        "last_seen_at": agent.last_seen_at,
        "mission": agent.mission,
        "role": agent.role,
        "goals": agent.goals,
        "constraints": agent.constraints,
        "instructions": agent.instructions,
        "allowed_write_scopes": _agent_policy_summaries(agent),
    }


@router.get("/{agent_id}/bridge-status")
async def get_agent_bridge_status(
    agent_id: str,
    db: AsyncSession = Depends(get_db),
    user = Depends(get_current_user),
):
    result = await db.execute(
        select(AgentProfile)
        .where(AgentProfile.id == agent_id)
        .where(AgentProfile.user_id == user.id)
    )
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    event_result = await db.execute(
        select(RawEvent)
        .where(RawEvent.user_id == user.id)
        .where(RawEvent.agent_id == agent.id)
        .order_by(RawEvent.ingested_at.desc())
        .limit(100)
    )
    events = event_result.scalars().all()
    event_ids = [event.id for event in events]
    event_id_set = set(event_ids)

    work_case_ids: set[str] = set()
    if event_id_set:
        case_result = await db.execute(
            select(MemoryWorkCase.id)
            .join(MemoryWorkEvidence, MemoryWorkEvidence.case_id == MemoryWorkCase.id)
            .where(
                MemoryWorkCase.user_id == user.id,
                MemoryWorkEvidence.raw_event_id.in_(event_id_set),
            )
        )
        work_case_ids = set(case_result.scalars().all())

    committed_count = 0
    if event_ids:
        source_result = await db.execute(
            select(MemorySource).where(MemorySource.raw_event_id.in_(event_ids))
        )
        committed_count = len({source.memory_id for source in source_result.scalars().all()})

    duplicate_skipped_count = 0
    processing_counts = {}
    recent_errors = []
    for event in events:
        status = _enum_value(event.processing_status)
        processing_counts[status] = processing_counts.get(status, 0) + 1
        metadata = event.event_metadata or {}
        duplicate_skipped_count += int(metadata.get("duplicate_seen_count") or 0)
        if event.processing_status == ProcessingStatus.FAILED:
            recent_errors.append({
                "event_id": event.id,
                "source_name": metadata.get("sync_source"),
                "message": metadata.get("last_error") or "Memory extraction failed",
                "ingested_at": _iso(event.ingested_at),
            })

    policies = _agent_policy_summaries(agent)
    return {
        "agent": {
            "id": agent.id,
            "agent_name": agent.agent_name,
            "agent_type": agent.agent_type.value,
            "default_recall_level": agent.default_recall_level.value,
            "last_seen_at": _iso(agent.last_seen_at),
        },
        "mcp_config": _mcp_config_snippet(agent),
        "external_agent_prompt": _external_agent_prompt(agent),
        "mcp_test_prompt": _mcp_test_prompt(agent),
        "policy_status": {
            "allowed_write_scopes": policies,
            "autonomous_memory_enabled": True,
            "governance_boundary": "working_agent_only",
        },
        "sync_status": {
            "raw_event_count": len(events),
            "work_case_count": len(work_case_ids),
            "committed_count": committed_count,
            "duplicate_skipped_count": duplicate_skipped_count,
            "processing_counts": processing_counts,
            "recent_errors": recent_errors[:10],
            "last_sync_at": _iso(events[0].ingested_at) if events else None,
        },
        "token_display": "shown_only_on_create_or_regenerate",
    }


@router.put("/{agent_id}")
async def update_agent(
    agent_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user = Depends(get_current_user),
):
    try:
        body = await request.json()
    except (ValueError, json.JSONDecodeError):
        body = {}
    
    result = await db.execute(select(AgentProfile).where(AgentProfile.id == agent_id).where(AgentProfile.user_id == user.id))
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    
    if body.get("agent_name"):
        agent.agent_name = body["agent_name"]
    if body.get("agent_type"):
        agent.agent_type = AgentType(body["agent_type"])
    if body.get("default_recall_level"):
        agent.default_recall_level = RecallLevel(body["default_recall_level"])
    if body.get("llm_provider"):
        agent.llm_provider = LLMProvider(body["llm_provider"])
    if "llm_model" in body:
        agent.llm_model = body["llm_model"]
    if "llm_api_key" in body:
        agent.llm_api_key = encrypt_value(body["llm_api_key"]) if body["llm_api_key"] else None
    if "llm_api_base" in body:
        if body["llm_api_base"]:
            provider_name = body.get("llm_provider") or _enum_value(agent.llm_provider)
            try:
                await assert_safe_llm_endpoint(
                    body["llm_api_base"],
                    "ollama" if provider_name == "ollama" else "openai",
                )
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        agent.llm_api_base = body["llm_api_base"]
    if "llm_temperature" in body:
        agent.llm_temperature = body["llm_temperature"]
    if "llm_max_tokens" in body:
        agent.llm_max_tokens = body["llm_max_tokens"]
    if "custom_provider_key" in body:
        if body["custom_provider_key"]:
            provider_check = await db.execute(
                select(CustomLLMProvider)
                .where(CustomLLMProvider.provider_key == body["custom_provider_key"])
                .where(CustomLLMProvider.user_id == user.id)
            )
            if not provider_check.scalar_one_or_none():
                raise HTTPException(status_code=400, detail="custom_provider_key not found")
        agent.custom_provider_key = body["custom_provider_key"]
    if "mission" in body:
        agent.mission = body["mission"]
    if "role" in body:
        agent.role = body["role"]
    if "goals" in body:
        try:
            agent.goals = json.loads(body["goals"])
        except (json.JSONDecodeError, TypeError):
            agent.goals = []
    if "constraints" in body:
        try:
            agent.constraints = json.loads(body["constraints"])
        except (json.JSONDecodeError, TypeError):
            agent.constraints = []
    if "instructions" in body:
        agent.instructions = body["instructions"]
    if "schedule_enabled" in body:
        agent.schedule_enabled = body["schedule_enabled"]
    if "event_extraction_interval" in body:
        agent.event_extraction_interval = body["event_extraction_interval"]
    if "memory_organize_hour" in body:
        agent.memory_organize_hour = body["memory_organize_hour"]
    if "weekly_summary_day" in body:
        agent.weekly_summary_day = body["weekly_summary_day"]
    if "obsidian_sync_interval" in body:
        agent.obsidian_sync_interval = body["obsidian_sync_interval"]
    
    await db.commit()
    await db.refresh(agent)
    
    try:
        from src.shared.db.scheduler import update_scheduler_from_config
        update_scheduler_from_config()
    except Exception as e:
        logger.warning(f"Scheduler update failed: {e}")
    
    return {
        "id": agent.id,
        "agent_name": agent.agent_name,
        "status": "updated",
    }

@router.delete("/{agent_id}")
async def delete_agent(
    agent_id: str,
    db: AsyncSession = Depends(get_db),
    user = Depends(get_current_user),
):
    result = await db.execute(select(AgentProfile).where(AgentProfile.id == agent_id).where(AgentProfile.user_id == user.id))
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    
    if agent.is_default:
        raise HTTPException(status_code=403, detail="默认助手不可删除")
    
    agent.status = False
    await db.commit()
    
    return {"status": "disabled"}

@router.post("/{agent_id}/regenerate-token")
async def regenerate_agent_token(
    agent_id: str,
    db: AsyncSession = Depends(get_db),
    user = Depends(get_current_user),
):
    result = await db.execute(select(AgentProfile).where(AgentProfile.id == agent_id).where(AgentProfile.user_id == user.id))
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    token = secrets.token_urlsafe(32)
    from src.shared.security.auth import hash_token
    token_hash = hash_token(token)
    agent.token_hash = token_hash
    agent.api_token_hash = token_hash

    await db.commit()
    await db.refresh(agent)

    return {
        "id": agent.id,
        "user_id": agent.user_id,
        "agent_name": agent.agent_name,
        "api_token": token,
        "message": "Save this token - it will only be shown once",
    }

@router.get("/{agent_id}/prompt")
async def get_agent_prompt(
    agent_id: str,
    regenerate_token: bool = False,
    prompt_type: str = "system",
    db: AsyncSession = Depends(get_db),
    user = Depends(get_current_user),
):
    """
    生成 Agent 提示词
    
    prompt_type:
    - system: 系统提示词（用于 API 调用）
    - mcp: MCP 配置说明（用于支持 MCP 的客户端）
    - simple: 简化版提示词（直接复制使用）
    """
    result = await db.execute(select(AgentProfile).where(AgentProfile.id == agent_id).where(AgentProfile.user_id == user.id))
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    if not agent.status:
        raise HTTPException(status_code=400, detail="Agent is disabled")
    
    api_token = None
    if regenerate_token:
        api_token = secrets.token_urlsafe(32)
        from src.shared.security.auth import hash_token
        token_hash = hash_token(api_token)
        agent.token_hash = token_hash
        agent.api_token_hash = token_hash
        await db.commit()
        await db.refresh(agent)
    
    # MCP 配置说明
    if prompt_type == "mcp":
        mcp_prompt = f"""
# Aion Memory Nexus · 永识中枢 - MCP 集成配置

## 快速开始

你的 Agent 已经配置好连接到 Aion Memory Nexus（永识中枢）。以下是 MCP 集成方式：

### 方式一：使用 MCP Server（推荐）

如果你使用的客户端支持 MCP（如 Claude Desktop、Cursor、Windsurf），添加以下配置：

**claude_desktop_config.json:**
```json
{{
  "mcpServers": {{
    "life-memory": {{
      "command": "python",
      "args": ["-m", "src.mcp_server"],
      "env": {{
        "LIFE_MEMORY_API_URL": "http://127.0.0.1:8000",
        "LIFE_MEMORY_AGENT_TOKEN": "{api_token or '<从管理界面获取>'}",
        "LIFE_MEMORY_AGENT_ID": "{agent.id}"
      }}
    }}
  }}
}}
```

**Cursor / Windsurf (.mcp.json):**
```json
{{
  "mcpServers": {{
    "life-memory": {{
      "command": "python",
      "args": ["-m", "src.mcp_server"],
      "env": {{
        "LIFE_MEMORY_API_URL": "http://127.0.0.1:8000",
        "LIFE_MEMORY_AGENT_TOKEN": "{api_token or '<从管理界面获取>'}",
        "LIFE_MEMORY_AGENT_ID": "{agent.id}"
      }}
    }}
  }}
}}
```

### 方式二：直接在 System Prompt 中集成

如果你无法使用 MCP，将以下提示词添加到你的 System Prompt 中：

---

{generate_simple_prompt(agent, api_token)}

---

### 可用的 MCP 工具

配置完成后，你可以直接使用以下工具：

1. **memory_search** - 搜索相关记忆
2. **memory_before_start** - 任务开始前获取上下文
3. **memory_after_end** - 任务结束后保存信息
4. **memory_upload_event** - 上传原始事件
5. **memory_list_types** - 查看支持的类型

### 使用示例

**搜索记忆：**
```
调用 memory_search 工具，query="项目A的技术选型"
```

**任务开始前：**
```
调用 memory_before_start 工具，task="重构用户认证模块"
```

**任务结束后：**
```
调用 memory_after_end 工具，
task="重构用户认证模块",
summary="完成了JWT认证重构",
decisions=["选择RS256算法"],
artifacts=["auth.py"]
```

### 配置信息

- **Agent ID**: {agent.id}
- **Agent 名称**: {agent.agent_name}
- **API Token**: {api_token or '<请在管理界面生成>'}
- **服务地址**: http://127.0.0.1:8000
"""
        return {
            "agent_id": agent.id,
            "agent_name": agent.agent_name,
            "api_token": api_token,
            "prompt": mcp_prompt.strip(),
            "prompt_type": "mcp",
        }
    
    # 简化版提示词
    elif prompt_type == "simple":
        simple_prompt = generate_simple_prompt(agent, api_token)
        return {
            "agent_id": agent.id,
            "agent_name": agent.agent_name,
            "api_token": api_token,
            "prompt": simple_prompt,
            "prompt_type": "simple",
        }
    
    # 默认：完整系统提示词
    else:
        agent_role_section = f"""
=== 角色定义 ===
角色: {agent.role or 'AI助手'}
使命: {agent.mission or '帮助用户完成任务，同时管理和利用记忆系统'}
""" if agent.role or agent.mission else ""

        agent_goals_section = f"""
=== 工作目标 ===
{chr(10).join([f"{i+1}. {goal}" for i, goal in enumerate(agent.goals)])}
""" if agent.goals else ""

        agent_constraints_section = f"""
=== 约束规则 ===
{chr(10).join([f"- {constraint}" for constraint in agent.constraints])}
""" if agent.constraints else ""

        agent_instructions_section = f"""
=== 工作指令 ===
{agent.instructions}
""" if agent.instructions else ""

        agent_llm_section = f"""
=== LLM配置 ===
提供商: {agent.llm_provider.value if agent.llm_provider else '默认'}
模型: {agent.llm_model or '默认'}
温度: {agent.llm_temperature}
最大Token: {agent.llm_max_tokens}
""" if agent.llm_provider or agent.llm_model else ""

        prompt = f"""
你是一个连接到“Aion Memory Nexus（永识中枢）”的 AI 助手。请严格按照以下规则操作：

=== 系统配置 ===
服务地址: http://127.0.0.1:8000
代理ID: {agent.id}
代理名称: {agent.agent_name}
代理类型: {agent.agent_type.value}
默认召回级别: {agent.default_recall_level.value}
API Token: {api_token if api_token else '<请在创建代理时获取或重新生成>'}

{agent_llm_section}

{agent_role_section}

{agent_goals_section}

{agent_constraints_section}

{agent_instructions_section}

=== 核心规则 ===
1. 在每次对话开始时，调用记忆系统获取相关记忆作为上下文
2. 在每次对话结束时，调用记忆系统保存本次对话中的关键信息
3. 自动识别对话中的决策、事实、项目上下文等有价值的信息

=== API 调用指南 ===

【获取思维上下文（推荐）】(对话开始时调用)
POST http://127.0.0.1:8000/api/agent/before-start
Headers: X-Agent-Token: <你的token>
Body:
{{
    "agent_id": "{agent.id}",
    "task": "当前任务描述",
    "project_id": "项目名称(可选)",
    "recall_level": "{agent.default_recall_level.value}",
    "top_k": 10
}}

返回包含：context_summary, decision_history, patterns, conflicts, relevant_memories, entities
注意：这是"思维上下文重建"，不是直接答案。

【保存新记忆】(对话结束时调用)
POST http://127.0.0.1:8000/api/agent/after-end
Headers: X-Agent-Token: <你的token>
Body:
{{
    "agent_id": "{agent.id}",
    "session_summary": "会话摘要",
    "decisions": [{{"content": "决策内容"}}],
    "actions": [{{"content": "执行的操作"}}],
    "artifacts": [{{"name": "产生的成果"}}],
    "project_id": "项目名称(可选)"
}}

此接口：
1. 创建不可变的 RawEvent
2. 异步触发工作 Agent 进行案件归并、证据治理和正式记忆写入
3. 立即返回，不阻塞

【搜索记忆（推荐）】(对话中随时调用)
POST http://127.0.0.1:8000/api/agent/search
Headers: X-Agent-Token: <你的token>
Body:
{{
    "agent_id": "{agent.id}",
    "task": "搜索问题",
    "project_id": "项目名称(可选)",
    "recall_level": "{agent.default_recall_level.value}",
    "top_k": 5
}}

使用 Retrieval Engine 重建思维背景，包含决策历史、模式、冲突。

【列出支持的 Agent 类型】
GET http://127.0.0.1:8000/api/agent/types
Headers: X-Agent-Token: <你的token>

【传统检索（保留兼容）】(不推荐，建议用 search)
POST http://127.0.0.1:8000/api/memory/search
Headers: X-Agent-Token: <你的token>
Body:
{{
    "query": "搜索关键词",
    "top_k": 5
}}

=== 自动执行流程 ===

每次对话开始时：
1. 分析当前任务
2. 调用 before-start 获取相关记忆
3. 将获取的记忆作为上下文融入回答

每次对话结束时：
1. 总结本次会话的关键信息
2. 识别有价值的记忆点（决策、事实、洞察等）
3. 调用 after-end 保存这些信息

在对话过程中：
- 如果用户提到某个项目或主题，主动搜索相关记忆
- 如果做出了重要决策，记录下来准备保存
- 如果发现与已有记忆冲突，标记出来

=== 记忆类型说明 ===
- decision: 重要决策，如技术选型、方案选择
- fact: 事实信息，如代码片段、配置参数
- project_context: 项目上下文，如项目背景、目标
- task: 任务信息，如待办事项、进度
- insight: 洞察发现，如规律总结、经验教训
- preference: 用户偏好，如习惯、喜好

=== 敏感级别说明 ===
- public: 公开信息，可随意使用
- normal: 正常信息，可在工作中使用
- sensitive: 敏感信息，谨慎使用
- private: 私密信息，仅在完全信任场景使用

请将此提示词作为你的核心指令，在每次对话中严格执行记忆系统的调用流程。
"""
    
    return {
        "agent_id": agent.id,
        "agent_name": agent.agent_name,
        "api_token": api_token,
        "prompt": prompt.strip(),
        "prompt_type": "system",
    }


def generate_simple_prompt(agent: AgentProfile, api_token: str = None) -> str:
    """生成简化版提示词"""
    token_display = api_token if api_token else '<请在管理界面生成>'
    
    return f"""你是"{agent.agent_name}"，一个连接到 Aion Memory Nexus（永识中枢）的 AI 助手。

## 核心职责
{agent.mission or '帮助用户完成任务，同时管理和利用记忆系统'}

## 必须遵守的规则
1. **每次对话开始前**：调用 before-start 接口获取相关记忆
2. **每次对话结束后**：调用 after-end 接口保存关键信息
3. **对话过程中**：主动搜索相关记忆辅助回答

## 系统配置
- 服务地址: http://127.0.0.1:8000
- Agent ID: {agent.id}
- API Token: {token_display}
- 召回级别: {agent.default_recall_level.value}

## API 调用方法

### 1. 获取上下文（对话开始时）
```
POST http://127.0.0.1:8000/api/agent/before-start
Headers: X-Agent-Token: {token_display}
Body: {{"agent_id": "{agent.id}", "task": "当前任务", "recall_level": "{agent.default_recall_level.value}"}}
```

### 2. 保存信息（对话结束时）
```
POST http://127.0.0.1:8000/api/agent/after-end
Headers: X-Agent-Token: {token_display}
Body: {{"agent_id": "{agent.id}", "session_summary": "摘要", "decisions": [], "actions": [], "artifacts": []}}
```

### 3. 搜索记忆（随时调用）
```
POST http://127.0.0.1:8000/api/agent/search
Headers: X-Agent-Token: {token_display}
Body: {{"agent_id": "{agent.id}", "task": "搜索问题"}}
```

## 工作流程

**开始任务时：**
1. 调用 before-start 获取相关记忆
2. 将记忆融入你的回答

**任务进行中：**
- 主动搜索相关记忆
- 记录重要决策和发现

**任务结束时：**
1. 总结关键信息
2. 调用 after-end 保存

## 记忆类型
- decision（决策）、fact（事实）、project_context（项目上下文）
- task（任务）、insight（洞察）、preference（偏好）

请立即开始执行，在每次对话中严格遵守上述流程。
"""
