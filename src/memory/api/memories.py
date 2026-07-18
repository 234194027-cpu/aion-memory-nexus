from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from datetime import datetime, timezone
from typing import List
from src.shared.config import settings
from src.shared.db.database import get_db, async_session
from src.memory.models.committed_memory import CommittedMemory, CommittedStatus
from src.memory.models.memory_source import MemorySource
from src.memory.schemas.memories import (
    MemorySearchRequest,
    MemorySearchResponse,
    MemoryForgetRequest,
    ContextReconstructionRequest,
    ContextReconstructionResponse,
    MemoryAskRequest,
    MemoryAskResponse,
    MemoryAskMemoryItem,
    MemoryAskSourceRef,
)
from src.shared.security.dependencies import get_current_user
from src.memory.services.retrieval_engine import RetrievalEngine
from src.memory.services.governance_policy import (
    SENSITIVITY_BY_RECALL,
    VISIBILITY_BY_RECALL,
    allowed_read_scope_ceiling,
    normalize_recall_level,
)
from src.execution.prompts.ask import build_chat_system_prompt, build_ask_system_prompt
from src.execution.services.ws_manager import ws_manager
from src.memory.services.deletion_service import (
    delete_from_vector_index,
    rebuild_wiki_derivatives,
    record_lifecycle_audit,
    tombstone_memory,
)
import logging

logger = logging.getLogger(__name__)

router = APIRouter()

@router.post("/search", response_model=MemorySearchResponse)
async def search_memories(
    request: MemorySearchRequest,
    db: AsyncSession = Depends(get_db),
    user = Depends(get_current_user),
):
    query_filters = [CommittedMemory.user_id == user.id, CommittedMemory.status == CommittedStatus.ACTIVE]

    if request.project_id:
        query_filters.append(CommittedMemory.project_id == request.project_id)

    if request.memory_types:
        from src.memory.models.memory_type import MemoryType
        memory_type_enums = []
        for t in request.memory_types:
            try:
                memory_type_enums.append(MemoryType(t))
            except ValueError:
                pass
        if memory_type_enums:
            query_filters.append(CommittedMemory.memory_type.in_(memory_type_enums))

    # 真分页：传了 page 启用真分页；否则保持原 top_k 行为
    use_pagination = request.page is not None
    if use_pagination:
        page = max(request.page, 1)
        page_size = min(max(request.page_size or 20, 1), 100)
        offset = (page - 1) * page_size
        limit_value = page_size
    else:
        page = 1
        page_size = request.page_size or 20
        offset = 0
        limit_value = request.top_k or 10

    # 仅启用真分页时计算 total
    total = None
    if use_pagination:
        count_result = await db.execute(
            select(func.count(CommittedMemory.id)).where(*query_filters)
        )
        total = count_result.scalar_one()

    result = await db.execute(
        select(CommittedMemory)
        .where(*query_filters)
        .order_by(CommittedMemory.importance.desc())
        .offset(offset)
        .limit(limit_value)
    )
    memories = result.scalars().all()

    # 一次性查询所有 sources（避免 N+1）
    memory_ids = [m.id for m in memories]
    all_sources = []
    if memory_ids:
        source_result = await db.execute(
            select(MemorySource).where(MemorySource.memory_id.in_(memory_ids))
        )
        all_sources = source_result.scalars().all()

    # 按 memory_id 分组
    sources_by_memory = {}
    for s in all_sources:
        sources_by_memory.setdefault(s.memory_id, []).append(s)

    memory_list = []
    source_refs = []
    for memory in memories:
        sources = sources_by_memory.get(memory.id, [])
        memory_list.append({
            "id": memory.id,
            "title": memory.title,
            "body": memory.body,
            "memory_type": memory.memory_type.value,
            "confidence": memory.confidence,
            "importance": memory.importance,
            "sensitivity": memory.sensitivity.value,
        })
        for source in sources:
            source_refs.append({
                "memory_id": memory.id,
                "raw_event_id": source.raw_event_id,
                "quote": source.quote,
                "source_type": source.source_type.value if source.source_type else None,
            })
    
    if not memory_list:
        return {
            "answer": "No relevant memories found.",
            "memories": [],
            "source_refs": [],
            "confidence": 0.0,
            "warnings": [],
            "total": total,
            "page": page if use_pagination else None,
            "page_size": page_size if use_pagination else None,
        }

    return {
        "answer": f"Found {len(memory_list)} relevant memories.",
        "memories": memory_list,
        "source_refs": source_refs,
        "confidence": 0.8 if memory_list else 0.0,
        "warnings": [],
        "total": total,
        "page": page if use_pagination else None,
        "page_size": page_size if use_pagination else None,
    }

