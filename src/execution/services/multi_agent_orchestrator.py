"""Multi-Agent Orchestrator (Gen 3 / v3).

从"通用并行 agent → 综合"升级为"4 种专业角色 + 串行/并行 + 输出回写"。

- execution_mode="parallel":  保留原有逻辑（多个 agent_profile 并行 draft → 综合）。
- execution_mode="sequential": 4 种内置角色串行执行（Research → Planning → Critic → Executor）。
- writeback_to_memory=True:   将 agent 输出回写 Memory Core / Task System。
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from src.shared.llm.providers import get_llm_provider
from src.shared.llm.model_gateway import ModelGateway
from src.execution.models.agent_profile import AgentProfile
from src.memory.models.raw_event import ProcessingStatus, RawEvent, SensitivityLevel, SourceType, VisibilityScope
from src.memory.services.retrieval_engine import RetrievalEngine
from src.execution.services.task_system import TaskSystem
from src.shared.ids.id_generator import generate_event_id
from src.shared.utils.hash import compute_content_hash
from src.execution.prompts.multi_agent import (
    build_agent_prompt,
    build_synthesize_prompt,
    build_research_prompt,
    build_planning_prompt,
    build_critic_prompt,
    build_executor_prompt,
)

logger = logging.getLogger(__name__)

DEFAULT_ROLES_ORDER = ["research", "planning", "critic", "executor"]


class MultiAgentOrchestrator:
    def __init__(self, db: AsyncSession):
        self.db = db

    # ------------------------------------------------------------------
    # run() — v3 入口
    # ------------------------------------------------------------------

    async def run(
        self,
        user_id: str,
        question: str,
        *,
        agent_ids: Optional[List[str]] = None,
        max_agents: int = 3,
        recall_level: str = "work_context",
        execution_mode: str = "parallel",
        roles_to_activate: Optional[List[str]] = None,
        writeback_to_memory: bool = True,
    ) -> Dict:
        started_at = time.perf_counter()
        warnings: List[str] = []

        # ---------- 召回上下文（两种模式共用） ----------
        try:
            retrieval = RetrievalEngine(self.db)
            context = await retrieval.reconstruct_context(
                user_id=user_id,
                question=question,
                recall_level=recall_level,
                top_k=10,
            )
        except Exception:
            context = {
                "context_summary": "",
                "relevant_memories": [],
                "decision_history": [],
                "patterns": [],
                "conflicts": [],
            }

        # ---------- 根据 execution_mode 分发 ----------
        if execution_mode == "sequential":
            result = await self._run_sequential(
                user_id=user_id,
                question=question,
                context=context,
                roles_to_activate=roles_to_activate,
                warnings=warnings,
            )
        else:
            # parallel — 保留原有逻辑
            result = await self._run_parallel(
                user_id=user_id,
                question=question,
                context=context,
                agent_ids=agent_ids,
                max_agents=max_agents,
                recall_level=recall_level,
                warnings=warnings,
            )

        # ---------- 输出回写 ----------
        writeback_results: Dict = {"memories_created": [], "tasks_created": []}
        if writeback_to_memory:
            try:
                memories_created = await self._writeback_to_memory(
                    user_id=user_id,
                    agent_outputs=result.get("role_outputs", {}),
                )
                writeback_results["memories_created"] = memories_created
            except Exception as e:
                logger.warning("writeback_to_memory failed: %s", e)
                warnings.append(f"writeback_memory_error: {e}")

            try:
                tasks_created = await self._writeback_to_tasks(
                    user_id=user_id,
                    executor_output=result.get("role_outputs", {}).get("executor", {}),
                )
                writeback_results["tasks_created"] = tasks_created
            except Exception as e:
                logger.warning("writeback_to_tasks failed: %s", e)
                warnings.append(f"writeback_tasks_error: {e}")

        result["writeback_results"] = writeback_results
        result["execution_mode"] = execution_mode
        result["warnings"] = warnings + result.get("warnings", [])
        result.setdefault("meta", {})["latency_ms"] = _elapsed_ms(started_at)
        return result

    # ------------------------------------------------------------------
    # _run_sequential — 4 角色串行
    # ------------------------------------------------------------------

    async def _run_sequential(
        self,
        *,
        user_id: str,
        question: str,
        context: Dict,
        roles_to_activate: Optional[List[str]],
        warnings: List[str],
    ) -> Dict:
        active_roles = roles_to_activate or DEFAULT_ROLES_ORDER
        role_outputs: Dict[str, dict] = {}
        upstream_output: dict = {}

        # 需要 persona（Critic 需要）
        persona_text = await self._get_persona(user_id)

        for role in active_roles:
            try:
                output = await self._agent_by_role(
                    role=role,
                    question=question,
                    context=context,
                    upstream_output=upstream_output,
                    persona=persona_text,
                    user_id=user_id,
                )
                role_outputs[role] = output
                upstream_output[role] = output
            except Exception as e:
                logger.warning("role %s failed: %s", role, e)
                role_outputs[role] = {"error": str(e)}
                warnings.append(f"role_{role}_error: {e}")

        # 综合最终建议
        final_advice = await self._synthesize_sequential(
            user_id=user_id,
            question=question,
            role_outputs=role_outputs,
            context=context,
        )

        return {
            "user_id": user_id,
            "question": question,
            "drafts": [],
            "final_advice": final_advice,
            "role_outputs": role_outputs,
            "confidence": self._compute_sequential_confidence(role_outputs),
            "warnings": warnings,
            "meta": {
                "active_roles": active_roles,
                "run_at": datetime.now(timezone.utc).isoformat(),
            },
        }

    # ------------------------------------------------------------------
    # _run_parallel — 保留原有逻辑 + role_outputs 扩展
    # ------------------------------------------------------------------

    async def _run_parallel(
        self,
        *,
        user_id: str,
        question: str,
        context: Dict,
        agent_ids: Optional[List[str]],
        max_agents: int,
        recall_level: str,
        warnings: List[str],
    ) -> Dict:
        agents = await self._select_agents(
            user_id=user_id,
            agent_ids=agent_ids,
            max_agents=max(1, int(max_agents)),
        )

        if not agents:
            warnings.append("no_agents_available")
            return {
                "user_id": user_id,
                "question": question,
                "drafts": [],
                "final_advice": "无可用 agent 来回答该问题。",
                "role_outputs": {},
                "confidence": 0.1,
                "warnings": warnings,
                "meta": {"agent_count": 0, "recall_level": recall_level},
            }

        drafts: List[Dict] = []
        for agent in agents:
            try:
                draft_text, conf = await self._agent_draft(
                    user_id=user_id,
                    agent=agent,
                    question=question,
                    context=context,
                )
                drafts.append({
                    "agent_id": agent.id,
                    "agent_name": agent.agent_name,
                    "agent_type": agent.agent_type.value if agent.agent_type else "custom",
                    "draft": draft_text,
                    "confidence": conf,
                    "warnings": [],
                })
            except Exception as e:
                drafts.append({
                    "agent_id": agent.id,
                    "agent_name": agent.agent_name,
                    "agent_type": agent.agent_type.value if agent.agent_type else "custom",
                    "draft": "",
                    "confidence": 0.0,
                    "warnings": [f"subagent_error: {e}"],
                })
                warnings.append(f"subagent_{agent.id}_error: {e}")

        final_advice, synth_confidence, synth_failed = await self._synthesize(
            user_id=user_id,
            question=question,
            drafts=drafts,
        )

        if synth_failed:
            warnings.append("synthesis_fallback")
            final_advice, synth_confidence = self._pick_top_draft(drafts)

        avg_conf = _avg_confidence(drafts)

        return {
            "user_id": user_id,
            "question": question,
            "drafts": drafts,
            "final_advice": final_advice,
            "role_outputs": {},
            "confidence": synth_confidence,
            "warnings": warnings,
            "meta": {
                "agent_count": len(agents),
                "draft_count": len(drafts),
                "recall_level": recall_level,
                "synth_failed": synth_failed,
                "average_draft_confidence": round(avg_conf, 4),
                "run_at": datetime.now(timezone.utc).isoformat(),
            },
        }

    # ------------------------------------------------------------------
    # _agent_by_role — 根据角色执行单个 agent
    # ------------------------------------------------------------------

    async def _agent_by_role(
        self,
        *,
        role: str,
        question: str,
        context: Dict,
        upstream_output: dict = None,
        persona: Optional[str] = None,
        user_id: str = "",
    ) -> dict:
        upstream_output = upstream_output or {}

        memory_block = self._format_memory_block(context)
        decision_block = self._format_decision_block(context)

        if role == "research":
            prompt = build_research_prompt(
                question=question,
                context=context.get("context_summary") or "（无）",
                memories=memory_block,
            )
        elif role == "planning":
            research_str = json.dumps(
                upstream_output.get("research", {}), ensure_ascii=False
            )
            open_decisions_str = decision_block
            prompt = build_planning_prompt(
                question=question,
                research_output=research_str,
                open_decisions=open_decisions_str,
            )
        elif role == "critic":
            plan_str = json.dumps(
                upstream_output.get("planning", {}), ensure_ascii=False
            )
            prompt = build_critic_prompt(
                plan=plan_str,
                decision_history=decision_block,
                persona=persona or "（无人格数据）",
            )
        elif role == "executor":
            final_plan = json.dumps(
                upstream_output.get("planning", {}), ensure_ascii=False
            )
            prompt = build_executor_prompt(
                final_plan=final_plan,
                tool_permissions="read_memory, manage_task, execute_code, read_file",
            )
        else:
            return {"error": f"Unknown role: {role}"}

        provider = get_llm_provider()
        raw_text = await ModelGateway(provider).generate_text(prompt, temperature=0.4, max_tokens=1500, prompt_id=f"orchestration-{role}", prompt_version="v1")
        if not isinstance(raw_text, str):
            raw_text = str(raw_text)
        raw_text = raw_text.strip()

        return self._parse_json_output(raw_text, role)

    def _parse_json_output(self, raw_text: str, role: str) -> dict:
        """尝试从 LLM 输出中提取 JSON。"""
        from src.shared.utils.llm_output import extract_json_object
        result = extract_json_object(raw_text)
        if result is not None:
            return result
        logger.warning("role %s: failed to parse JSON, returning raw text", role)
        return {"raw_output": raw_text}

    # ------------------------------------------------------------------
    # _writeback_to_memory — 将 agent 输出回写 Memory Core
    # ------------------------------------------------------------------

    async def _writeback_to_memory(
        self, user_id: str, agent_outputs: dict
    ) -> list:
        """Route agent output through RawEvent and the Working Agent case ledger.

        - Research findings → FACT
        - Critic risks → INSIGHT
        
        所有代理输出都先作为有来源的 Agent 观点进入 RawEvent，由工作 Agent 自动治理；不得冒充用户事实。
        """
        created_ids: List[str] = []
        now = datetime.now(timezone.utc)

        # Research → FACT
        research = agent_outputs.get("research", {})
        findings = research.get("findings", [])
        if findings:
            findings_text = "\n".join(f"- {f}" for f in findings)
            sources = research.get("sources", [])
            body = f"发现:\n{findings_text}"
            if sources:
                body += f"\n\n来源记忆: {', '.join(sources)}"
            gaps = research.get("gaps", [])
            if gaps:
                body += "\n\n信息缺口:\n" + "\n".join(f"- {g}" for g in gaps)

            from src.memory.services.event_ingestion import EventIngestionService
            event = (
                await EventIngestionService(self.db).append(
                    user_id=user_id,
                    content=body,
                    source_type=SourceType.AGENT_API,
                    source_id="multi_agent_orchestrator:research",
                    occurred_at=now,
                    event_metadata={
                    "agent_output_kind": "research",
                    "question": research.get("question"),
                    "sources": sources,
                    "epistemic_boundary": "agent_assertion",
                    },
                    sensitivity=SensitivityLevel.NORMAL,
                    visibility_scope=VisibilityScope.PROJECT,
                    processing_status=ProcessingStatus.COMPLETED,
                )
            ).event
            from src.execution.runtime.working_coordinator import WorkingCoordinator

            created_ids.extend(
                await WorkingCoordinator(self.db).materialize_preclassified(
                    event=event,
                    proposals=(
                        {
                            "memory_type": "fact",
                            "title": f"Research: {research.get('question', '')[:50] or 'agent_output'}",
                            "content": body,
                            "confidence": 0.6,
                            "importance": 0.5,
                            "sensitivity": "normal",
                            "reason": "由 research agent 生成，作为有来源的 Agent 观点交给工作 Agent 治理",
                            "entities": ["agent_output", "research", "pending_review"],
                        },
                    ),
                    origin="multi_agent_research",
                )
            )

        # Critic → INSIGHT
        critic = agent_outputs.get("critic", {})
        risks = critic.get("risks", [])
        for risk in risks:
            risk_text = risk if isinstance(risk, str) else risk.get("risk", "")
            severity = risk.get("severity", "medium") if isinstance(risk, dict) else "medium"
            suggestion = risk.get("suggestion", "") if isinstance(risk, dict) else ""
            if not risk_text:
                continue

            body = f"风险: {risk_text}\n严重程度: {severity}"
            if suggestion:
                body += f"\n建议: {suggestion}"

            event = (
                await EventIngestionService(self.db).append(
                    user_id=user_id,
                    content=body,
                    source_type=SourceType.AGENT_API,
                    source_id="multi_agent_orchestrator:critic",
                    occurred_at=now,
                    event_metadata={
                    "agent_output_kind": "critic",
                    "severity": severity,
                    "epistemic_boundary": "agent_assertion",
                    },
                    sensitivity=SensitivityLevel.NORMAL,
                    visibility_scope=VisibilityScope.PROJECT,
                    processing_status=ProcessingStatus.COMPLETED,
                )
            ).event
            from src.execution.runtime.working_coordinator import WorkingCoordinator

            created_ids.extend(
                await WorkingCoordinator(self.db).materialize_preclassified(
                    event=event,
                    proposals=(
                        {
                            "memory_type": "insight",
                            "title": f"Risk: {risk_text[:50]}",
                            "content": body,
                            "confidence": 0.5,
                            "importance": 0.6 if severity == "high" else 0.4,
                            "sensitivity": "normal",
                            "reason": f"由 critic agent 识别的风险（严重程度: {severity}），作为 Agent 观点交给工作 Agent 治理",
                            "entities": ["agent_output", "critic", severity, "pending_review"],
                        },
                    ),
                    origin="multi_agent_critic",
                )
            )

        if created_ids:
            await self.db.commit()

        return created_ids

    # ------------------------------------------------------------------
    # _writeback_to_tasks — 将 executor 输出转化为 tasks
    # ------------------------------------------------------------------

    async def _writeback_to_tasks(
        self, user_id: str, executor_output: dict
    ) -> list:
        """将 executor 的 actions_taken 转化为 LifeTask。"""
        created_ids: List[str] = []
        actions = executor_output.get("actions_taken", [])

        if not actions:
            return created_ids

        ts = TaskSystem(self.db)
        for action in actions:
            if isinstance(action, str):
                title = action
                description = ""
            elif isinstance(action, dict):
                status = action.get("status", "")
                if status == "failed":
                    continue
                title = action.get("action", "未命名动作")
                description = action.get("tool_used", "")
            else:
                continue

            try:
                task = await ts.create_task(
                    user_id=user_id,
                    title=title[:255],
                    description=description or None,
                    priority="P2",
                )
                created_ids.append(task.id)
            except Exception as e:
                logger.warning("writeback create_task failed: %s", e)

        return created_ids

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    async def _get_persona(self, user_id: str) -> Optional[str]:
        try:
            from src.cognition.services.persona_engine import PersonaEngine

            pe = PersonaEngine(self.db)
            persona = await pe.build_persona(user_id)
            if isinstance(persona, dict):
                return persona.get("summary") or _persona_to_text(persona)
            elif isinstance(persona, str):
                return persona
        except Exception:
            pass
        return None

    async def _synthesize_sequential(
        self,
        *,
        user_id: str,
        question: str,
        role_outputs: Dict,
        context: Dict,
    ) -> str:
        """串行模式综合：将所有 role_outputs 汇总成 final_advice。"""
        parts: List[str] = []
        research = role_outputs.get("research", {})
        if research.get("findings"):
            parts.append("【调研发现】\n" + "\n".join(
                f"- {f}" for f in research["findings"]
            ))

        planning = role_outputs.get("planning", {})
        if planning.get("steps"):
            step_lines = []
            for s in planning["steps"]:
                title = s.get("title", "") if isinstance(s, dict) else str(s)
                priority = s.get("priority", "") if isinstance(s, dict) else ""
                step_lines.append(f"- [{priority}] {title}")
            parts.append("【计划步骤】\n" + "\n".join(step_lines))

        critic = role_outputs.get("critic", {})
        if critic.get("risks"):
            risk_lines = []
            for r in critic["risks"]:
                if isinstance(r, dict):
                    risk_lines.append(
                        f"- [{r.get('severity','')}] {r.get('risk','')}"
                    )
                else:
                    risk_lines.append(f"- {r}")
            parts.append("【风险提示】\n" + "\n".join(risk_lines))

        executor = role_outputs.get("executor", {})
        if executor.get("next_steps"):
            parts.append("【后续建议】\n" + "\n".join(
                f"- {s}" for s in executor["next_steps"]
            ))

        if not parts:
            return "各角色未产出有效输出。"

        # 尝试用 LLM 综合
        persona_text = await self._get_persona(user_id)
        combined = "\n\n".join(parts)
        prompt = build_synthesize_prompt(
            persona_block=persona_text or "（无人格数据）",
            question=question,
            draft_block=combined,
        )
        try:
            provider = get_llm_provider()
            text = await ModelGateway(provider).generate_text(prompt, temperature=0.4, max_tokens=1800, prompt_id="orchestration-synthesis", prompt_version="v1")
            if isinstance(text, str) and text.strip():
                return text.strip()
        except Exception:
            pass

        return combined

    def _compute_sequential_confidence(self, role_outputs: Dict) -> float:
        count = sum(1 for v in role_outputs.values() if not v.get("error"))
        return max(0.0, min(1.0, 0.3 + count * 0.15))

    @staticmethod
    def _format_memory_block(context: Dict) -> str:
        lines = []
        for i, m in enumerate((context.get("relevant_memories") or [])[:8]):
            lines.append(
                f"[{i+1}] (类型={m.get('memory_type','')}, 重要性={m.get('importance',0):.2f}) "
                f"id={m.get('memory_id','')} 标题={m.get('title','')} 内容={m.get('content','')[:200]}"
            )
        return "\n".join(lines) if lines else "（无相关记忆）"

    @staticmethod
    def _format_decision_block(context: Dict) -> str:
        lines = []
        for d in (context.get("decision_history") or [])[:5]:
            lines.append(
                f"- {d.get('content','')} (原因: {d.get('reason','')}, 结果: {d.get('outcome','')})"
            )
        return "\n".join(lines) if lines else "（无明显决策历史）"

    # --- 以下为原有 parallel 模式方法，保持不变 ---

    async def _select_agents(
        self,
        *,
        user_id: str,
        agent_ids: Optional[List[str]],
        max_agents: int,
    ) -> List[AgentProfile]:
        if agent_ids:
            result = await self.db.execute(
                select(AgentProfile).where(
                    AgentProfile.id.in_(agent_ids),
                    AgentProfile.user_id == user_id,
                )
            )
            agents = list(result.scalars().all())
            agents.sort(key=lambda a: agent_ids.index(a.id) if a.id in agent_ids else 0)
            return agents[:max_agents]

        result = await self.db.execute(
            select(AgentProfile)
            .where(AgentProfile.user_id == user_id, AgentProfile.status.is_(True))
            .order_by(AgentProfile.created_at.asc())
        )
        agents = list(result.scalars().all())
        return agents[:max_agents]

    async def _agent_draft(
        self,
        *,
        user_id: str,
        agent: AgentProfile,
        question: str,
        context: Dict,
        recall_level: str = "work_context",
    ) -> tuple[str, float]:
        prompt = self._build_agent_prompt(agent=agent, question=question, context=context)

        provider = get_llm_provider(
            agent_id=agent.id,
            llm_provider=agent.llm_provider.value if agent.llm_provider else None,
            custom_provider_key=agent.custom_provider_key,
            llm_model=agent.llm_model,
            llm_api_key=agent.llm_api_key,
            llm_api_base=agent.llm_api_base,
            llm_temperature=agent.llm_temperature or 0.4,
            llm_max_tokens=agent.llm_max_tokens or 1500,
        )

        text = await ModelGateway(provider).generate_text(prompt, model_name=agent.llm_model, temperature=0.4, max_tokens=1200, prompt_id="external-agent-draft", prompt_version="v1")
        if not isinstance(text, str):
            text = str(text)
        text = text.strip()

        confidence = self._compute_draft_confidence(context=context, has_text=bool(text))
        return text, confidence

    def _build_agent_prompt(
        self,
        *,
        agent: AgentProfile,
        question: str,
        context: Dict,
    ) -> str:
        persona_block = self._agent_persona_block(agent)
        memory_block = self._format_memory_block(context)
        decision_block = self._format_decision_block(context)

        return build_agent_prompt(
            agent_name=agent.agent_name,
            agent_role=agent.role or '通用助手',
            persona_block=persona_block,
            context_summary=context.get('context_summary') or '（无）',
            memory_block=memory_block,
            decision_block=decision_block,
            question=question,
        )

    def _agent_persona_block(self, agent: AgentProfile) -> str:
        lines = []
        if agent.mission:
            lines.append(f"使命: {agent.mission}")
        if agent.goals:
            try:
                goals_list = list(agent.goals)
                if goals_list:
                    lines.append("目标:\n- " + "\n- ".join(goals_list))
            except Exception:
                pass
        if agent.constraints:
            try:
                cons = list(agent.constraints)
                if cons:
                    lines.append("约束:\n- " + "\n- ".join(cons))
            except Exception:
                pass
        if agent.instructions:
            lines.append(f"指令: {agent.instructions}")
        return "\n".join(lines) if lines else "（无 persona 描述）"

    async def _synthesize(
        self,
        *,
        user_id: str,
        question: str,
        drafts: List[Dict],
    ) -> tuple[str, float, bool]:
        non_empty = [d for d in drafts if d.get("draft")]
        if not non_empty:
            return "", 0.1, True

        persona_text = await self._get_persona(user_id)

        draft_lines = []
        for i, d in enumerate(non_empty):
            draft_lines.append(
                f"[Agent {i+1}] name={d.get('agent_name','')} confidence={d.get('confidence',0):.2f}\n"
                f"  {d.get('draft','')[:600]}"
            )
        draft_block = "\n".join(draft_lines)

        prompt = build_synthesize_prompt(
            persona_block=persona_text or "（无人格数据）",
            question=question,
            draft_block=draft_block,
        )

        try:
            provider = get_llm_provider()
            text = await ModelGateway(provider).generate_text(prompt, temperature=0.4, max_tokens=1800, prompt_id="orchestration-critique", prompt_version="v1")
            if not isinstance(text, str):
                text = str(text)
            text = text.strip()
            if not text:
                return "", 0.1, True
            confidence = _synth_confidence(non_empty, persona_text)
            return text, confidence, False
        except Exception:
            return "", 0.1, True

    def _pick_top_draft(self, drafts: List[Dict]) -> tuple[str, float]:
        if not drafts:
            return "", 0.1
        ranked = sorted(drafts, key=lambda d: d.get("confidence") or 0.0, reverse=True)
        top = ranked[0]
        if not top.get("draft"):
            return "无 agent 能生成可用回答。", 0.1
        return (
            f"（综合 LLM 不可用, 取置信度最高的 agent 回答）\n\n{top['draft']}",
            float(top.get("confidence") or 0.0),
        )

    def _compute_draft_confidence(self, *, context: Dict, has_text: bool) -> float:
        conf = 0.3
        if not has_text:
            return 0.1
        total = (context.get("meta") or {}).get("total_found", 0) or 0
        conf += min(total * 0.05, 0.4)
        decisions = context.get("decision_history") or []
        if decisions:
            conf += min(len(decisions) * 0.05, 0.2)
        return max(0.0, min(1.0, conf))


# ---------------------------------------------------------------------------
# module-level helpers
# ---------------------------------------------------------------------------


def _avg_confidence(drafts: List[Dict]) -> float:
    if not drafts:
        return 0.0
    return sum(float(d.get("confidence") or 0.0) for d in drafts) / len(drafts)


def _synth_confidence(drafts: List[Dict], persona_text: Optional[str]) -> float:
    base = _avg_confidence(drafts)
    if persona_text:
        base += 0.1
    base += min(len(drafts) * 0.05, 0.2)
    return max(0.0, min(1.0, base))


def _persona_to_text(persona: Dict) -> str:
    if not isinstance(persona, dict):
        return ""
    bullets = []
    for k in ("traits", "preferences", "habits", "values"):
        v = persona.get(k)
        if isinstance(v, list):
            for item in v[:5]:
                bullets.append(f"- {k}: {item}")
    return "\n".join(bullets)


def _elapsed_ms(started_at: float) -> int:
    try:
        return int((time.perf_counter() - started_at) * 1000)
    except Exception:
        return 0
