"""Context Router (Gen 3 / Cognitive OS) — v3.

根据用户消息自动决定:
- intent:  "ask" | "store" | "decide" | "reflect" | "compare" | "review" | "manage_task" | "unknown"
- recall_level:  task_only / work_context / personal_context / full_trusted
- suggested_agent_type: memory_curator / cognitive_advisor / task_assistant / default
- confidence, rationale, meta

v3 新增:
- selected_memories: 预选的记忆列表 (top-K)
- selected_agents: 建议使用的 agent 列表
- tool_permissions: 建议授权的工具
- context_window_budget: token 预算
- execution_strategy: 执行策略
- blocked_info: 被屏蔽的信息类型

策略:
1. 优先 LLM (强 prompt + 严格 JSON 输出, 自动重试一次)
2. 失败时 -> heuristic
3. heuristic 也不命中时 -> (intent="ask", recall_level="work_context", confidence=0.3)
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Dict, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_

from src.shared.llm.providers import get_llm_provider
from src.shared.llm.model_gateway import ModelGateway
from src.execution.models.agent_profile import AgentProfile
from src.execution.prompts.context_router import build_routing_prompt
from src.memory.services.retrieval_engine import RetrievalEngine

logger = logging.getLogger(__name__)

VALID_INTENTS = {
    "ask", "store", "decide", "reflect", "compare", "review", "manage_task", "unknown",
}
VALID_RECALL_LEVELS = {
    "task_only", "work_context", "personal_context", "full_trusted",
}
VALID_AGENT_TYPES = {
    "memory_curator", "cognitive_advisor", "task_assistant", "default",
}

_INTENT_TO_RECALL = {
    "manage_task": "task_only",
    "ask": "work_context",
    "compare": "work_context",
    "decide": "personal_context",
    "review": "personal_context",
    "reflect": "personal_context",
    "store": "full_trusted",
    "unknown": "work_context",
}

_INTENT_TO_AGENT = {
    "ask": "memory_curator",
    "reflect": "memory_curator",
    "decide": "cognitive_advisor",
    "compare": "cognitive_advisor",
    "review": "cognitive_advisor",
    "manage_task": "task_assistant",
    "store": "default",
    "unknown": "default",
}

_RECALL_LEVEL_TOP_K = {
    "task_only": 5,
    "work_context": 10,
    "personal_context": 15,
    "full_trusted": 20,
}

_RECALL_LEVEL_BUDGET = {
    "task_only": 2000,
    "work_context": 4000,
    "personal_context": 6000,
    "full_trusted": 8000,
}

_INTENT_TO_EXECUTION_STRATEGY = {
    "ask": {"mode": "single", "priority_order": ["answer"], "estimated_steps": 1},
    "store": {"mode": "single", "priority_order": ["store"], "estimated_steps": 1},
    "decide": {"mode": "sequential", "priority_order": ["research", "planning", "critic"], "estimated_steps": 3},
    "compare": {"mode": "sequential", "priority_order": ["research", "planning", "critic"], "estimated_steps": 3},
    "reflect": {"mode": "sequential", "priority_order": ["recall", "analysis", "synthesis"], "estimated_steps": 3},
    "review": {"mode": "sequential", "priority_order": ["recall", "analysis", "synthesis"], "estimated_steps": 3},
    "manage_task": {"mode": "parallel", "priority_order": ["task_query", "task_plan"], "estimated_steps": 2},
    "unknown": {"mode": "single", "priority_order": ["answer"], "estimated_steps": 1},
}

_INTENT_TO_TOOL_PERMISSIONS = {
    "manage_task": [{"tool_name": "read_task", "scope": "allow", "reason": "需要查询任务状态"}],
    "store": [{"tool_name": "add_memory", "scope": "allow", "reason": "需要保存新记忆"}],
    "ask": [{"tool_name": "read_memory", "scope": "allow", "reason": "需要读取记忆回答问题"}],
    "decide": [{"tool_name": "read_memory", "scope": "allow", "reason": "需要读取记忆辅助决策"}],
    "compare": [{"tool_name": "read_memory", "scope": "allow", "reason": "需要读取记忆进行比较"}],
    "reflect": [{"tool_name": "read_memory", "scope": "allow", "reason": "需要读取记忆进行反思"}],
    "review": [{"tool_name": "read_memory", "scope": "allow", "reason": "需要读取记忆进行复盘"}],
    "unknown": [{"tool_name": "read_memory", "scope": "allow", "reason": "需要读取记忆"}],
}

# (regex, intent) — 顺序敏感, 先匹配先赢
_HEURISTIC_RULES: List[tuple[re.Pattern, str]] = [
    (re.compile(r"保存|记住|记录|存档|存一下|记一下"), "store"),
    (re.compile(r"我决定了|我决定\b|已经决定|做出决定"), "decide"),
    (re.compile(r"对比|比较|还是|哪个好|两者|vs\.|vs\b"), "compare"),
    (re.compile(r"周报|复盘|总结|本周|这周|回顾|weekly|review"), "review"),
    (re.compile(r"任务|todo|待办|计划|接下来|安排|要去做|我要做"), "manage_task"),
    (re.compile(r"我为什么|为什么我|解释|反思|思考下"), "reflect"),
]


def _coerce_enum(value: str, allowed: set, default: str) -> str:
    if not isinstance(value, str):
        return default
    v = value.strip().lower()
    return v if v in allowed else default


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _heuristic_route(message: str) -> Dict:
    """根据关键字推断 intent / recall / agent_type。"""
    text = (message or "").strip()
    for pattern, intent in _HEURISTIC_RULES:
        if pattern.search(text):
            recall = _INTENT_TO_RECALL[intent]
            return {
                "intent": intent,
                "recall_level": recall,
                "suggested_agent_type": _INTENT_TO_AGENT[intent],
                "confidence": 0.55,
                "rationale": f"heuristic matched keyword for intent={intent}",
                "execution_strategy": _INTENT_TO_EXECUTION_STRATEGY[intent],
                "blocked_info": ["personal_info", "private_decisions"] if recall == "task_only" else [],
                "meta": {"decided_at": _now_iso(), "model": "heuristic"},
            }
    recall = "work_context"
    return {
        "intent": "ask",
        "recall_level": recall,
        "suggested_agent_type": "memory_curator",
        "confidence": 0.3,
        "rationale": "heuristic default fallback (no keyword match)",
        "execution_strategy": _INTENT_TO_EXECUTION_STRATEGY["ask"],
        "blocked_info": [],
        "meta": {"decided_at": _now_iso(), "model": "heuristic"},
    }


def _build_router_prompt(
    message: str,
    recent_history: Optional[List[Dict]],
    *,
    persona: Optional[Dict] = None,
    memory_summary: Optional[List[str]] = None,
    task_context: Optional[List[Dict]] = None,
) -> str:
    history_block = ""
    if recent_history:
        snippet = recent_history[-6:]
        lines = []
        for h in snippet:
            role = h.get("role", "user")
            content = (h.get("content") or "").replace("\n", " ")[:200]
            lines.append(f"- {role}: {content}")
        history_block = "\n".join(lines)
    if not history_block:
        history_block = "（无最近对话历史）"

    persona_block = ""
    if persona:
        persona_block = json.dumps(persona, ensure_ascii=False)[:500]

    memory_summary_block = ""
    if memory_summary:
        memory_summary_block = "\n".join(f"- {s}" for s in memory_summary[:10])

    task_context_block = ""
    if task_context:
        task_lines = []
        for t in task_context[:5]:
            task_lines.append(
                f"- [{t.get('status', '?')}] {t.get('title', '?')} (优先级: {t.get('priority', '?')})"
            )
        task_context_block = "\n".join(task_lines)

    return build_routing_prompt(
        message,
        history_block,
        persona_block=persona_block,
        memory_summary_block=memory_summary_block,
        task_context_block=task_context_block,
    )


def _parse_llm_json(raw: str) -> Optional[Dict]:
    from src.shared.utils.llm_output import extract_json_object
    return extract_json_object(raw)


class ContextRouter:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def route(
        self,
        user_id: str,
        message: str,
        *,
        recent_history: Optional[List[Dict]] = None,
        persona: Optional[Dict] = None,
        memory_summary: Optional[List[str]] = None,
        task_context: Optional[List[Dict]] = None,
    ) -> Dict:
        """综合 LLM + heuristic 决策, 返回结构化路由结果 (v3)。"""
        llm_result = await self._try_llm_route(
            message,
            recent_history,
            persona=persona,
            memory_summary=memory_summary,
            task_context=task_context,
        )

        if llm_result is not None:
            intent = llm_result["intent"]
            recall_level = llm_result["recall_level"]
            base = {
                "user_id": user_id,
                "message": message,
                "intent": intent,
                "recall_level": recall_level,
                "suggested_agent_type": llm_result["suggested_agent_type"],
                "confidence": float(llm_result.get("confidence") or 0.7),
                "rationale": llm_result.get("rationale") or "llm routed",
                "meta": {
                    "decided_at": _now_iso(),
                    "embed_method": "llm",
                    "model": "llm",
                },
            }
        else:
            h = _heuristic_route(message)
            intent = h["intent"]
            recall_level = h["recall_level"]
            base = {
                "user_id": user_id,
                "message": message,
                **h,
                "meta": {**h["meta"], "embed_method": "heuristic"},
            }

        # --- v3 enrichment ---
        execution_strategy = (
            llm_result.get("execution_strategy") if llm_result else None
        ) or _INTENT_TO_EXECUTION_STRATEGY.get(intent, _INTENT_TO_EXECUTION_STRATEGY["unknown"])

        blocked_info = (
            llm_result.get("blocked_info") if llm_result else None
        )
        if blocked_info is None:
            blocked_info = ["personal_info", "private_decisions"] if recall_level == "task_only" else []

        base["execution_strategy"] = execution_strategy
        base["blocked_info"] = blocked_info
        base["context_window_budget"] = _RECALL_LEVEL_BUDGET.get(recall_level, 4000)

        # 1. selected_memories: 调 RetrievalEngine 预检索 top-K
        top_k = _RECALL_LEVEL_TOP_K.get(recall_level, 10)
        base["selected_memories"] = await self._select_memories(
            user_id, message, recall_level, top_k
        )

        # 2. selected_agents: 查询 AgentProfile
        base["selected_agents"] = await self._select_agents(user_id, intent)

        # 3. tool_permissions
        base["tool_permissions"] = _INTENT_TO_TOOL_PERMISSIONS.get(
            intent, _INTENT_TO_TOOL_PERMISSIONS["unknown"]
        )

        return base

    async def _select_memories(
        self,
        user_id: str,
        message: str,
        recall_level: str,
        top_k: int,
    ) -> List[Dict]:
        """调 RetrievalEngine.reconstruct_context 预检索 top-K 记忆。"""
        try:
            engine = RetrievalEngine(self.db)
            context = await engine.reconstruct_context(
                user_id=user_id,
                question=message,
                recall_level=recall_level,
                top_k=top_k,
            )
            results = []
            for m in context.get("relevant_memories", [])[:top_k]:
                # 生成 relevance_reason
                sim = m.get("similarity", 0.0)
                mtype = m.get("memory_type", "")
                importance = m.get("importance", 0.0)
                reason_parts = []
                if sim > 0.5:
                    reason_parts.append("语义高度相关")
                elif sim > 0.2:
                    reason_parts.append("语义相关")
                if importance >= 0.8:
                    reason_parts.append("高重要性记忆")
                if mtype in ("DECISION", "CORRECTION"):
                    reason_parts.append(f"类型为{mtype}")
                if not reason_parts:
                    reason_parts.append("检索引擎匹配")
                results.append({
                    "memory_id": m.get("memory_id", ""),
                    "title": m.get("title", ""),
                    "importance": float(importance),
                    "relevance_reason": "; ".join(reason_parts),
                })
            return results
        except Exception as e:
            logger.warning("_select_memories failed: %s", e)
            return []

    async def _select_agents(
        self, user_id: str, intent: str
    ) -> List[Dict]:
        """查询 AgentProfile, 根据 intent 匹配建议 agent。"""
        try:
            result = await self.db.execute(
                select(AgentProfile).where(
                    and_(
                        AgentProfile.user_id == user_id,
                        AgentProfile.status == True,  # noqa: E712
                    )
                )
            )
            agents = list(result.scalars().all())
            if not agents:
                return []

            # 根据 intent 决定优先 agent_type
            preferred_types = {
                "ask": {"custom", "advisor"},
                "reflect": {"custom", "advisor"},
                "decide": {"advisor", "custom"},
                "compare": {"advisor", "custom"},
                "review": {"custom", "advisor"},
                "manage_task": {"custom", "codex", "openclaw", "claude_code"},
                "store": {"custom", "advisor"},
                "unknown": {"custom", "advisor"},
            }
            preferred = preferred_types.get(intent, {"custom", "advisor"})

            selected: List[Dict] = []
            # 优先匹配 preferred types
            for agent in agents:
                agent_type_val = (
                    agent.agent_type.value
                    if hasattr(agent.agent_type, "value")
                    else str(agent.agent_type)
                )
                if agent_type_val in preferred:
                    selected.append({
                        "agent_id": agent.id,
                        "agent_name": agent.agent_name,
                        "agent_role": agent.role or agent_type_val,
                        "reason": f"intent={intent}, agent_type={agent_type_val} 匹配",
                    })
                    if len(selected) >= 3:
                        break

            # 不够则补上第一个可用 agent
            if not selected and agents:
                agent = agents[0]
                selected.append({
                    "agent_id": agent.id,
                    "agent_name": agent.agent_name,
                    "agent_role": agent.role or "assistant",
                    "reason": "默认可用 agent",
                })

            return selected
        except Exception as e:
            logger.warning("_select_agents failed: %s", e)
            return []

    async def _try_llm_route(
        self,
        message: str,
        recent_history: Optional[List[Dict]],
        *,
        persona: Optional[Dict] = None,
        memory_summary: Optional[List[str]] = None,
        task_context: Optional[List[Dict]] = None,
    ) -> Optional[Dict]:
        """调 LLM 路由; 失败/超时/解析失败时返回 None -> 走 heuristic。"""
        prompt = _build_router_prompt(
            message,
            recent_history,
            persona=persona,
            memory_summary=memory_summary,
            task_context=task_context,
        )
        try:
            provider = get_llm_provider()
        except Exception as e:
            logger.warning("get_llm_provider failed: %s", e)
            return None

        try:
            raw = await ModelGateway(provider).generate_text(prompt, temperature=0.1, max_tokens=600, prompt_id="context-router", prompt_version="v1")
        except Exception as e:
            logger.warning("llm generate failed: %s", e)
            return None

        data = _parse_llm_json(raw if isinstance(raw, str) else str(raw))
        if not isinstance(data, dict):
            return None

        intent = _coerce_enum(data.get("intent", ""), VALID_INTENTS, "ask")
        recall = _coerce_enum(
            data.get("recall_level", ""), VALID_RECALL_LEVELS,
            _INTENT_TO_RECALL[intent],
        )
        agent = _coerce_enum(
            data.get("suggested_agent_type", ""), VALID_AGENT_TYPES,
            _INTENT_TO_AGENT[intent],
        )
        rationale = data.get("rationale")
        if not isinstance(rationale, str) or not rationale.strip():
            rationale = f"llm routed: intent={intent}"

        try:
            confidence = float(data.get("confidence", 0.7))
        except Exception:
            confidence = 0.7
        confidence = max(0.0, min(1.0, confidence))

        # execution_strategy from LLM
        execution_strategy = None
        es_raw = data.get("execution_strategy")
        if isinstance(es_raw, dict):
            mode = es_raw.get("mode", "single")
            if mode not in ("parallel", "sequential", "single"):
                mode = "single"
            execution_strategy = {
                "mode": mode,
                "priority_order": es_raw.get("priority_order") or [],
                "estimated_steps": int(es_raw.get("estimated_steps") or 1),
            }

        # blocked_info from LLM
        blocked_info = None
        bi_raw = data.get("blocked_info")
        if isinstance(bi_raw, list):
            blocked_info = [str(x) for x in bi_raw]

        return {
            "intent": intent,
            "recall_level": recall,
            "suggested_agent_type": agent,
            "confidence": confidence,
            "rationale": rationale,
            "execution_strategy": execution_strategy,
            "blocked_info": blocked_info,
        }