@router.post("/store-request")
async def store_request(
    request: dict,
    db: AsyncSession = Depends(get_db),
    user = Depends(get_current_user),
):
    from src.memory.api.events import create_event
    from src.memory.schemas.events import EventCreate
    
    event_data = EventCreate(
        source_type="manual",
        content=request.get("content", ""),
        metadata=request.get("metadata", {}),
        sensitivity=request.get("sensitivity", "normal"),
    )
    
    result = await create_event(event_data, db, user)
    return {"message": "Memory store request received", "event_id": result["event_id"]}

@router.post("/chat")
async def chat_with_memory(
    request: dict,
    db: AsyncSession = Depends(get_db),
    user = Depends(get_current_user),
):
    """
    与记忆系统进行对话，基于用户的记忆上下文进行回复
    支持多轮对话历史和记忆引用
    """
    user_message = request.get("message", "")
    agent_id = request.get("agent_id")
    conversation_history = request.get("history", [])
    
    if not user_message:
        raise HTTPException(status_code=400, detail="Message is required")
    
    if not isinstance(conversation_history, list):
        conversation_history = []
    
    # 获取Agent配置
    agent_config = None
    if agent_id:
        from src.execution.models.agent_profile import AgentProfile
        agent_result = await db.execute(
            select(AgentProfile).where(AgentProfile.id == agent_id).where(AgentProfile.user_id == user.id)
        )
        agent_config = agent_result.scalar_one_or_none()
        if not agent_config:
            raise HTTPException(status_code=404, detail="Agent not found")
    
    # 确定召回级别
    recall_level = "work_context"
    if agent_config and agent_config.default_recall_level:
        recall_level = agent_config.default_recall_level.value if hasattr(agent_config.default_recall_level, 'value') else str(agent_config.default_recall_level)
        recall_level = allowed_read_scope_ceiling(
            agent_config.allowed_read_scopes,
            default_recall_level=recall_level,
        )
    recall_level = normalize_recall_level(recall_level)
    
    # 获取用户记忆上下文
    query_filters = [
        CommittedMemory.user_id == user.id,
        CommittedMemory.status == CommittedStatus.ACTIVE
    ]
    
    from src.memory.models.memory_type import MemoryType

    # Keep the existing chat type selection stable; the added visibility filter
    # below closes the privacy gap without widening the chat context.
    if recall_level == "task_only":
        query_filters.append(CommittedMemory.memory_type.in_([MemoryType.TASK, MemoryType.FACT]))
    elif recall_level == "work_context":
        query_filters.append(CommittedMemory.memory_type.in_([
            MemoryType.TASK, MemoryType.FACT, MemoryType.PROJECT_CONTEXT, MemoryType.DECISION
        ]))
    allowed_sensitivities = SENSITIVITY_BY_RECALL.get(
        recall_level,
        SENSITIVITY_BY_RECALL["work_context"],
    )
    query_filters.append(CommittedMemory.sensitivity.in_(allowed_sensitivities))
    query_filters.append(CommittedMemory.visibility_scope.in_(VISIBILITY_BY_RECALL[recall_level]))
    
    result = await db.execute(
        select(CommittedMemory)
        .where(*query_filters)
        .order_by(CommittedMemory.importance.desc())
        .limit(20)
    )
    memories = result.scalars().all()
    
    # 构建记忆上下文
    memory_context = []
    memory_refs = []
    for idx, memory in enumerate(memories):
        memory_context.append({
            "id": memory.id,
            "title": memory.title,
            "body": memory.body[:300] + "..." if len(memory.body) > 300 else memory.body,
            "type": memory.memory_type.value,
            "importance": memory.importance,
            "confidence": memory.confidence,
        })
        memory_refs.append({
            "id": memory.id,
            "title": memory.title,
            "type": memory.memory_type.value,
            "importance": memory.importance,
            "sensitivity": memory.sensitivity.value,
        })
    
    # 构建系统提示词
    agent_name = agent_config.agent_name if agent_config else "记忆助手"
    agent_role = agent_config.role if agent_config and agent_config.role else "人生记忆管家"
    agent_mission = agent_config.mission if agent_config and agent_config.mission else ""
    agent_goals = agent_config.goals if agent_config and agent_config.goals else []
    agent_constraints = agent_config.constraints if agent_config and agent_config.constraints else []
    
    system_prompt = build_chat_system_prompt(
        agent_name=agent_name,
        agent_role=agent_role,
        agent_mission=agent_mission if agent_mission else '帮助用户管理和利用个人记忆，提供个性化的智能对话服务。',
        goals_text=chr(10).join([f'{i+1}. {goal}' for i, goal in enumerate(agent_goals)]) if agent_goals else '1. 基于用户的记忆上下文提供个性化回复\n2. 帮助用户回忆重要信息和事件\n3. 保护用户隐私，谨慎处理敏感记忆',
        constraints_text=chr(10).join([f'- {c}' for c in agent_constraints]) if agent_constraints else '- 保护用户隐私，不泄露敏感记忆内容- 基于记忆上下文回答，不确定时坦诚说明- 以友好、专业的方式与用户交流',
    )

    # 添加记忆上下文
    if memory_context:
        memory_items = []
        for idx, m in enumerate(memory_context[:15]):
            memory_items.append(f"[{idx+1}] ({m['type']}) {m['title']}: {m['body']}")
        memory_summary = "\n".join(memory_items)
        system_prompt += f"\n\n---\n用户记忆库（按重要性排序）：\n{memory_summary}\n---"
    
    # 构建对话历史
    history_prompt = ""
    if conversation_history:
        history_parts = []
        for msg in conversation_history[-10:]:
            role = msg.get("role", "")
            content = msg.get("content", "")
            if role == "user":
                history_parts.append(f"用户: {content}")
            elif role == "assistant":
                history_parts.append(f"助手: {content}")
        if history_parts:
            history_prompt = "\n\n对话历史：\n" + "\n".join(history_parts)
    
    # 调用LLM生成回复
    # WP-0A-T04: 通过 MemoryAnswerService 调用 LLM，避免 API 层直接依赖
    # src.shared.llm.providers（满足 .importlinter 契约 api_no_direct_llm）。
    # 该服务是当前唯一的问答模型调用入口；回滚使用提交/Feature Flag 级
    # 路由切换，不保留一个看似可关闭但实际未生效的配置项。

    # Prompt injection defense: isolate user message
    sanitized_message = user_message.replace("Ignore previous instructions", "[FILTERED]").replace("ignore previous instructions", "[FILTERED]").replace("IGNORE PREVIOUS INSTRUCTIONS", "[FILTERED]")
    sanitized_message = sanitized_message.replace("system prompt", "[FILTERED]").replace("System prompt", "[FILTERED]")

    full_prompt = f"{system_prompt}{history_prompt}\n\n用户: {sanitized_message}\n助手:"

    from src.memory.services.memory_answer_service import MemoryAnswerService
    _answer_service = MemoryAnswerService(db=db)
    _answer_result = await _answer_service.answer_question(
        prompt=full_prompt,
        agent_id=agent_id,
        agent_config=agent_config,
    )
    response = _answer_result["answer"]
    if _answer_result["error"] is not None:
        # 服务层已记录错误日志；这里清空 memory_refs 以匹配旧失败路径行为
        memory_refs = []
    
    # 从回复中提取记忆引用
    import re
    referenced_ids = []
    ref_pattern = re.compile(r'\[记忆[：:]([^\]]+)\]')
    matches = ref_pattern.findall(response)
    if matches:
        for title in matches:
            for mem in memory_refs:
                if title in mem["title"] or mem["title"] in title:
                    if mem["id"] not in referenced_ids:
                        referenced_ids.append(mem["id"])
    
    # 清理回复中的引用标记，保留纯文本
    clean_response = ref_pattern.sub('', response).strip()
    
    return {
        "response": clean_response,
        "raw_response": response,
        "memories_used": len(memory_context),
        "memory_references": [m for m in memory_refs if m["id"] in referenced_ids],
        "all_memories": memory_refs[:10],
        "agent_id": agent_id,
        "agent_name": agent_name,
    }


