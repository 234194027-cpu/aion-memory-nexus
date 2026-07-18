"""Advisor Engine v2.0。

综合 persona + decisions + conflicts + patterns 回答用户的五类问题:
- recall:    回忆模式 — 你以前怎么想？
- decision:  决策模式 — 你现在应该怎么判断？
- review:    复盘模式 — 过去判断是否正确？
- planning:  计划模式 — 下一步怎么做更稳？
- reflection: 反思模式 — 你最近的思维模式是什么？

设计要点:
- 自动 track: 从检索到的 memory 中识别 DECISION 类, 调用 DecisionTracker.auto_track_from_committed_memory
- 优雅退化: PersonaEngine / ConflictChecker / Dedup / Rewriter 是并行 agent 在做,
  我们用 try/except 让 Advisor 在它们尚未就绪时也能跑通, 不会因为依赖缺失崩。
- LLM 输出严格 JSON 解析, 解析失败用 fallback 结构。
- 成功后写入 AdvisorSession 表持久化。
"""
from __future__ import annotations

import json
import uuid
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from src.shared.llm.providers import get_llm_provider
from src.shared.llm.model_gateway import ModelGateway
from src.cognition.models.decision_record import DecisionRecord
from src.cognition.services.decision_tracker import DecisionTracker

from src.memory.services.retrieval_engine import RetrievalEngine
from src.cognition.prompts.advisor import build_advisor_instructions, build_advisor_prompt

# v2.0 模式: compare → planning, explain → reflection
VALID_MODES = {"recall", "decision", "review", "planning", "reflection"}


def _safe_get(obj, attr, default=None):
    try:
        return getattr(obj, attr, default)
    except Exception:
        return default


def _v2_fallback(raw_text: Optional[str], mode: str) -> Dict:
    """LLM 输出 JSON 解析失败时的 fallback 结构。"""
    return {
        "answer": raw_text or "无法生成回答",
        "direct_recommendation": "",
        "historical_basis": [],
        "risk_points": [],
        "conflicts_or_changes": [],
        "suggested_next_steps": [],
        "uncertainty": "LLM 输出解析失败",
        "cited_memories": [],
        "cited_decisions": [],
        "advisor_mode": mode,
        "confidence": 0.2,
        "meta": {"fallback": True},
    }


def _parse_llm_json(raw_text: str, mode: str) -> Dict:
    """尝试从 LLM 输出中解析 JSON, 失败时返回 fallback。"""
    from src.shared.utils.llm_output import extract_json_object

    if not raw_text or not raw_text.strip():
        return _v2_fallback(raw_text, mode)

    parsed = extract_json_object(raw_text)
    if isinstance(parsed, dict) and "answer" in parsed:
        parsed.setdefault("advisor_mode", mode)
        return parsed

    return _v2_fallback(raw_text, mode)


def _empty_context() -> Dict:
    """上下文检索完全失败时的空结构。"""
    return {
        "context_summary": "",
        "decision_history": [],
        "patterns": [],
        "conflicts": [],
        "relevant_memories": [],
        "entities": [],
        "meta": {"total_found": 0},
    }


