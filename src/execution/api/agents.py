import asyncio
import logging
import re
import secrets
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified
from src.shared.db.database import get_db
from src.execution.models.agent_profile import AgentProfile, AgentType, RecallLevel, LLMProvider
from src.memory.models.raw_event import (
    RawEvent,
    SourceType,
    SensitivityLevel,
    VisibilityScope,
    ProcessingStatus,
)
from src.memory.models.committed_memory import CommittedMemory, CommittedStatus
from src.memory.models.memory_source import MemorySource
from src.execution.models.memory_work import MemoryWorkCase, MemoryWorkDecision, MemoryWorkEvidence
from src.execution.schemas.agents import (
    AgentBeforeStartRequest,
    AgentBeforeStartResponse,
    AgentAfterEndRequest,
    AgentAfterEndResponse,
)
from src.shared.security.dependencies import get_current_agent, get_current_user
from src.shared.ids.id_generator import generate_id
from src.shared.utils.hash import compute_content_hash
from src.shared.security.auth import hash_token
from src.memory.services.retrieval_engine import RetrievalEngine
from src.memory.services.governance_policy import (
    allowed_read_scope_ceiling,
    clamp_recall_level as clamp_recall_level_value,
)
from src.memory.tasks.memory_extraction import _process_memory_event
from src.memory.services.memory_os import build_agent_memory_protocol
from src.execution.models.user import User
from src.memory.services.event_ingestion import EventIngestionService, trigger_ingested_event
from src.shared.config import settings


logger = logging.getLogger(__name__)
router = APIRouter()
MOJIBAKE_MARKERS = ("Ã", "Â", "�")
SYNC_IDENTIFIER_FIELDS = ("project_id", "repo_id", "workspace_id", "external_id")
def _clamp_recall_level(
    requested: RecallLevel,
    allowed: RecallLevel | None,
) -> RecallLevel:
    return RecallLevel(clamp_recall_level_value(requested.value, allowed.value if allowed else None))


def _effective_agent_recall_level(requested: RecallLevel, agent: AgentProfile) -> RecallLevel:
    """Apply both the legacy profile default and configured read-scope ceiling."""
    default = agent.default_recall_level or RecallLevel.TASK_ONLY
    policy_ceiling = allowed_read_scope_ceiling(
        agent.allowed_read_scopes,
        default_recall_level=default.value,
    )
    return _clamp_recall_level(requested, RecallLevel(policy_ceiling))


# ---------------------------------------------------------------------------
# Pydantic models for Agent management
# ---------------------------------------------------------------------------

class AgentCreateRequest(BaseModel):
    agent_name: str
    agent_type: str = "custom"
    role: str = ""
    mission: str = ""
    default_recall_level: str = "work_context"
    instructions: str = ""
    goals: List[str] = []
    constraints: List[str] = []
    llm_provider: str = "deepseek"
    llm_model: str = "deepseek-chat"
    llm_temperature: float = 0.7
    llm_max_tokens: int = 4096


class AgentUpdateRequest(BaseModel):
    agent_name: Optional[str] = None
    role: Optional[str] = None
    mission: Optional[str] = None
    default_recall_level: Optional[str] = None
    instructions: Optional[str] = None
    goals: Optional[List[str]] = None
    constraints: Optional[List[str]] = None
    llm_provider: Optional[str] = None
    llm_model: Optional[str] = None
    llm_temperature: Optional[float] = None
    llm_max_tokens: Optional[int] = None
    status: Optional[bool] = None


class AgentEventIngestRequest(BaseModel):
    agent_id: str = ""
    content: str = Field(..., min_length=1, max_length=50000)
    source_type: str = "agent"
    project_id: Optional[str] = None
    repo_id: Optional[str] = None
    workspace_id: Optional[str] = None
    occurred_at: Optional[datetime] = None
    sensitivity: str = "normal"
    visibility_scope: str = "project"
    metadata: Dict[str, Any] = Field(default_factory=dict)
    dedupe: bool = True
    trigger_extraction: bool = True


class AgentMemorySyncItem(BaseModel):
    content: str = Field(..., min_length=1, max_length=50000)
    title: Optional[str] = None
    memory_type: Optional[str] = None
    project_id: Optional[str] = None
    repo_id: Optional[str] = None
    workspace_id: Optional[str] = None
    external_id: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class AgentMemorySyncRequest(BaseModel):
    agent_id: str = ""
    source_name: str = "agent_memory"
    default_project_id: Optional[str] = None
    memories: List[AgentMemorySyncItem] = Field(default_factory=list)
    dedupe: bool = True
    trigger_extraction: bool = True
    client_validation_warnings: List[Dict[str, Any]] = Field(default_factory=list)


class AgentSyncStatusRequest(BaseModel):
    agent_id: str = ""
    source_name: Optional[str] = None
    project_id: Optional[str] = None
    limit: int = Field(default=50, ge=1, le=500)


class AgentRoundtripRequest(BaseModel):
    agent_id: str = ""
    content: str = Field(
        default="Agent Memory Bridge roundtrip test: store a low-risk fact for MCP verification.",
        min_length=1,
        max_length=50000,
    )
    project_id: Optional[str] = "life-memory-system"
    source_name: str = "agent_memory_bridge_roundtrip"
    metadata: Dict[str, Any] = Field(default_factory=dict)
    recall_level: str = "work_context"
    top_k: int = Field(default=5, ge=1, le=20)


class PublicMcpBootstrapRequest(BaseModel):
    """The deliberately small public input surface for single-owner setup."""

    agent_name: str = Field(min_length=1, max_length=80, pattern=r"^[\w .-]+$")
    project_id: str = Field(
        default="life-memory-system",
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9_.-]+$",
    )


class AgentResponse(BaseModel):
    id: str
    agent_name: str
    agent_type: str
    role: str
    mission: str
    default_recall_level: str
    instructions: str
    goals: List[str]
    constraints: List[str]
    status: bool
    llm_provider: str
    llm_model: str
    llm_temperature: float
    llm_max_tokens: int
    created_at: str
    updated_at: str
    is_default: bool = False