@router.post("/context", response_model=ContextReconstructionResponse)
async def reconstruct_context(
    request: ContextReconstructionRequest,
    db: AsyncSession = Depends(get_db),
    user = Depends(get_current_user),
):
    """
    Cognitive Context Reconstruction (第二代 Retrieval Engine 接口)

    根据用户问题，从历史记忆中重建"思维背景"，而非直接回答。
    不返回 answer 字段，只返回结构化 context。
    """
    engine = RetrievalEngine(db)
    context = await engine.reconstruct_context(
        user_id=user.id,
        question=request.question,
        project_id=request.project_id,
        recall_level=request.recall_level or "work_context",
        top_k=request.top_k or 20,
    )
    return context


@router.post("/ask", response_model=MemoryAskResponse)
async def ask_memory(
    request: MemoryAskRequest,
    db: AsyncSession = Depends(get_db),
    user = Depends(get_current_user),
):
    """
    Memory Ask API — 基于记忆库直接回答用户问题（Gen 1 完整交付端点）。

    流程：
      1) 通过 RetrievalEngine.reconstruct_context() 重建相关上下文；
      2) 使用 agent（可选）或个人默认 LLM 生成精确引用记忆的答案；
      3) 解析 [记忆:id] 引用，构建 source_refs；
      4) 任何 LLM 失败都降级返回 context，永远不崩。
    """
    import re
    import time
    from src.execution.models.agent_profile import AgentProfile

    started_at = time.perf_counter()
    now_iso = datetime.now(timezone.utc).isoformat()

    if not request.question or not request.question.strip():
        raise HTTPException(status_code=400, detail="question is required")

    valid_recall_levels = {"task_only", "work_context", "personal_context", "full_trusted"}
    recall_level = request.recall_level or "work_context"
    if recall_level not in valid_recall_levels:
        raise HTTPException(
            status_code=400,
            detail=f"invalid recall_level: {recall_level}; must be one of {sorted(valid_recall_levels)}",
        )

    top_k = request.top_k if request.top_k and request.top_k > 0 else 20

    engine = RetrievalEngine(db)
    context = await engine.reconstruct_context(
        user_id=user.id,
        question=request.question,
        project_id=request.project_id,
        recall_level=recall_level,
        top_k=top_k,
    )

    relevant_memories = context.get("relevant_memories", []) or []
    context_summary = context.get("context_summary", "")
    decision_history = context.get("decision_history", []) or []
    patterns = context.get("patterns", []) or []
    conflicts = context.get("conflicts", []) or []
    meta_in = context.get("meta", {}) or {}
    embed_method = meta_in.get("embed_method", "keyword")

    memories_payload: List[MemoryAskMemoryItem] = []
    for m in relevant_memories:
        try:
            memories_payload.append(MemoryAskMemoryItem(
                id=str(m.get("memory_id", "")),
                title=str(m.get("title", "")),
                body=str(m.get("content", "")),
                memory_type=str(m.get("memory_type", "")),
                confidence=float(m.get("confidence", 0.0) or 0.0),
                importance=float(m.get("importance", 0.0) or 0.0),
                similarity=(float(m["similarity"]) if m.get("similarity") is not None else None),
                tags=list(m.get("tags") or []),
                epistemic_status=m.get("epistemic_status"),
                valid_from=m.get("valid_from"),
                valid_until=m.get("valid_until"),
            ))
        except Exception as e:
            logger.warning(f"Memory item parse failed: {e}")
            continue

    warnings: List[str] = []
    if len(relevant_memories) == 0:
        warnings.append("no_relevant_memory")

    agent_config = None
    if request.agent_id:
        agent_result = await db.execute(
            select(AgentProfile).where(
                AgentProfile.id == request.agent_id,
                AgentProfile.user_id == user.id,
            )
        )
        agent_config = agent_result.scalar_one_or_none()
        if agent_config is None:
            raise HTTPException(status_code=404, detail="Agent not found or not owned by current user")

    agent_name = agent_config.agent_name if agent_config and agent_config.agent_name else "记忆助手"
    agent_role = agent_config.role if agent_config and agent_config.role else "人生记忆管家"
    agent_mission = (
        agent_config.mission
        if agent_config and agent_config.mission
        else "帮助用户基于个人记忆库回答问题、提供可引用的真实依据。"
    )
    agent_goals = (
        agent_config.goals
        if agent_config and agent_config.goals
        else [
            "基于用户记忆库给出准确、可引用的回答",
            "对每个非空泛事实使用 [记忆:id] 标注",
            "对未知或缺失信息坦诚说明",
        ]
    )
    agent_constraints = (
        agent_config.constraints
        if agent_config and agent_config.constraints
        else [
            "不编造记忆库中不存在的内容",
            "不泄露超过当前 recall_level 允许的敏感级别记忆",
            "引用必须使用记忆 id 精确标注",
        ]
    )

    goals_text = "\n".join([f"{i+1}. {g}" for i, g in enumerate(agent_goals)]) if agent_goals else "1. 提供准确、基于记忆的回答"
    constraints_text = "\n".join([f"- {c}" for c in agent_constraints]) if agent_constraints else "- 保护用户隐私，不臆造内容"

    if relevant_memories:
        memory_lines = []
        for idx, m in enumerate(memories_payload):
            body_excerpt = m.body[:300] + ("..." if len(m.body) > 300 else "")
            memory_lines.append(
                f"[{idx+1}] (类型={m.memory_type}, 重要性={m.importance:.2f}, 置信={m.confidence:.2f}, "
                f"认识状态={m.epistemic_status or 'legacy_unclassified'}, 有效期={m.valid_from or '未知'} 至 {m.valid_until or '持续'}) "
                f"id={m.id} 标题={m.title} 内容={body_excerpt}"
            )
        memory_block = "\n".join(memory_lines)
    else:
        memory_block = "（用户记忆库中未检索到与该问题相关的条目）"

    decision_lines = []
    for d in decision_history[:4]:
        decision_lines.append(
            f"- {d.get('content','')} (原因: {d.get('reason','')}, 结果: {d.get('outcome','')})"
        )
    decision_text = "\n".join(decision_lines) if decision_lines else "- （无明显决策历史）"

    pattern_text = "\n".join([f"- {p}" for p in patterns[:5]]) if patterns else "- （无明显模式）"
    conflict_text = (
        "\n".join([f"- 当前: {c.get('current','')} | 过去: {c.get('past','')} | 解释: {c.get('explanation','')}"
                   for c in conflicts[:3]])
        if conflicts else "- （无冲突）"
    )

    # Prompt injection defense
    sanitized_question = request.question.replace("Ignore previous instructions", "[FILTERED]").replace("ignore previous instructions", "[FILTERED]").replace("system prompt", "[FILTERED]")

    prompt = build_ask_system_prompt(
        agent_name=agent_name,
        agent_role=agent_role,
        agent_mission=agent_mission,
        goals_text=goals_text,
        constraints_text=constraints_text,
        memory_block=memory_block,
        context_summary=context_summary,
        decision_text=decision_text,
        pattern_text=pattern_text,
        conflict_text=conflict_text,
        question=sanitized_question,
    )

    answer_text: str
    llm_failed = False
    # WP-0A-T04: 通过 MemoryAnswerService 调用 LLM（满足 .importlinter 契约）
    from src.memory.services.memory_answer_service import MemoryAnswerService
    _ask_service = MemoryAnswerService(db=db)
    _ask_result = await _ask_service.answer_question(
        prompt=prompt,
        agent_id=request.agent_id,
        agent_config=agent_config,
        temperature=float(request.temperature) if request.temperature is not None else 0.4,
        max_tokens=int(request.max_tokens) if request.max_tokens else 1500,
    )
    answer_text = _ask_result["answer"]
    if _ask_result["error"] is not None:
        llm_failed = True
        answer_text = "抱歉，记忆检索已完成，但答案生成暂时不可用。"
        warnings.append("llm_generation_failed")

    citation_pattern = re.compile(r"\[记忆[：:]([^\]]+)\]")
    cited_ids: List[str] = []
    seen = set()
    for raw in citation_pattern.findall(answer_text or ""):
        mem_id = raw.strip()
        if not mem_id:
            continue
        if mem_id not in seen:
            seen.add(mem_id)
            cited_ids.append(mem_id)

    id_to_memory_item = {m.id: m for m in memories_payload}
    source_rows = []
    if id_to_memory_item:
        source_result = await db.execute(
            select(MemorySource).where(MemorySource.memory_id.in_(id_to_memory_item))
        )
        source_rows = source_result.scalars().all()
    first_source_by_memory = {}
    for source in source_rows:
        first_source_by_memory.setdefault(source.memory_id, source)

    source_refs: List[MemoryAskSourceRef] = []
    for mid in cited_ids:
        m_item = id_to_memory_item.get(mid)
        if m_item is None:
            warnings.append("invalid_citation")
            continue
        source = first_source_by_memory.get(mid)
        source_refs.append(MemoryAskSourceRef(
            memory_id=mid,
            title=m_item.title,
            quote=source.quote if source else None,
            source_type=(
                source.source_type.value
                if source and source.source_type
                else None
            ),
        ))

    if relevant_memories and not cited_ids and not llm_failed:
        warnings.append("no_citation")

    confidence = 0.5
    confidence += min(len(relevant_memories) * 0.05, 0.3)
    if embed_method == "semantic":
        confidence += 0.1
    if llm_failed:
        confidence = min(confidence, 0.3)
    if len(relevant_memories) == 0:
        confidence = 0.1
    confidence = max(0.0, min(1.0, confidence))

    if len(relevant_memories) == 0:
        answer_text = "未找到与该问题相关的记忆。"

    elapsed_ms = int((time.perf_counter() - started_at) * 1000)
    meta_out = {
        "total_found": len(relevant_memories),
        "embed_method": embed_method,
        "recall_level": recall_level,
        "asked_at": now_iso,
        "latency_ms": elapsed_ms,
        "agent_id": request.agent_id,
        "agent_name": agent_name,
        "provider_used": _ask_result["provider_used"],
    }

    return MemoryAskResponse(
        answer=answer_text,
        confidence=confidence,
        memories=memories_payload,
        source_refs=source_refs,
        context_summary=context_summary,
        warnings=warnings,
        meta=meta_out,
    )