class AdvisorEngine:
    def __init__(self, db: AsyncSession):
        self.db = db

    # ── 上下文构建（可由 ControlPlane 调用）──────────────────────────────

    async def build_advisor_context(
        self,
        user_id: str,
        question: str,
        *,
        project_id: Optional[str] = None,
        recall_level: str = "work_context",
        top_k: int = 20,
    ) -> Dict:
        """为 Advisor 构建上下文。

        职责单一: 只负责把检索结果组装成 Advisor 需要的 context dict。
        未来由 ControlPlane 统一调度时, 可直接调用此方法注入上下文,
        而不必走 advise() 内部的 fallback 检索路径。
        """
        try:
            engine = RetrievalEngine(self.db)
            return await engine.reconstruct_context(
                user_id=user_id,
                question=question,
                project_id=project_id,
                recall_level=recall_level,
                top_k=top_k,
            )
        except Exception:
            return _empty_context()

    # ── 主入口 ─────────────────────────────────────────────────────────

    async def advise(
        self,
        user_id: str,
        question: str,
        *,
        mode: str = "decision",
        recall_level: str = "work_context",
        project_id: Optional[str] = None,
        decision_ids: Optional[List[str]] = None,
        context: Optional[Dict] = None,
    ) -> Dict:
        if mode not in VALID_MODES:
            mode = "decision"
        if not question or not question.strip():
            question = "(用户未提供问题)"

        warnings: List[str] = []

        # ── 1. 获取上下文 ──────────────────────────────────────────────
        # 优先使用调用方注入的 context（ControlPlane 场景）;
        # 未注入时走自身检索 fallback, 保持向后兼容。
        if context is not None:
            ctx = context
        else:
            ctx = await self.build_advisor_context(
                user_id=user_id,
                question=question,
                project_id=project_id,
                recall_level=recall_level,
            )
            if not (ctx.get("meta") or {}).get("total_found"):
                warnings.append("context_empty")

        # ── 2. 人格模型 ────────────────────────────────────────────────
        persona_text: Optional[str] = None
        persona_used = False
        try:
            from src.cognition.services.persona_engine import PersonaEngine  # type: ignore
            pe = PersonaEngine(self.db)
            persona = await pe.build_persona(user_id, project_id=project_id)
            persona_text = _format_persona(persona)
            persona_used = bool(persona_text)
        except Exception as e:
            warnings.append(f"persona_unavailable: {e}")

        # ── 3. 决策跟踪 ────────────────────────────────────────────────
        open_decisions: List[DecisionRecord] = []
        try:
            tracker = DecisionTracker(self.db)
            open_decisions = await tracker.list_open_decisions(
                user_id, project_id=project_id, limit=10
            )
        except Exception as e:
            warnings.append(f"decision_tracker_error: {e}")

        tracked_count = 0
        try:
            # 批量预加载 decision 类 memory，避免循环内逐条查询（N+1 → 2 次查询）
            # AsyncSession 非并发安全，因此先批量 SELECT 再串行创建。
            decision_mids = [
                mem.get("memory_id")
                for mem in (ctx.get("relevant_memories") or [])[:10]
                if mem.get("memory_id") and mem.get("memory_type") == "decision"
            ]

            if decision_mids:
                from sqlalchemy import select as _select, and_ as _and
                from src.memory.models.committed_memory import CommittedMemory as _CM
                from src.memory.models.memory_type import MemoryType as _MT

                # 一次 IN 查询加载所有 CommittedMemory
                mem_rows = await self.db.execute(
                    _select(_CM).where(_CM.id.in_(decision_mids))
                )
                memories_by_id = {m.id: m for m in mem_rows.scalars().all()}

                # 一次 IN 查询加载所有已存在的 DecisionRecord（按 linked_memory_id）
                existing_rows = await self.db.execute(
                    _select(DecisionRecord).where(
                        _and(
                            DecisionRecord.user_id == user_id,
                            DecisionRecord.linked_memory_id.in_(decision_mids),
                        )
                    )
                )
                existing_by_mid = {
                    r.linked_memory_id: r
                    for r in existing_rows.scalars().all()
                    if r.linked_memory_id
                }

                # 串行创建新记录（AsyncSession 非并发安全，不能 asyncio.gather）
                tracker = DecisionTracker(self.db)
                for mid in decision_mids:
                    memory = memories_by_id.get(mid)
                    if memory is None or memory.user_id != user_id:
                        continue
                    if memory.memory_type != _MT.DECISION:
                        continue
                    # 已有跟踪记录则跳过创建
                    if mid in existing_by_mid:
                        tracked_count += 1
                        continue
                    try:
                        title = memory.title or "未命名决策"
                        body = memory.body or ""
                        decision_text = body.splitlines()[0] if body else title
                        rec = await tracker.track_decision(
                            user_id=user_id,
                            title=title,
                            context=memory.body or "",
                            decision=decision_text,
                            rationale=body,
                            expected_outcome=memory.body if memory.body else None,
                            project_id=memory.project_id,
                            linked_memory_id=memory.id,
                        )
                        if rec is not None:
                            tracked_count += 1
                            existing_by_mid[mid] = rec  # 防同批次重复
                    except Exception:
                        continue
        except Exception as e:
            warnings.append(f"auto_track_error: {e}")

        extra_decisions: List[DecisionRecord] = []
        if decision_ids:
            from sqlalchemy import select

            result = await self.db.execute(
                select(DecisionRecord).where(DecisionRecord.id.in_(decision_ids))
            )
            for rec in result.scalars().all():
                if rec.user_id != user_id:
                    continue
                if rec not in open_decisions and rec not in extra_decisions:
                    extra_decisions.append(rec)

        # ── 4. 冲突记录 ────────────────────────────────────────────────
        conflict_payload: List[Dict] = []
        try:
            from src.memory.services.conflict_checker import ConflictChecker  # type: ignore
            checker = ConflictChecker(self.db)
            conflicts_raw = await checker.check_for_user(
                user_id, project_id=project_id, limit=5
            )
            for c in conflicts_raw or []:
                if isinstance(c, dict):
                    conflict_payload.append(c)
                else:
                    conflict_payload.append({"summary": str(c)})
        except Exception as e:
            warnings.append(f"conflict_checker_unavailable: {e}")

        # ── 5. 构建 supporting_decisions ───────────────────────────────
        supporting_decisions: List[Dict] = []
        for rec in (open_decisions + extra_decisions)[:10]:
            supporting_decisions.append({
                "id": rec.id,
                "title": rec.title,
                "status": rec.status,
                "decided_at": _safe_get(rec, "decided_at").isoformat() if _safe_get(rec, "decided_at") else None,
                "linked_memory_id": rec.linked_memory_id,
                "project_id": rec.project_id,
            })

        # ── 6. 构建 prompt 并调用 LLM ─────────────────────────────────
        prompt = self._build_prompt(
            mode=mode,
            question=question,
            context=ctx,
            persona_text=persona_text,
            supporting_decisions=supporting_decisions,
            conflicts=conflict_payload,
        )

        advice_text, llm_failed = await self._generate_advice(prompt)

        confidence = self._compute_confidence(
            context=ctx,
            supporting_decisions=supporting_decisions,
            persona_used=persona_used,
            llm_failed=llm_failed,
        )

        # ── 7. 解析 LLM JSON 输出 ─────────────────────────────────────
        if llm_failed or not advice_text:
            # LLM 完全失败 → 使用 markdown fallback, 再包成 v2 结构
            fallback_text = self._fallback_advice(
                mode=mode,
                supporting_decisions=supporting_decisions,
                persona_text=persona_text,
                context_summary=ctx.get("context_summary", ""),
            )
            warnings.append("llm_fallback")
            parsed = _v2_fallback(fallback_text, mode)
            parsed["confidence"] = min(confidence, 0.35)
        else:
            parsed = _parse_llm_json(advice_text, mode)
            if parsed.get("meta", {}).get("fallback"):
                # JSON 解析失败, 但 LLM 确实返回了文本
                warnings.append("llm_json_parse_failed")
                parsed["confidence"] = min(confidence, 0.35)
            else:
                parsed["confidence"] = confidence

        # ── 8. 组装 v2.0 输出结构 ─────────────────────────────────────
        elapsed_ms = 0
        try:
            elapsed_ms = int(time.perf_counter() * 1000)
        except Exception:
            pass

        meta = parsed.get("meta") or {}
        meta.update({
            "mode": mode,
            "recall_level": recall_level,
            "project_id": project_id,
            "decision_ids": decision_ids or [],
            "tracked_count": tracked_count,
            "context_total_found": (ctx.get("meta") or {}).get("total_found", 0),
            "embed_method": (ctx.get("meta") or {}).get("embed_method", "keyword"),
            "persona_used": persona_used,
            "asked_at": datetime.now(timezone.utc).isoformat(),
            "elapsed_ms": elapsed_ms,
        })

        result: Dict = {
            "answer": parsed.get("answer", ""),
            "direct_recommendation": parsed.get("direct_recommendation", ""),
            "historical_basis": parsed.get("historical_basis", []),
            "risk_points": parsed.get("risk_points", []),
            "conflicts_or_changes": parsed.get("conflicts_or_changes", []),
            "suggested_next_steps": parsed.get("suggested_next_steps", []),
            "uncertainty": parsed.get("uncertainty", ""),
            "cited_memories": parsed.get("cited_memories", []),
            "cited_decisions": parsed.get("cited_decisions", []),
            "advisor_mode": parsed.get("advisor_mode", mode),
            "confidence": parsed.get("confidence", confidence),
            "meta": meta,
            # 保留旧字段以向后兼容
            "user_id": user_id,
            "question": question,
            "mode": mode,
            "advice": parsed.get("answer", ""),
            "supporting_decisions": supporting_decisions,
            "conflicts": conflict_payload,
            "persona_used": persona_used,
            "warnings": warnings,
        }

        # ── 9. 持久化 AdvisorSession ──────────────────────────────────
        try:
            await self._save_session(user_id, question, result, project_id)
        except Exception as e:
            warnings.append(f"session_save_error: {e}")

        return result

    async def _save_session(
        self,
        user_id: str,
        question: str,
        result: Dict,
        project_id: Optional[str],
    ) -> None:
        """将成功的 advisor 结果写入 AdvisorSession 表。

        注意: AdvisorSession 模型字段有限, 这里只持久化模型支持的字段;
        其余扩展字段(如 historical_basis / conflicts_or_changes /
        suggested_next_steps / meta)合并到 risk_points 中以 JSON 形式保存,
        避免字段名不匹配导致持久化失败。
        """
        from src.cognition.models.advisor_session import AdvisorSession

        # 将模型未直接支持的字段合并成扩展 JSON, 写入 risk_points (Text) 字段
        extended_meta = {
            "historical_basis": result.get("historical_basis", []),
            "conflicts_or_changes": result.get("conflicts_or_changes", []),
            "suggested_next_steps": result.get("suggested_next_steps", []),
            "meta": result.get("meta", {}),
            "project_id": project_id,
        }
        risk_points_data = {
            "risk_points": result.get("risk_points", []),
            "extended": extended_meta,
        }

        session = AdvisorSession(
            id=uuid.uuid4().hex[:16],
            user_id=user_id,
            question=question,
            advisor_mode=result.get("advisor_mode", "decision"),
            answer=result.get("answer", ""),
            direct_recommendation=result.get("direct_recommendation"),
            cited_memory_ids=json.dumps(result.get("cited_memories", []), ensure_ascii=False),
            cited_decision_ids=json.dumps(result.get("cited_decisions", []), ensure_ascii=False),
            risk_points=json.dumps(risk_points_data, ensure_ascii=False),
            uncertainty=result.get("uncertainty"),
            confidence=result.get("confidence", 0.5),
        )
        self.db.add(session)
        await self.db.commit()

    def _build_prompt(
        self,
        *,
        mode: str,
        question: str,
        context: Dict,
        persona_text: Optional[str],
        supporting_decisions: List[Dict],
        conflicts: List[Dict],
    ) -> str:
        instructions = build_advisor_instructions()
        instruction = instructions.get(mode, instructions.get("decision", ""))

        context_summary = context.get("context_summary", "")
        patterns = context.get("patterns", []) or []
        relevant_memories = context.get("relevant_memories", []) or []

        memory_lines = []
        for i, m in enumerate(relevant_memories[:10]):
            memory_lines.append(
                f"[{i+1}] (类型={m.get('memory_type','')}, 重要性={m.get('importance',0):.2f}) "
                f"id={m.get('memory_id','')} 标题={m.get('title','')} 内容={m.get('content','')[:200]}"
            )
        memory_block = "\n".join(memory_lines) if memory_lines else "（无相关记忆）"

        pattern_text = "\n".join([f"- {p}" for p in patterns[:5]]) if patterns else "- （无明显模式）"

        conflict_text = ""
        if conflicts:
            conflict_lines = []
            for c in conflicts[:3]:
                conflict_lines.append(f"- {json.dumps(c, ensure_ascii=False)}")
            conflict_text = "\n".join(conflict_lines)
        else:
            conflict_text = "- （无冲突）"

        sup_lines = []
        for sd in supporting_decisions:
            sup_lines.append(
                f"- id={sd.get('id')} status={sd.get('status')} title={sd.get('title')}"
            )
        sup_text = "\n".join(sup_lines) if sup_lines else "- （无 open 决策）"

        persona_block = persona_text or "（无人格数据）"

        return build_advisor_prompt(
            mode=mode,
            instruction=instruction,
            persona_block=persona_block,
            context_summary=context_summary,
            pattern_text=pattern_text,
            conflict_text=conflict_text,
            sup_text=sup_text,
            memory_block=memory_block,
            question=question,
        )

    async def _generate_advice(self, prompt: str):
        try:
            provider = get_llm_provider()
            text = await ModelGateway(provider).generate_text(prompt, temperature=0.4, max_tokens=2000, prompt_id="cognitive-advisor", prompt_version="v1")
            if not isinstance(text, str):
                text = str(text)
            return text.strip(), False
        except Exception:
            return "", True

    def _fallback_advice(
        self,
        *,
        mode: str,
        supporting_decisions: List[Dict],
        persona_text: Optional[str],
        context_summary: str,
    ) -> str:
        lines: List[str] = []
        if persona_text:
            lines.append("【人格画像】")
            lines.append(persona_text)
            lines.append("")
        if supporting_decisions:
            lines.append("【相关 Open 决策】")
            lines.append("| id | 标题 | 状态 | 决策时间 |")
            lines.append("|---|---|---|---|")
            for sd in supporting_decisions:
                lines.append(
                    f"| {sd.get('id','')} | {sd.get('title','')} | "
                    f"{sd.get('status','')} | {sd.get('decided_at','') or ''} |"
                )
            lines.append("")
        if context_summary:
            lines.append("【上下文摘要】")
            lines.append(context_summary)
            lines.append("")
        lines.append(f"（LLM 暂不可用, 已按模式 {mode} 返回决策清单）")
        return "\n".join(lines) if lines else f"暂无可用决策或记忆 (mode={mode})"

    def _compute_confidence(
        self,
        *,
        context: Dict,
        supporting_decisions: List[Dict],
        persona_used: bool,
        llm_failed: bool,
    ) -> float:
        conf = 0.4
        total_found = (context.get("meta") or {}).get("total_found", 0) or 0
        conf += min(total_found * 0.03, 0.25)
        if supporting_decisions:
            conf += min(len(supporting_decisions) * 0.04, 0.2)
        if persona_used:
            conf += 0.1
        if llm_failed:
            conf = min(conf, 0.35)
        return max(0.0, min(1.0, conf))


def _format_persona(persona) -> Optional[str]:
    """PersonaEngine.build_persona 的返回值结构由并行 agent 决定。
    这里做一个最宽容的格式化: 能 dict 就 dict, 能 str 就 str, 否则 None。
    """
    if persona is None:
        return None
    if isinstance(persona, str):
        return persona.strip() or None
    if isinstance(persona, dict):
        summary = persona.get("summary") or persona.get("description")
        if summary:
            return str(summary).strip()
        bullets = []
        for k in ("traits", "preferences", "habits", "values"):
            v = persona.get(k)
            if isinstance(v, list):
                for item in v[:5]:
                    bullets.append(f"- {k}: {item}")
        return "\n".join(bullets) if bullets else None
    return None