class AgentListResponse(BaseModel):
    agents: List[AgentResponse]
    total: int


def _enum_value(value: Any) -> Any:
    return value.value if hasattr(value, "value") else value


def _iso(value: Any) -> Optional[str]:
    return value.isoformat() if value else None


def _mark_duplicate_seen(event: RawEvent, reason: str) -> None:
    metadata = dict(event.event_metadata or {})
    metadata["duplicate_seen_count"] = int(metadata.get("duplicate_seen_count") or 0) + 1
    metadata["last_duplicate_seen_at"] = datetime.now(timezone.utc).isoformat()
    metadata["last_sync_result"] = "skipped"
    metadata["last_skip_reason"] = reason
    event.event_metadata = metadata
    flag_modified(event, "event_metadata")


def _policy_summary(policy: dict) -> dict:
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


def _iter_policy_summaries(agent: AgentProfile) -> list[dict]:
    raw_policies = agent.allowed_write_scopes or []
    if isinstance(raw_policies, dict):
        raw_policies = [raw_policies]
    if not isinstance(raw_policies, list):
        return []
    return [_policy_summary(policy) for policy in raw_policies if isinstance(policy, dict)]


def _event_matches_sync_filters(
    event: RawEvent,
    *,
    source_name: Optional[str] = None,
    project_id: Optional[str] = None,
) -> bool:
    if project_id and event.project_id != project_id:
        return False
    if source_name and (event.event_metadata or {}).get("sync_source") != source_name:
        return False
    return True


def _resolve_user_id(agent: AgentProfile) -> str:
    """从 agent 解析所属 user_id"""
    return agent.user_id if hasattr(agent, "user_id") and agent.user_id else "default"

def _assert_agent_matches_request(request_agent_id: str, agent: AgentProfile) -> None:
    if request_agent_id and request_agent_id != agent.id:
        raise HTTPException(status_code=403, detail="Agent token does not match request agent_id")


_BOOTSTRAP_PROJECT_PREFIX = "mcp_bootstrap_project:"


def _bootstrap_project_id(agent: AgentProfile) -> Optional[str]:
    """Return the immutable project binding placed on public-bootstrap agents."""
    for constraint in agent.constraints or []:
        if isinstance(constraint, str) and constraint.startswith(_BOOTSTRAP_PROJECT_PREFIX):
            return constraint.removeprefix(_BOOTSTRAP_PROJECT_PREFIX)
    return None


def _bound_project_or_forbidden(agent: AgentProfile, requested_project_id: Optional[str]) -> Optional[str]:
    """Keep public-bootstrap tokens inside their single project boundary."""
    bound_project_id = _bootstrap_project_id(agent)
    if not bound_project_id:
        return requested_project_id
    if requested_project_id and requested_project_id != bound_project_id:
        raise HTTPException(status_code=403, detail="Bootstrap agent is bound to a different project")
    return bound_project_id


def _identifier_problem(value: Optional[str]) -> Optional[str]:
    if value is None or value == "":
        return None
    text = str(value)
    if any(marker in text for marker in MOJIBAKE_MARKERS):
        return "mojibake_marker"
    if "\\" in text or "/" in text:
        return "path_separator_in_identifier"
    if re.search(r"^[A-Za-z]:", text):
        return "windows_drive_path_in_identifier"
    try:
        text.encode("ascii")
    except UnicodeEncodeError:
        return "non_ascii_identifier"
    if len(text) > 160:
        return "identifier_too_long"
    return None


def _memory_sync_item_problem(
    item: AgentMemorySyncItem,
    *,
    source_name: str,
    default_project_id: Optional[str],
) -> Optional[Dict[str, Any]]:
    for field, value in (
        ("source_name", source_name),
        ("default_project_id", default_project_id),
        ("project_id", item.project_id),
        ("repo_id", item.repo_id),
        ("workspace_id", item.workspace_id),
        ("external_id", item.external_id),
    ):
        problem = _identifier_problem(value)
        if problem:
            return {"field": field, "reason": problem, "value": value}
    if not item.content.strip():
        return {"field": "content", "reason": "empty_content"}
    return None


def _coerce_source_type(value: str) -> SourceType:
    mapping = {
        "agent": SourceType.AGENT_API,
        "agent_api": SourceType.AGENT_API,
        "mcp": SourceType.AGENT_API,
        "codex": SourceType.CODEX,
        "openclaw": SourceType.OPENCLAW,
        "chatgpt": SourceType.CHATGPT,
        "obsidian": SourceType.OBSIDIAN,
    }
    return mapping.get((value or "agent").lower(), SourceType.AGENT_API)


def _coerce_sensitivity(value: str) -> SensitivityLevel:
    try:
        return SensitivityLevel(value)
    except ValueError:
        return SensitivityLevel.NORMAL


def _coerce_visibility(value: str) -> VisibilityScope:
    try:
        return VisibilityScope(value)
    except ValueError:
        return VisibilityScope.PROJECT