@router.get("/{memory_id}")
async def get_memory(
    memory_id: str,
    db: AsyncSession = Depends(get_db),
    user = Depends(get_current_user),
):
    result = await db.execute(
        select(CommittedMemory).where(CommittedMemory.id == memory_id)
    )
    memory = result.scalar_one_or_none()
    if not memory:
        raise HTTPException(status_code=404, detail="Memory not found")
    if memory.user_id != user.id:
        raise HTTPException(status_code=403, detail="Not authorized")
    
    source_result = await db.execute(
        select(MemorySource).where(MemorySource.memory_id == memory.id)
    )
    sources = source_result.scalars().all()
    
    return {
        "id": memory.id,
        "source_work_case_id": memory.source_work_case_id,
        "source_work_decision_id": memory.source_work_decision_id,
        "origin_kind": memory.origin_kind,
        "revision": memory.revision,
        "user_id": memory.user_id,
        "memory_type": memory.memory_type.value,
        "title": memory.title,
        "body": memory.body,
        "confidence": memory.confidence,
        "importance": memory.importance,
        "sensitivity": memory.sensitivity.value,
        "epistemic_status": memory.epistemic_status,
        "visibility_scope": memory.visibility_scope.value,
        "status": memory.status.value,
        "valid_from": memory.valid_from,
        "valid_until": memory.valid_until,
        "tags": memory.tags,
        "created_at": memory.created_at,
        "updated_at": memory.updated_at,
        "last_accessed_at": memory.last_accessed_at,
        "sources": [{
            "id": s.id,
            "raw_event_id": s.raw_event_id,
            "quote": s.quote,
            "location": s.location,
            "source_type": s.source_type.value if s.source_type else None,
        } for s in sources],
    }

@router.post("/{memory_id}/forget")
async def forget_memory(
    memory_id: str,
    request: MemoryForgetRequest,
    db: AsyncSession = Depends(get_db),
    user = Depends(get_current_user),
):
    result = await db.execute(
        select(CommittedMemory).where(CommittedMemory.id == memory_id)
    )
    memory = result.scalar_one_or_none()
    if not memory:
        raise HTTPException(status_code=404, detail="Memory not found")
    if memory.user_id != user.id:
        raise HTTPException(status_code=403, detail="Not authorized")

    action_map = {
        "revoke": CommittedStatus.REVOKED,
        "expire": CommittedStatus.EXPIRED,
        "delete": CommittedStatus.DELETED,
        "supersede": CommittedStatus.SUPERSEDED,
    }

    new_status = action_map.get(request.action)
    if not new_status:
        raise HTTPException(status_code=400, detail="Invalid action")

    # A user correction is evidence, not a bypass around Working-Agent
    # governance.  The old memory remains active until the evidence/decision
    # pipeline creates a traceable replacement.
    if request.action == "supersede" and (request.new_title or request.new_body):
        from src.memory.models.raw_event import SourceType
        from src.memory.services.event_ingestion import EventIngestionService, trigger_ingested_event

        corrected_title = request.new_title or memory.title
        corrected_body = request.new_body or memory.body
        ingested = await EventIngestionService(db).append(
            user_id=memory.user_id,
            content=(
                f"用户要求修正正式记忆 {memory.id}。\n"
                f"原题：{memory.title}\n原文：{memory.body}\n"
                f"新题：{corrected_title}\n新文：{corrected_body}"
            ),
            source_type=SourceType.MANUAL,
            source_id=memory.id,
            project_id=memory.project_id,
            repo_id=memory.repo_id,
            workspace_id=memory.workspace_id,
            event_metadata={
                "event_kind": "correction",
                "supersedes_memory_id": memory.id,
                "conflict_memory_ids": [memory.id],
                "requested_memory_type": request.new_memory_type or memory.memory_type.value,
                "user_quote": corrected_body,
            },
            sensitivity=memory.sensitivity,
            visibility_scope=memory.visibility_scope,
        )
        await db.commit()
        trigger_ingested_event(ingested.event.id)

        return {
            "status": "awaiting_governance",
            "memory_id": memory_id,
            "event_id": ingested.event.id,
        }

    previous_status = memory.status
    if request.action == "delete":
        affected_counts = await tombstone_memory(db, memory)
    else:
        memory.status = new_status
        affected_counts = await rebuild_wiki_derivatives(db, memory.user_id)
        from src.memory.services.graph_projection import queue_source_deletion

        await queue_source_deletion(
            db,
            user_id=memory.user_id,
            project_id=memory.project_id,
            source_kind="committed_memory",
            source_id=memory.id,
            source_revision=memory.content_hash or str(memory.revision or 1),
        )
    memory.updated_at = datetime.now(timezone.utc)
    await record_lifecycle_audit(
        db,
        user_id=memory.user_id,
        action=request.action,
        target_type="committed_memory",
        target_id=memory.id,
        affected_counts=affected_counts,
    )
    from src.memory.services.memory_lifecycle import record_memory_state_transition
    await record_memory_state_transition(
        db, user_id=memory.user_id, subject_type="committed_memory", subject_id=memory.id,
        from_state=previous_status, to_state=memory.status, actor_type="user", actor_id=user.id,
        reason=f"user_{request.action}",
    )

    await db.commit()
    from src.execution.services.conversation_memory_projector import (
        try_refresh_conversation_memory_projection,
    )
    await try_refresh_conversation_memory_projection(db, user_id=memory.user_id)
    await db.refresh(memory)
    if request.action == "delete":
        delete_from_vector_index(memory.id)

    return {"status": request.action, "memory_id": memory_id}