async def _find_duplicate_agent_event(
    db: AsyncSession,
    *,
    user_id: str,
    agent_id: str,
    content_hash: str,
) -> Optional[RawEvent]:
    result = await db.execute(
        select(RawEvent)
        .where(RawEvent.user_id == user_id)
        .where(RawEvent.agent_id == agent_id)
        .where(RawEvent.content_hash == content_hash)
        .order_by(RawEvent.ingested_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def _find_latest_agent_event_by_external_id(
    db: AsyncSession,
    *,
    user_id: str,
    agent_id: str,
    source_name: str,
    external_id: str,
) -> Optional[RawEvent]:
    result = await db.execute(
        select(RawEvent)
        .where(RawEvent.user_id == user_id)
        .where(RawEvent.agent_id == agent_id)
        .order_by(RawEvent.ingested_at.desc())
        .limit(500)
    )
    for event in result.scalars().all():
        metadata = event.event_metadata or {}
        if (
            metadata.get("sync_source") == source_name
            and metadata.get("external_memory_id") == external_id
        ):
            return event
    return None


async def _create_agent_raw_event(
    db: AsyncSession,
    *,
    agent: AgentProfile,
    user_id: str,
    content: str,
    source_type: str = "agent",
    project_id: Optional[str] = None,
    repo_id: Optional[str] = None,
    workspace_id: Optional[str] = None,
    occurred_at: Optional[datetime] = None,
    sensitivity: str = "normal",
    visibility_scope: str = "project",
    metadata: Optional[Dict[str, Any]] = None,
    dedupe: bool = True,
) -> tuple[RawEvent, bool]:
    content_hash = compute_content_hash(content)
    if dedupe:
        existing = await _find_duplicate_agent_event(
            db,
            user_id=user_id,
            agent_id=agent.id,
            content_hash=content_hash,
        )
        if existing is not None:
            _mark_duplicate_seen(existing, "content_hash_duplicate")
            return existing, False

    event_metadata = dict(metadata or {})
    event_metadata.setdefault("agent_name", agent.agent_name)
    event_metadata.setdefault("agent_type", agent.agent_type.value)
    event_metadata.setdefault("ingest_channel", "agent_api")

    event = (
        await EventIngestionService(db).append(
            user_id=user_id,
            content=content,
            source_type=_coerce_source_type(source_type),
            source_id=agent.id,
            agent_id=agent.id,
            project_id=project_id,
            repo_id=repo_id,
            workspace_id=workspace_id,
            occurred_at=occurred_at,
            event_metadata=event_metadata,
            sensitivity=_coerce_sensitivity(sensitivity),
            visibility_scope=_coerce_visibility(visibility_scope),
        )
    ).event
    return event, True


@router.post("/public-bootstrap", status_code=201)
async def public_mcp_bootstrap(
    request: PublicMcpBootstrapRequest,
    db: AsyncSession = Depends(get_db),
):
    """Create one least-privilege MCP token for a deliberate solo deployment.

    This endpoint never accepts user, read-scope, write-scope, LLM, Graphiti,
    or agent-type configuration from the caller.  It is a convenience for an
    owner-operated instance, not a multi-tenant authentication mechanism.
    """
    if not (settings.SOLO_MODE and settings.PUBLIC_MCP_BOOTSTRAP_ENABLED):
        raise HTTPException(status_code=404, detail="Public MCP bootstrap is disabled")

    owner = await get_current_user(token=None, db=db)
    token = secrets.token_urlsafe(32)
    token_hash = hash_token(token)
    agent = AgentProfile(
        id=generate_id(),
        user_id=owner.id,
        agent_name=request.agent_name.strip(),
        agent_type=AgentType.CUSTOM,
        token_hash=token_hash,
        api_token_hash=token_hash,
        default_recall_level=RecallLevel.TASK_ONLY,
        allowed_read_scopes=[RecallLevel.TASK_ONLY.value],
        allowed_write_scopes=[
            {
                "type": "raw_event_append",
                "enabled": True,
                "description": "External MCP may append RawEvent only; Working Agent governs formal memory.",
                "project_ids": [request.project_id],
            }
        ],
        constraints=[
            f"{_BOOTSTRAP_PROJECT_PREFIX}{request.project_id}",
            "mcp_bootstrap_mode:solo_only",
            "formal_memory_write:working_agent_only",
            "graphiti_access:internal_only",
        ],
        mission="Read task-only context and append RawEvents through the Life Memory MCP contract.",
        role="External MCP event producer",
        schedule_enabled=False,
    )
    db.add(agent)
    await db.commit()

    return {
        "agent_id": agent.id,
        "agent_name": agent.agent_name,
        "project_id": request.project_id,
        "agent_type": agent.agent_type.value,
        "default_recall_level": agent.default_recall_level.value,
        "allowed_operations": ["read_task_context", "append_raw_event"],
        "forbidden_operations": [
            "formal_memory_write",
            "formal_memory_delete",
            "graphiti_write",
            "graphiti_replay",
            "admin_access",
        ],
        "api_token": token,
        "token_message": "Save this token now. It is returned once and is never included in status responses.",
    }


@router.post("/before-start", response_model=AgentBeforeStartResponse)
async def agent_before_start(
    request: AgentBeforeStartRequest,
    db: AsyncSession = Depends(get_db),
    agent: AgentProfile = Depends(get_current_agent),
):
    """
    Agent 在每次任务开始时调用此接口获取思维上下文。

    使用 Retrieval Engine 重建背景，而非简单按 importance 排序。
    支持 4 级 recall：task_only / work_context / personal_context / full_trusted
    """
    try:
        recall_level = RecallLevel(request.recall_level)
    except ValueError:
        recall_level = agent.default_recall_level
    recall_level = _effective_agent_recall_level(recall_level, agent)
    _assert_agent_matches_request(request.agent_id, agent)
    request.project_id = _bound_project_or_forbidden(agent, request.project_id)

    user_id = _resolve_user_id(agent)

    engine = RetrievalEngine(db)
    context = await engine.reconstruct_context(
        user_id=user_id,
        question=request.task,
        project_id=request.project_id,
        recall_level=recall_level.value,
        top_k=request.top_k or 10,
    )

    memories_for_agent = []
    for m in context.get("relevant_memories", []):
        memories_for_agent.append({
            "id": m["memory_id"],
            "uri": m.get("memory_uri"),
            "context_path": m.get("context_path"),
            "title": m["title"],
            "body": m["content"],
            "memory_type": m["memory_type"],
            "memory_layer": m.get("memory_layer"),
            "confidence": m["confidence"],
            "importance": m["importance"],
            "tags": m["tags"],
            "similarity": m.get("similarity", 0.0),
            "final_score": m.get("final_score", 0.0),
            "valid_from": m.get("valid_from"),
            "valid_until": m.get("valid_until"),
        })

    summary_parts = []
    if context.get("decision_history"):
        summary_parts.append(f"发现 {len(context['decision_history'])} 条相关决策历史")
    if context.get("patterns"):
        summary_parts.append(f"识别 {len(context['patterns'])} 个行为模式")
    if context.get("conflicts"):
        summary_parts.append(f"检测到 {len(context['conflicts'])} 处认知冲突")
    if not summary_parts:
        summary_parts.append(f"找到 {len(memories_for_agent)} 条相关记忆")

    summary_text = "；".join(summary_parts)

    return {
        "context_pack": {
            "summary": summary_text,
            "context_summary": context.get("context_summary", ""),
            "memories": memories_for_agent,
            "decision_history": context.get("decision_history", []),
            "patterns": context.get("patterns", []),
            "conflicts": context.get("conflicts", []),
            "entities": context.get("entities", []),
            "context_tiers": context.get("context_tiers", {}),
            "context_tree": context.get("context_tree", {}),
            "memory_layers": context.get("memory_layers", {}),
            "relation_graph": context.get("relation_graph", {}),
            "memory_evolution": context.get("memory_evolution", {}),
            "retrieval_trace": context.get("retrieval_trace", []),
            "memory_protocol": build_agent_memory_protocol(request.task, recall_level.value),
            "constraints": [],
            "source_refs": [
                {
                    "memory_id": m["id"],
                    "memory_uri": m.get("uri"),
                    "context_path": m.get("context_path"),
                    "tags": m["tags"],
                }
                for m in memories_for_agent
            ],
            "meta": context.get("meta", {}),
        }
    }


@router.post("/after-end", response_model=AgentAfterEndResponse)
async def agent_after_end(
    request: AgentAfterEndRequest,
    db: AsyncSession = Depends(get_db),
    agent: AgentProfile = Depends(get_current_agent),
):
    """
    Agent 在每次任务结束时调用，保存本次会话的关键信息。

    工作流程：
    1. 创建 RawEvent（不可变事实）
    2. 异步触发工作 Agent 进行案件归并、证据治理和正式记忆写入
    3. 立即返回，不阻塞 Agent

    后续可通过 /api/memory/context 检索这些记忆。
    """
    user_id = _resolve_user_id(agent)
    _assert_agent_matches_request(request.agent_id, agent)
    request.project_id = _bound_project_or_forbidden(agent, request.project_id)

    decisions_text = ""
    if request.decisions:
        if isinstance(request.decisions, list):
            decisions_text = "\n".join([
                f"- {d.get('content', str(d))}" if isinstance(d, dict) else f"- {d}"
                for d in request.decisions
            ])
        else:
            decisions_text = str(request.decisions)

    actions_text = ""
    if request.actions:
        if isinstance(request.actions, list):
            actions_text = "\n".join([
                f"- {a.get('content', str(a))}" if isinstance(a, dict) else f"- {a}"
                for a in request.actions
            ])
        else:
            actions_text = str(request.actions)

    artifacts_text = ""
    if request.artifacts:
        if isinstance(request.artifacts, list):
            artifacts_text = "\n".join([
                f"- {a.get('name', str(a))}" if isinstance(a, dict) else f"- {a}"
                for a in request.artifacts
            ])
        else:
            artifacts_text = str(request.artifacts)

    content_parts = [f"[{agent.agent_name}] 会话摘要：{request.session_summary}"]
    if decisions_text:
        content_parts.append(f"\n## 决策\n{decisions_text}")
    if actions_text:
        content_parts.append(f"\n## 操作\n{actions_text}")
    if artifacts_text:
        content_parts.append(f"\n## 成果\n{artifacts_text}")
    content = "\n".join(content_parts)

    new_event = (
        await EventIngestionService(db).append(
            user_id=user_id,
            content=content,
            source_type=SourceType.AGENT_API,
            source_id=agent.id,
            agent_id=agent.id,
            project_id=request.project_id,
            repo_id=request.repo_id,
            workspace_id=request.workspace_id,
            event_metadata={
            "agent_name": agent.agent_name,
            "agent_type": agent.agent_type.value,
            "decisions": request.decisions,
            "actions": request.actions,
            "artifacts": request.artifacts,
            "raw_transcript_ref": request.raw_transcript_ref,
            },
            sensitivity=SensitivityLevel.NORMAL,
            visibility_scope=VisibilityScope.PROJECT,
        )
    ).event
    await db.commit()
    trigger_ingested_event(new_event.id)

    return {
        "event_id": new_event.id,
        "formal_memory_count": 0,
        "processing_status": "queued",
        "message": "Event 已记录，工作 Agent 正在异步治理记忆。可通过 /api/memory/context 检索。",
    }

@router.post("/events")
async def agent_ingest_event(
    request: AgentEventIngestRequest,
    db: AsyncSession = Depends(get_db),
    agent: AgentProfile = Depends(get_current_agent),
):
    """Let trusted agents append one raw event through the Agent API.

    This endpoint preserves the CIP boundary: agents create RawEvent entries,
    and the Working Agent later performs autonomous evidence governance.
    """
    _assert_agent_matches_request(request.agent_id, agent)
    user_id = _resolve_user_id(agent)
    request.project_id = _bound_project_or_forbidden(agent, request.project_id)

    event, created = await _create_agent_raw_event(
        db,
        agent=agent,
        user_id=user_id,
        content=request.content,
        source_type=request.source_type,
        project_id=request.project_id,
        repo_id=request.repo_id,
        workspace_id=request.workspace_id,
        occurred_at=request.occurred_at,
        sensitivity=request.sensitivity,
        visibility_scope=request.visibility_scope,
        metadata=request.metadata,
        dedupe=request.dedupe,
    )

    event_id = event.id
    processing_status = event.processing_status.value

    if created:
        await db.commit()
        await db.refresh(event)
        event_id = event.id
        processing_status = event.processing_status.value
        if request.trigger_extraction:
            trigger_ingested_event(event.id)
    else:
        await db.commit()

    return {
        "event_id": event_id,
        "created": created,
        "processing_status": processing_status,
        "message": "Event queued for Memory Agent extraction." if created else "Duplicate event already exists.",
    }


@router.post("/memory-sync")
async def agent_sync_existing_memories(
    request: AgentMemorySyncRequest,
    db: AsyncSession = Depends(get_db),
    agent: AgentProfile = Depends(get_current_agent),
):
    """Import an agent's existing memory store as raw events.

    Use this when first connecting an external agent. Each source memory is
    recorded as a RawEvent, not as a committed memory, so existing review and
    extraction policies still apply. The same endpoint is safe for daily delta
    uploads because content hashes are de-duplicated per agent.
    """
    _assert_agent_matches_request(request.agent_id, agent)
    if len(request.memories) > 100:
        raise HTTPException(status_code=400, detail="At most 100 memories per sync request")

    bound_project_id = _bootstrap_project_id(agent)
    if bound_project_id:
        request.default_project_id = _bound_project_or_forbidden(agent, request.default_project_id)
        for item in request.memories:
            item.project_id = _bound_project_or_forbidden(agent, item.project_id)

    user_id = _resolve_user_id(agent)
    created_events: List[RawEvent] = []
    updated_events: List[RawEvent] = []
    skipped_event_ids: List[str] = []
    skipped_items: List[Dict[str, Any]] = []
    failed_items: List[Dict[str, Any]] = []
    seen_hashes: set[str] = set()

    for index, item in enumerate(request.memories):
        problem = _memory_sync_item_problem(
            item,
            source_name=request.source_name,
            default_project_id=request.default_project_id,
        )
        if problem:
            failed_items.append({
                "index": index,
                "external_id": item.external_id,
                **problem,
            })
            continue

        title = item.title or "Untitled memory"
        memory_type = item.memory_type or "unknown"
        content = (
            "[Imported agent memory]\n"
            f"Source: {request.source_name}\n"
            f"Title: {title}\n"
            f"Type: {memory_type}\n\n"
            f"{item.content}"
        )
        content_hash = compute_content_hash(content)
        if request.dedupe and content_hash in seen_hashes:
            skipped_items.append({
                "index": index,
                "external_id": item.external_id,
                "reason": "duplicate_in_request",
            })
            continue
        seen_hashes.add(content_hash)

        metadata = dict(item.metadata or {})
        metadata.update({
            "sync_source": request.source_name,
            "external_memory_id": item.external_id,
            "memory_title": item.title,
            "memory_type": item.memory_type,
            "source_created_at": item.created_at.isoformat() if item.created_at else None,
            "source_updated_at": item.updated_at.isoformat() if item.updated_at else None,
            "ingest_channel": "agent_memory_sync",
            "sync_operation": "created",
            "last_sync_result": "created",
        })

        existing_by_external_id = None
        if request.dedupe and item.external_id:
            existing_by_external_id = await _find_latest_agent_event_by_external_id(
                db,
                user_id=user_id,
                agent_id=agent.id,
                source_name=request.source_name,
                external_id=item.external_id,
            )

        if existing_by_external_id is not None:
            if existing_by_external_id.content_hash == content_hash:
                _mark_duplicate_seen(existing_by_external_id, "external_id_content_unchanged")
                skipped_event_ids.append(existing_by_external_id.id)
                skipped_items.append({
                    "index": index,
                    "external_id": item.external_id,
                    "event_id": existing_by_external_id.id,
                    "reason": "external_id_content_unchanged",
                })
                continue
            metadata["sync_operation"] = "updated"
            metadata["last_sync_result"] = "updated"
            metadata["previous_event_id"] = existing_by_external_id.id

        event, created = await _create_agent_raw_event(
            db,
            agent=agent,
            user_id=user_id,
            content=content,
            source_type="agent",
            project_id=item.project_id or request.default_project_id,
            repo_id=item.repo_id,
            workspace_id=item.workspace_id,
            occurred_at=item.updated_at or item.created_at,
            metadata=metadata,
            dedupe=False if existing_by_external_id is not None else request.dedupe,
        )
        if created:
            if existing_by_external_id is not None:
                updated_events.append(event)
            else:
                created_events.append(event)
        else:
            skipped_event_ids.append(event.id)
            skipped_items.append({
                "index": index,
                "external_id": item.external_id,
                "event_id": event.id,
                "reason": "content_hash_duplicate",
            })

    new_events = created_events + updated_events
    if new_events or skipped_event_ids:
        await db.commit()
        for event in new_events:
            await db.refresh(event)
        if request.trigger_extraction:
            for event in new_events:
                trigger_ingested_event(event.id)

    extraction_job = {
        "mode": "async_background" if request.trigger_extraction else "not_requested",
        "scheduled_count": len(new_events) if request.trigger_extraction else 0,
        "event_ids": [event.id for event in new_events] if request.trigger_extraction else [],
    }

    return {
        "source_name": request.source_name,
        "accepted_count": len(created_events) + len(updated_events) + len(skipped_event_ids) + len([item for item in skipped_items if item.get("reason") == "duplicate_in_request"]),
        "failed_count": len(failed_items),
        "created_count": len(created_events),
        "updated_count": len(updated_events),
        "skipped_count": len(skipped_event_ids) + len([item for item in skipped_items if item.get("reason") == "duplicate_in_request"]),
        "event_ids": [event.id for event in new_events],
        "created_event_ids": [event.id for event in created_events],
        "updated_event_ids": [event.id for event in updated_events],
        "skipped_event_ids": skipped_event_ids,
        "skipped_items": skipped_items,
        "failed_items": failed_items,
        "client_validation_warnings": request.client_validation_warnings,
        "extraction_job": extraction_job,
        "processing_status": "queued" if new_events else "unchanged",
        "message": "Imported memories were queued as RawEvents." if not failed_items else "Imported valid memories; some items failed validation.",
    }


async def _agent_sync_status_payload(
    request: AgentSyncStatusRequest,
    db: AsyncSession,
    agent: AgentProfile,
) -> Dict[str, Any]:
    _assert_agent_matches_request(request.agent_id, agent)
    request.project_id = _bound_project_or_forbidden(agent, request.project_id)
    user_id = _resolve_user_id(agent)

    result = await db.execute(
        select(RawEvent)
        .where(RawEvent.user_id == user_id)
        .where(RawEvent.agent_id == agent.id)
        .order_by(RawEvent.ingested_at.desc())
        .limit(request.limit)
    )
    events = [
        event for event in result.scalars().all()
        if _event_matches_sync_filters(
            event,
            source_name=request.source_name,
            project_id=request.project_id,
        )
    ]
    event_ids = [event.id for event in events]
    event_id_set = set(event_ids)

    case_counts: Dict[str, int] = {}
    if event_id_set:
        case_result = await db.execute(
            select(MemoryWorkCase.status, MemoryWorkEvidence.raw_event_id)
            .join(MemoryWorkEvidence, MemoryWorkEvidence.case_id == MemoryWorkCase.id)
            .where(
                MemoryWorkCase.user_id == user_id,
                MemoryWorkEvidence.raw_event_id.in_(event_id_set),
            )
        )
        for status, _raw_event_id in case_result.all():
            value = str(status)
            case_counts[value] = case_counts.get(value, 0) + 1

    committed_memory_ids: set[str] = set()
    if event_ids:
        source_result = await db.execute(
            select(MemorySource).where(MemorySource.raw_event_id.in_(event_ids))
        )
        committed_memory_ids = {source.memory_id for source in source_result.scalars().all()}
        if committed_memory_ids:
            active_result = await db.execute(
                select(CommittedMemory.id)
                .where(CommittedMemory.id.in_(committed_memory_ids))
                .where(CommittedMemory.status == CommittedStatus.ACTIVE)
            )
            committed_memory_ids = set(active_result.scalars().all())

    processing_counts: Dict[str, int] = {}
    duplicate_skipped_count = 0
    updated_event_count = 0
    recent_errors = []
    recent_events = []
    for event in events:
        metadata = event.event_metadata or {}
        status = _enum_value(event.processing_status)
        processing_counts[status] = processing_counts.get(status, 0) + 1
        duplicate_skipped_count += int(metadata.get("duplicate_seen_count") or 0)
        if metadata.get("sync_operation") == "updated":
            updated_event_count += 1
        if event.processing_status == ProcessingStatus.FAILED:
            recent_errors.append({
                "event_id": event.id,
                "source_name": metadata.get("sync_source"),
                "message": metadata.get("last_error") or "Memory extraction failed",
                "ingested_at": _iso(event.ingested_at),
            })
        recent_events.append({
            "event_id": event.id,
            "source_name": metadata.get("sync_source"),
            "external_memory_id": metadata.get("external_memory_id"),
            "project_id": event.project_id,
            "processing_status": status,
            "sync_operation": metadata.get("sync_operation"),
            "duplicate_seen_count": int(metadata.get("duplicate_seen_count") or 0),
            "ingested_at": _iso(event.ingested_at),
        })

    return {
        "agent_id": agent.id,
        "agent_name": agent.agent_name,
        "source_name": request.source_name,
        "project_id": request.project_id,
        "raw_event_count": len(events),
        "work_case_count": sum(case_counts.values()),
        "committed_count": len(committed_memory_ids),
        "duplicate_skipped_count": duplicate_skipped_count,
        "updated_event_count": updated_event_count,
        "processing_counts": processing_counts,
        "case_counts": case_counts,
        "recent_errors": recent_errors[:10],
        "recent_events": recent_events[:20],
        "last_sync_at": _iso(events[0].ingested_at) if events else None,
    }


@router.post("/sync-status")
async def agent_sync_status(
    request: AgentSyncStatusRequest,
    db: AsyncSession = Depends(get_db),
    agent: AgentProfile = Depends(get_current_agent),
):
    return await _agent_sync_status_payload(request, db, agent)


@router.get("/sync-status")
async def agent_sync_status_get(
    source_name: Optional[str] = None,
    project_id: Optional[str] = None,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
    agent: AgentProfile = Depends(get_current_agent),
):
    request = AgentSyncStatusRequest(
        agent_id=agent.id,
        source_name=source_name,
        project_id=project_id,
        limit=limit,
    )
    return await _agent_sync_status_payload(request, db, agent)


@router.get("/policy-status")
async def agent_policy_status(
    agent: AgentProfile = Depends(get_current_agent),
):
    policies = _iter_policy_summaries(agent)
    return {
        "agent_id": agent.id,
        "agent_name": agent.agent_name,
        "agent_type": agent.agent_type.value,
        "default_recall_level": agent.default_recall_level.value,
        "last_seen_at": _iso(agent.last_seen_at),
        "autonomous_memory_enabled": True,
        "allowed_write_scopes": policies,
        "security": {
            "token_returned": False,
            "default_without_policy": "working_agent_governance",
            "client_can_bypass_governance": False,
            "working_agent_formal_write": "autonomous_evidence_gated",
        },
    }


@router.post("/test-roundtrip")
async def agent_test_roundtrip(
    request: AgentRoundtripRequest,
    db: AsyncSession = Depends(get_db),
    agent: AgentProfile = Depends(get_current_agent),
):
    _assert_agent_matches_request(request.agent_id, agent)
    request.project_id = _bound_project_or_forbidden(agent, request.project_id)
    agent_id = agent.id
    user_id = _resolve_user_id(agent)
    metadata = dict(request.metadata or {})
    metadata.update({
        "test_case": "memory_test_roundtrip",
        "runner": "agent_memory_bridge",
        "sync_source": request.source_name,
        "ingest_channel": "agent_roundtrip_test",
    })

    event, created = await _create_agent_raw_event(
        db,
        agent=agent,
        user_id=user_id,
        content=request.content,
        source_type="agent",
        project_id=request.project_id,
        metadata=metadata,
        dedupe=True,
    )
    await db.commit()
    await db.refresh(event)

    if created:
        await _process_memory_event(event.id)
        await db.refresh(event)

    case_rows = list(
        (
            await db.execute(
                select(MemoryWorkCase)
                .join(MemoryWorkEvidence, MemoryWorkEvidence.case_id == MemoryWorkCase.id)
                .where(
                    MemoryWorkCase.user_id == user_id,
                    MemoryWorkEvidence.raw_event_id == event.id,
                )
            )
        ).scalars()
    )

    source_result = await db.execute(
        select(MemorySource).where(MemorySource.raw_event_id == event.id)
    )
    committed_ids = [source.memory_id for source in source_result.scalars().all()]
    event_payload = {
        "event_id": event.id,
        "created": created,
        "processing_status": _enum_value(event.processing_status),
    }

    search_context = {}
    try:
        engine = RetrievalEngine(db)
        search_context = await engine.reconstruct_context(
            user_id=user_id,
            question=request.content,
            project_id=request.project_id,
            recall_level=_effective_agent_recall_level(
                RecallLevel(request.recall_level)
                if request.recall_level in {level.value for level in RecallLevel}
                else agent.default_recall_level,
                agent,
            ).value,
            top_k=request.top_k,
        )
    except Exception as exc:
        # 安全：仅记录内部日志，不向调用方泄露异常类型与堆栈信息
        logger.error("reconstruct_context failed for user %s: %s", user_id, exc)
        search_context = {
            "error": "retrieval_failed",
        }

    return {
        "agent_id": agent_id,
        "event": event_payload,
        "work_case_count": len(case_rows),
        "work_cases": [
            {"case_id": item.id, "status": item.status, "active_memory_id": item.active_memory_id}
            for item in case_rows
        ],
        "committed_count": len(committed_ids),
        "committed_memory_ids": committed_ids,
        "search": {
            "total_found": (search_context.get("meta") or {}).get("total_found"),
            "relevant_memories": search_context.get("relevant_memories", []),
            "context_tiers": search_context.get("context_tiers", {}),
            "context_tree": search_context.get("context_tree", {}),
            "memory_layers": search_context.get("memory_layers", {}),
            "relation_graph": search_context.get("relation_graph", {}),
            "memory_evolution": search_context.get("memory_evolution", {}),
            "retrieval_trace": search_context.get("retrieval_trace", []),
            "error": search_context.get("error"),
        },
    }


@router.get("/types")
async def list_agent_types(
    agent: AgentProfile = Depends(get_current_agent),
):
    """列出支持的 Agent 类型"""
    return {
        "supported_types": [
            {
                "type": "codex",
                "description": "Codex / GPT 编程 Agent。任务：代码生成、重构、调试",
            },
            {
                "type": "openclaw",
                "description": "OpenClaw / OpenHands 类 Agent。任务：浏览器自动化、文件操作",
            },
            {
                "type": "claude_code",
                "description": "Claude Code CLI。任务：编程 + 多步骤推理",
            },
            {
                "type": "wecom",
                "description": "企业微信 Bot。任务：消息收发、移动端对话",
            },
            {
                "type": "custom",
                "description": "自定义 Agent 类型",
            },
        ],
        "your_type": agent.agent_type.value,
    }


@router.post("/search")
async def agent_search(
    request: AgentBeforeStartRequest,
    db: AsyncSession = Depends(get_db),
    agent: AgentProfile = Depends(get_current_agent),
):
    """
    Agent 在对话中需要查询记忆时调用。
    返回结构化的 context，包含决策历史、模式、冲突、相关记忆。
    """
    user_id = _resolve_user_id(agent)
    _assert_agent_matches_request(request.agent_id, agent)
    request.project_id = _bound_project_or_forbidden(agent, request.project_id)

    try:
        recall_level = RecallLevel(request.recall_level)
    except ValueError:
        recall_level = agent.default_recall_level
    recall_level = _effective_agent_recall_level(recall_level, agent)

    # Prime session: get_current_agent 的 commit 会释放连接, 而 RetrievalEngine
    # 的 _hybrid_search 用 asyncio.gather 并发查询同一 session 会触发
    # InvalidRequestError (provisioning new connection). 先执行一次查询建立连接.
    await db.execute(select(1))

    engine = RetrievalEngine(db)
    context = await engine.reconstruct_context(
        user_id=user_id,
        question=request.task,
        project_id=request.project_id,
        recall_level=recall_level.value,
        top_k=request.top_k or 10,
    )

    return context


# ---------------------------------------------------------------------------
# Agent Management APIs (for frontend)
# ---------------------------------------------------------------------------

def _serialize_agent(agent: AgentProfile) -> AgentResponse:
    """将 AgentProfile 模型序列化为响应格式"""
    return AgentResponse(
        id=agent.id,
        agent_name=agent.agent_name or "",
        agent_type=agent.agent_type.value if agent.agent_type else "custom",
        role=agent.role or "",
        mission=agent.mission or "",
        default_recall_level=agent.default_recall_level.value if agent.default_recall_level else "work_context",
        instructions=agent.instructions or "",
        goals=agent.goals or [],
        constraints=agent.constraints or [],
        status=bool(agent.status),
        llm_provider=agent.llm_provider.value if agent.llm_provider else "deepseek",
        llm_model=agent.llm_model or "deepseek-chat",
        llm_temperature=agent.llm_temperature or 0.7,
        llm_max_tokens=agent.llm_max_tokens or 4096,
        created_at=agent.created_at.isoformat() if agent.created_at else "",
        updated_at="",  # AgentProfile 没有 updated_at 字段
        is_default=getattr(agent, 'is_default', False),
    )


@router.get("/", response_model=AgentListResponse)
async def list_agents(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """获取当前用户的 Agent 列表"""
    result = await db.execute(
        select(AgentProfile).where(
            AgentProfile.user_id == user.id
        ).order_by(AgentProfile.created_at.desc())
    )
    agents = result.scalars().all()
    return AgentListResponse(
        agents=[_serialize_agent(a) for a in agents],
        total=len(agents),
    )


@router.get("/{agent_id}", response_model=AgentResponse)
async def get_agent(
    agent_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """获取 Agent 详情"""
    result = await db.execute(
        select(AgentProfile).where(
            AgentProfile.id == agent_id,
            AgentProfile.user_id == user.id,
        )
    )
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    return _serialize_agent(agent)


@router.post("", response_model=AgentResponse)
async def create_agent(
    request: AgentCreateRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """创建新 Agent"""
    # 生成 API Token
    api_token = secrets.token_urlsafe(32)
    token_hash = hash_token(api_token)
    
    agent_id = generate_id("agent")
    agent = AgentProfile(
        id=agent_id,
        user_id=user.id,
        agent_name=request.agent_name,
        agent_type=AgentType(request.agent_type),
        role=request.role,
        mission=request.mission,
        default_recall_level=RecallLevel(request.default_recall_level),
        instructions=request.instructions,
        goals=request.goals,
        constraints=request.constraints,
        token_hash=token_hash,
        api_token_hash=token_hash,
        status=True,
        llm_provider=LLMProvider(request.llm_provider),
        llm_model=request.llm_model,
        llm_temperature=request.llm_temperature,
        llm_max_tokens=request.llm_max_tokens,
    )
    
    db.add(agent)
    await db.commit()
    await db.refresh(agent)
    
    return _serialize_agent(agent)


@router.put("/{agent_id}", response_model=AgentResponse)
async def update_agent(
    agent_id: str,
    request: AgentUpdateRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """更新 Agent"""
    result = await db.execute(
        select(AgentProfile).where(
            AgentProfile.id == agent_id,
            AgentProfile.user_id == user.id,
        )
    )
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    
    # 更新字段
    if request.agent_name is not None:
        agent.agent_name = request.agent_name
    if request.role is not None:
        agent.role = request.role
    if request.mission is not None:
        agent.mission = request.mission
    if request.default_recall_level is not None:
        agent.default_recall_level = RecallLevel(request.default_recall_level)
    if request.instructions is not None:
        agent.instructions = request.instructions
    if request.goals is not None:
        agent.goals = request.goals
    if request.constraints is not None:
        agent.constraints = request.constraints
    if request.llm_provider is not None:
        agent.llm_provider = LLMProvider(request.llm_provider)
    if request.llm_model is not None:
        agent.llm_model = request.llm_model
    if request.llm_temperature is not None:
        agent.llm_temperature = request.llm_temperature
    if request.llm_max_tokens is not None:
        agent.llm_max_tokens = request.llm_max_tokens
    if request.status is not None:
        agent.status = request.status
    
    # AgentProfile 没有 updated_at 字段，跳过
    
    await db.commit()
    await db.refresh(agent)
    
    return _serialize_agent(agent)


@router.delete("/{agent_id}")
async def delete_agent(
    agent_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """删除 Agent"""
    result = await db.execute(
        select(AgentProfile).where(
            AgentProfile.id == agent_id,
            AgentProfile.user_id == user.id,
        )
    )
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    
    # 检查是否为默认 Agent
    if getattr(agent, 'is_default', False):
        raise HTTPException(status_code=400, detail="Cannot delete default agent")
    
    await db.delete(agent)
    await db.commit()
    
    return {"status": "deleted", "agent_id": agent_id}


@router.post("/{agent_id}/regenerate-token")
async def regenerate_agent_token(
    agent_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """重新生成 Agent Token"""
    result = await db.execute(
        select(AgentProfile).where(
            AgentProfile.id == agent_id,
            AgentProfile.user_id == user.id,
        )
    )
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    
    # 生成新 Token
    new_token = secrets.token_urlsafe(32)
    token_hash = hash_token(new_token)
    agent.token_hash = token_hash
    agent.api_token_hash = token_hash
    
    await db.commit()
    
    return {
        "agent_id": agent_id,
        "api_token": new_token,
        "message": "Token 已重新生成，请保存新 Token"
    }


@router.get("/{agent_id}/prompt")
async def get_agent_prompt(
    agent_id: str,
    prompt_type: str = "system",
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """获取 Agent 提示词"""
    result = await db.execute(
        select(AgentProfile).where(
            AgentProfile.id == agent_id,
            AgentProfile.user_id == user.id,
        )
    )
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    
    # 根据类型生成提示词
    if prompt_type == "system":
        prompt = f"""你是 {agent.agent_name}，{agent.role}。

使命：{agent.mission}

## 核心职责
{agent.instructions}

## 目标
{chr(10).join(f'- {g}' for g in agent.goals) if agent.goals else '无特定目标'}

## 约束
{chr(10).join(f'- {c}' for c in agent.constraints) if agent.constraints else '无特定约束'}

## 记忆召回级别
{agent.default_recall_level.value}
"""
    elif prompt_type == "simple":
        prompt = f"{agent.agent_name} - {agent.role}\n\n{agent.mission}"
    elif prompt_type == "mcp":
        prompt = f"""MCP Server 配置提示：
Agent: {agent.agent_name}
Role: {agent.role}
Mission: {agent.mission}
Recall Level: {agent.default_recall_level.value}
"""
    else:
        raise HTTPException(status_code=400, detail="Invalid prompt_type")
    
    return {
        "agent_id": agent_id,
        "prompt_type": prompt_type,
        "prompt": prompt,
    }