@router.websocket("/ws/chat/{user_id}")
async def websocket_chat(websocket: WebSocket, user_id: str):
    """WebSocket 实时对话（token 级 streaming）。需要 JWT 鉴权。"""
    # Authenticate via query parameter or subprotocol
    token = websocket.query_params.get("token")
    if not token:
        # Try subprotocol
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
            recall_level = data.get("recall_level", "work_context")
            agent_id = data.get("agent_id")

            if not question:
                await ws_manager.send_error(user_id, "question is required")
                continue

            try:
                async with async_session() as db:
                    engine = RetrievalEngine(db)
                    ctx = await engine.reconstruct_context(
                        user_id=user_id,
                        question=question,
                        recall_level=recall_level,
                    )

                    memories = ctx.get("relevant_memories", [])
                    memories_text = "\n".join(
                        f"[{i+1}] {m.get('title','')}: {m.get('body', m.get('content',''))[:100]}"
                        for i, m in enumerate(memories[:10])
                    )
                    prompt = f"基于以下记忆回答问题：\n{memories_text}\n\n问题：{question}"

                    # WP-0A-T04: 通过 MemoryAnswerService 流式调用 LLM
                    # （满足 .importlinter 契约 api_no_direct_llm）
                    from src.memory.services.memory_answer_service import MemoryAnswerService
                    _answer_service = MemoryAnswerService(db=db)
                    async for chunk in _answer_service.answer_question_stream(
                        prompt=prompt,
                        agent_id=agent_id,
                    ):
                        await ws_manager.send_token(user_id, chunk)

                    await ws_manager.send_done(user_id, result={"memories": memories})

            except Exception as e:
                import logging
                logging.getLogger(__name__).warning(f"WebSocket chat error: {e}")
                await ws_manager.send_error(user_id, "stream_failed")

    except WebSocketDisconnect:
        ws_manager.disconnect(user_id)
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"WebSocket connection error: {e}")
        ws_manager.disconnect(user_id)
