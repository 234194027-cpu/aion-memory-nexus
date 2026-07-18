"""Simulation Engine (Gen 3).

"如果当初 ... 会怎样" 的反事实推演:
- 基于用户历史决策 + 记忆模式 + persona 推断可能后果
- 不修改任何 memory / decision
- 默认 dry_run=False 时把结果写入 simulation_runs 表
- LLM 失败 -> counterfactual = "模拟失败: {msg}", confidence=0.2, 其它字段尽量留空
- horizon_days 控制视角跨度 (1~365)
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from src.shared.llm.providers import get_llm_provider
from src.shared.llm.model_gateway import ModelGateway
from src.cognition.models.decision_record import DecisionRecord
from src.execution.models.simulation_run import SimulationRun
from src.memory.models.memory_type import MemoryType
from src.cognition.services.decision_tracker import DecisionTracker
from src.memory.services.retrieval_engine import RetrievalEngine
from src.shared.ids.id_generator import generate_simulation_run_id
from src.execution.prompts.simulation import (
    build_simulation_prompt,
    build_simulation_v3_prompt,
    build_similar_decision_lesson_prompt,
)


logger = logging.getLogger(__name__)


class SimulationEngine:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def simulate(
        self,
        user_id: str,
        question: str,
        *,
        horizon_days: int = 90,
    ) -> Dict:
        if not question or not question.strip():
            question = "(用户未提供反事实问题)"

        horizon = max(1, min(365, int(horizon_days or 90)))
        warnings: List[str] = []

        try:
            baseline_text, baseline_memory_ids = await self._build_baseline(
                user_id=user_id, question=question
            )
        except Exception as e:
            baseline_text = ""
            baseline_memory_ids = []
            warnings.append(f"baseline_error: {e}")

        try:
            decisions = await self._collect_decisions(user_id=user_id)
        except Exception as e:
            decisions = []
            warnings.append(f"decisions_error: {e}")

        supporting_decisions = [
            {
                "id": d.id,
                "title": d.title,
                "decision": (d.decision or "")[:200],
                "status": d.status,
                "linked_memory_id": d.linked_memory_id,
            }
            for d in decisions[:10]
        ]

        persona_text: Optional[str] = None
        try:
            from src.cognition.services.persona_engine import PersonaEngine  # type: ignore
            pe = PersonaEngine(self.db)
            persona = await pe.build_persona(user_id)
            if isinstance(persona, dict):
                persona_text = persona.get("summary")
                if not persona_text:
                    bullets = []
                    for k in ("traits", "preferences", "habits", "values"):
                        v = persona.get(k)
                        if isinstance(v, list):
                            for item in v[:5]:
                                bullets.append(f"- {k}: {item}")
                    persona_text = "\n".join(bullets) or None
            elif isinstance(persona, str):
                persona_text = persona
        except Exception as e:
            warnings.append(f"persona_unavailable: {e}")
            persona_text = None

        # --- v3: 历史模式分析 + 类似决策检索 ---
        historical_patterns: Dict = {}
        similar_decisions: List[Dict] = []
        try:
            historical_patterns = await self._analyze_historical_patterns(user_id)
        except Exception as e:
            warnings.append(f"historical_patterns_error: {e}")
            historical_patterns = {
                "total_decisions": 0,
                "success_rate": 0.0,
                "failure_rate": 0.0,
                "abandonment_rate": 0.0,
                "avg_time_to_resolution_days": 0.0,
                "common_failure_patterns": [],
                "common_success_patterns": [],
            }

        try:
            similar_decisions = await self.find_similar_decisions(user_id, question)
        except Exception as e:
            warnings.append(f"similar_decisions_error: {e}")

        similar_decisions_block_lines = []
        for sd in similar_decisions:
            similar_decisions_block_lines.append(
                f"- id={sd.get('decision_id','')} title={sd.get('title','')} "
                f"status={sd.get('status','')} lesson={sd.get('lesson','')}"
            )
        similar_decisions_block = (
            "\n".join(similar_decisions_block_lines) if similar_decisions_block_lines else "（无类似历史决策）"
        )

        counterfactual_text = ""
        predicted_outcome = ""
        confidence = 0.4
        risk_factors: List[str] = []
        risk_level = "medium"
        similar_past_decisions: List[Dict] = []
        historical_pattern_match: Dict = {}
        try:
            prompt = self._build_prompt(
                question=question,
                horizon_days=horizon,
                baseline=baseline_text,
                persona_text=persona_text,
                supporting_decisions=supporting_decisions,
                historical_patterns=historical_patterns,
                similar_decisions_block=similar_decisions_block,
            )
            provider = get_llm_provider()
            response = await ModelGateway(provider).generate_text(prompt, temperature=0.5, max_tokens=2000, prompt_id="decision-simulation", prompt_version="v1")
            if not isinstance(response, str):
                response = str(response)
            response = response.strip()

            parsed = self._parse_response(response)
            counterfactual_text = parsed.get("counterfactual") or "（反事实场景未给出）"
            predicted_outcome = parsed.get("outcome") or "（后果未给出）"
            risk_factors = parsed.get("risk_factors", [])
            if not isinstance(risk_factors, list):
                risk_factors = []
            risk_level = parsed.get("risk_level", "medium")
            if risk_level not in ("low", "medium", "high"):
                risk_level = "medium"
            similar_past_decisions = parsed.get("similar_past_decisions", [])
            if not isinstance(similar_past_decisions, list):
                similar_past_decisions = []
            historical_pattern_match = parsed.get("historical_pattern_match", {})
            if not isinstance(historical_pattern_match, dict):
                historical_pattern_match = {}
            confidence = self._compute_confidence(
                baseline=baseline_text,
                decisions=supporting_decisions,
                persona=persona_text,
                synth_failed=False,
            )
        except Exception as e:
            warnings.append(f"llm_failure: {e}")
            counterfactual_text = f"模拟失败: {e}"
            predicted_outcome = ""
            confidence = 0.2

        result: Dict = {
            "user_id": user_id,
            "question": question,
            "baseline": baseline_text,
            "counterfactual": counterfactual_text,
            "predicted_outcome": predicted_outcome,
            "supporting_memories": baseline_memory_ids,
            "supporting_decisions": [sd["id"] for sd in supporting_decisions if sd.get("id")],
            "confidence": round(float(confidence), 4),
            "warnings": warnings,
            "horizon_days": horizon,
            "run_id": None,
            "created_at": datetime.now(timezone.utc).isoformat(),
            # v3 新增
            "risk_factors": risk_factors,
            "risk_level": risk_level,
            "similar_past_decisions": similar_past_decisions,
            "historical_pattern_match": historical_pattern_match,
        }

        try:
            run_id = await self._persist(
                user_id=user_id,
                question=question,
                baseline=baseline_text,
                counterfactual_payload={
                    "text": counterfactual_text,
                    "horizon_days": horizon,
                },
                outcome=predicted_outcome,
                confidence=float(confidence),
                linked_memory_ids=baseline_memory_ids,
            )
            result["run_id"] = run_id
        except Exception as e:
            warnings.append(f"persist_error: {e}")

        if result.get("run_id"):
            from src.execution.services.audit_logger import AuditLogger
            await AuditLogger.log(
                self.db,
                user_id=user_id,
                action="simulation_run",
                actor_type="user",
                actor_id=user_id,
                target_type="simulation_run",
                target_id=result["run_id"],
                detail={"question": question[:200], "confidence": round(float(confidence), 4)},
            )

        return result

    async def _build_baseline(
        self,
        *,
        user_id: str,
        question: str,
    ) -> tuple[str, List[str]]:
        try:
            retrieval = RetrievalEngine(self.db)
            context = await retrieval.reconstruct_context(
                user_id=user_id,
                question=question,
                recall_level="work_context",
                top_k=20,
            )
        except Exception:
            context = {
                "context_summary": "",
                "relevant_memories": [],
                "decision_history": [],
                "patterns": [],
                "conflicts": [],
            }

        mem_ids: List[str] = []
        for m in (context.get("relevant_memories") or [])[:20]:
            mid = m.get("memory_id")
            if mid:
                mem_ids.append(str(mid))

        memory_lines = []
        for i, m in enumerate((context.get("relevant_memories") or [])[:10]):
            memory_lines.append(
                f"[{i+1}] (类型={m.get('memory_type','')}, 重要性={m.get('importance',0):.2f}) "
                f"id={m.get('memory_id','')} 标题={m.get('title','')} 内容={m.get('content','')[:200]}"
            )
        memory_block = "\n".join(memory_lines) if memory_lines else "（无相关记忆）"

        decision_lines = []
        for d in (context.get("decision_history") or [])[:5]:
            decision_lines.append(
                f"- {d.get('content','')} (原因: {d.get('reason','')}, 结果: {d.get('outcome','')})"
            )
        decision_block = "\n".join(decision_lines) if decision_lines else "（无明显决策历史）"

        pattern_block = "\n".join(f"- {p}" for p in (context.get("patterns") or [])[:5])
        pattern_block = pattern_block or "（无明显模式）"

        baseline = (
            f"【上下文摘要】\n{context.get('context_summary') or '（无）'}\n\n"
            f"【相关记忆】\n{memory_block}\n\n"
            f"【决策历史】\n{decision_block}\n\n"
            f"【行为模式】\n{pattern_block}"
        )
        return baseline, mem_ids

    async def _collect_decisions(self, *, user_id: str) -> List[DecisionRecord]:
        try:
            tracker = DecisionTracker(self.db)
            return await tracker.list_open_decisions(user_id, limit=10)
        except Exception:
            result = await self.db.execute(
                select(DecisionRecord)
                .where(DecisionRecord.user_id == user_id)
                .order_by(DecisionRecord.decided_at.desc())
                .limit(10)
            )
            return list(result.scalars().all())

    def _build_prompt(
        self,
        *,
        question: str,
        horizon_days: int,
        baseline: str,
        persona_text: Optional[str],
        supporting_decisions: List[Dict],
        historical_patterns: Optional[Dict] = None,
        similar_decisions_block: Optional[str] = None,
    ) -> str:
        persona_block = persona_text or "（无人格数据）"
        decision_lines = []
        for d in supporting_decisions:
            decision_lines.append(
                f"- id={d.get('id','')} status={d.get('status','')} "
                f"title={d.get('title','')} decision={(d.get('decision') or '')[:160]}"
            )
        decision_block = "\n".join(decision_lines) if decision_lines else "（无 open 决策）"

        # 使用 v3 prompt (含历史模式和类似决策)
        if historical_patterns is not None:
            return build_simulation_v3_prompt(
                persona_block=persona_block,
                baseline=baseline,
                decision_block=decision_block,
                question=question,
                horizon_days=horizon_days,
                historical_patterns=historical_patterns,
                similar_decisions_block=similar_decisions_block or "（无类似历史决策）",
            )

        # 降级: 使用基础 prompt
        return build_simulation_prompt(
            persona_block=persona_block,
            baseline=baseline,
            decision_block=decision_block,
            question=question,
            horizon_days=horizon_days,
        )

    def _parse_response(self, text: str) -> Dict:
        text = (text or "").strip()
        if not text:
            return {}

        from src.shared.utils.llm_output import extract_json_object
        data = extract_json_object(text)

        try:
            if not isinstance(data, dict):
                raise ValueError("not a dict")
            out: Dict = {
                "counterfactual": str(data.get("counterfactual") or "").strip(),
                "outcome": str(data.get("outcome") or "").strip(),
            }
            try:
                conf_val = float(data.get("confidence"))
                out["confidence"] = max(0.0, min(1.0, conf_val))
            except (TypeError, ValueError):
                pass
            # v3 字段
            risk_factors = data.get("risk_factors")
            if isinstance(risk_factors, list):
                out["risk_factors"] = [str(r) for r in risk_factors[:5]]
            risk_level = data.get("risk_level")
            if isinstance(risk_level, str) and risk_level in ("low", "medium", "high"):
                out["risk_level"] = risk_level
            similar_past = data.get("similar_past_decisions")
            if isinstance(similar_past, list):
                out["similar_past_decisions"] = similar_past
            hpm = data.get("historical_pattern_match")
            if isinstance(hpm, dict):
                out["historical_pattern_match"] = hpm
            return out
        except Exception:
            return {
                "counterfactual": "",
                "outcome": text,
            }

    def _compute_confidence(
        self,
        *,
        baseline: str,
        decisions: List[Dict],
        persona: Optional[str],
        synth_failed: bool,
    ) -> float:
        if synth_failed:
            return 0.2
        conf = 0.4
        if baseline and len(baseline) > 100:
            conf += 0.15
        if decisions:
            conf += min(len(decisions) * 0.04, 0.2)
        if persona:
            conf += 0.1
        return max(0.0, min(1.0, conf))

    async def _persist(
        self,
        *,
        user_id: str,
        question: str,
        baseline: str,
        counterfactual_payload: Dict,
        outcome: str,
        confidence: float,
        linked_memory_ids: List[str],
    ) -> str:
        run = SimulationRun(
            id=generate_simulation_run_id(),
            user_id=user_id,
            question=question,
            baseline_summary=baseline or None,
            counterfactual=json.dumps(counterfactual_payload, ensure_ascii=False),
            outcome=outcome or None,
            confidence=float(confidence),
            linked_memory_ids=json.dumps(linked_memory_ids, ensure_ascii=False),
            horizon_days=str(counterfactual_payload.get("horizon_days") or ""),
        )
        self.db.add(run)
        await self.db.commit()
        await self.db.refresh(run)
        return run.id

    async def list_runs(
        self,
        user_id: str,
        *,
        limit: int = 20,
    ) -> List[SimulationRun]:
        result = await self.db.execute(
            select(SimulationRun)
            .where(SimulationRun.user_id == user_id)
            .order_by(SimulationRun.created_at.desc())
            .limit(max(1, min(100, int(limit))))
        )
        return list(result.scalars().all())

    async def get_run(self, user_id: str, run_id: str) -> Optional[SimulationRun]:
        result = await self.db.execute(
            select(SimulationRun).where(
                SimulationRun.id == run_id,
                SimulationRun.user_id == user_id,
            )
        )
        return result.scalar_one_or_none()

    # ------------------------------------------------------------------ v3: historical patterns

    async def _analyze_historical_patterns(self, user_id: str) -> Dict:
        """分析用户历史决策模式。"""
        # 统计所有决策的 status 分布
        result = await self.db.execute(
            select(
                DecisionRecord.status,
                func.count().label("cnt"),
            )
            .where(DecisionRecord.user_id == user_id)
            .group_by(DecisionRecord.status)
        )
        status_counts: Dict[str, int] = {}
        for row in result.all():
            status_counts[row[0] or "unknown"] = int(row[1])

        total = sum(status_counts.values())
        if total == 0:
            return {
                "total_decisions": 0,
                "success_rate": 0.0,
                "failure_rate": 0.0,
                "abandonment_rate": 0.0,
                "avg_time_to_resolution_days": 0.0,
                "common_failure_patterns": [],
                "common_success_patterns": [],
            }

        success_count = status_counts.get("resolved", 0) + status_counts.get("succeeded", 0) + status_counts.get("done", 0)
        failure_count = status_counts.get("failed", 0)
        abandoned_count = status_counts.get("abandoned", 0)

        success_rate = round(success_count / total, 4)
        failure_rate = round(failure_count / total, 4)
        abandonment_rate = round(abandoned_count / total, 4)

        # 计算平均解决时间 (天)
        resolved_res = await self.db.execute(
            select(DecisionRecord)
            .where(
                DecisionRecord.user_id == user_id,
                DecisionRecord.resolved_at.isnot(None),
            )
            .limit(200)
        )
        resolved_decisions = list(resolved_res.scalars().all())
        resolution_days: List[float] = []
        for d in resolved_decisions:
            if d.resolved_at and d.created_at:
                delta = (d.resolved_at - d.created_at).total_seconds() / 86400
                if delta >= 0:
                    resolution_days.append(delta)
        avg_resolution = round(sum(resolution_days) / len(resolution_days), 1) if resolution_days else 0.0

        # LLM 分析失败/成功模式
        common_failure_patterns: List[str] = []
        common_success_patterns: List[str] = []
        try:
            # 取 failed/abandoned 的决策
            failed_res = await self.db.execute(
                select(DecisionRecord)
                .where(
                    DecisionRecord.user_id == user_id,
                    DecisionRecord.status.in_(["failed", "abandoned"]),
                )
                .order_by(DecisionRecord.decided_at.desc())
                .limit(10)
            )
            failed_list = []
            for d in failed_res.scalars().all():
                failed_list.append({
                    "title": d.title or "",
                    "decision": (d.decision or "")[:200],
                    "status": d.status,
                    "actual_outcome": (d.actual_outcome or "")[:200],
                })

            # 取 resolved/succeeded/done 的决策
            success_res = await self.db.execute(
                select(DecisionRecord)
                .where(
                    DecisionRecord.user_id == user_id,
                    DecisionRecord.status.in_(["resolved", "succeeded", "done"]),
                )
                .order_by(DecisionRecord.decided_at.desc())
                .limit(10)
            )
            success_list = []
            for d in success_res.scalars().all():
                success_list.append({
                    "title": d.title or "",
                    "decision": (d.decision or "")[:200],
                    "status": d.status,
                    "actual_outcome": (d.actual_outcome or "")[:200],
                })

            if failed_list or success_list:
                import json as _json
                provider = get_llm_provider()
                prompt = (
                    f"分析以下决策, 提取失败模式和成功模式 (每个最多 3 条, 每条不超过 50 字)。\n\n"
                    f"失败决策:\n{_json.dumps(failed_list, ensure_ascii=False, default=str)}\n\n"
                    f"成功决策:\n{_json.dumps(success_list, ensure_ascii=False, default=str)}\n\n"
                    f'输出严格 JSON: {{"failure_patterns": ["..."], "success_patterns": ["..."]}}\n只输出 JSON。'
                )
                response = await ModelGateway(provider).generate_text(prompt, temperature=0.3, max_tokens=500, prompt_id="decision-pattern-analysis", prompt_version="v1")
                resp_text = (response or "").strip()
                s = resp_text.find("{")
                e = resp_text.rfind("}")
                if s != -1 and e != -1 and e > s:
                    patterns = _json.loads(resp_text[s:e + 1])
                    common_failure_patterns = patterns.get("failure_patterns", [])
                    common_success_patterns = patterns.get("success_patterns", [])
        except Exception as e:
            logger.warning("_analyze_historical_patterns LLM failed: %s", e)

        return {
            "total_decisions": total,
            "success_rate": success_rate,
            "failure_rate": failure_rate,
            "abandonment_rate": abandonment_rate,
            "avg_time_to_resolution_days": avg_resolution,
            "common_failure_patterns": common_failure_patterns[:5],
            "common_success_patterns": common_success_patterns[:5],
        }

    # ------------------------------------------------------------------ v3: similar decisions

    async def find_similar_decisions(
        self, user_id: str, question: str, *, top_k: int = 3
    ) -> List[Dict]:
        """查找与当前问题类似的历史决策。"""
        try:
            retrieval = RetrievalEngine(self.db)
            context = await retrieval.reconstruct_context(
                user_id=user_id,
                question=question,
                recall_level="work_context",
                top_k=top_k * 5,
            )
        except Exception:
            return []

        # 过滤出 DECISION 类型的记忆
        decision_memories = []
        for m in (context.get("relevant_memories") or []):
            mt = m.get("memory_type", "")
            if mt in ("DECISION", "decision", MemoryType.DECISION.value):
                decision_memories.append(m)

        if not decision_memories:
            return []

        results: List[Dict] = []
        provider = get_llm_provider()
        for m in decision_memories[:top_k]:
            decision_data = {
                "decision_id": m.get("memory_id", ""),
                "title": m.get("title", ""),
                "content": m.get("content", "")[:300],
            }
            # 查找对应的 DecisionRecord 获取 status/outcome
            try:
                dec_res = await self.db.execute(
                    select(DecisionRecord).where(DecisionRecord.id == decision_data["decision_id"])
                )
                dec = dec_res.scalar_one_or_none()
                if dec:
                    decision_data["status"] = dec.status or "open"
                    decision_data["outcome"] = (dec.actual_outcome or dec.expected_outcome or "")[:200]
                else:
                    decision_data["status"] = "unknown"
                    decision_data["outcome"] = ""
            except Exception:
                decision_data["status"] = "unknown"
                decision_data["outcome"] = ""

            # LLM 总结 lesson
            lesson = ""
            try:
                lesson_prompt = build_similar_decision_lesson_prompt(decision_data, question)
                lesson = await ModelGateway(provider).generate_text(lesson_prompt, temperature=0.3, max_tokens=200, prompt_id="similar-decision-lesson", prompt_version="v1")
                lesson = (lesson or "").strip()[:100]
            except Exception:
                lesson = f"历史决策: {decision_data['title']}"

            results.append({
                "decision_id": decision_data["decision_id"],
                "title": decision_data["title"],
                "status": decision_data["status"],
                "outcome": decision_data["outcome"],
                "similarity": float(m.get("score", 0.0)) if m.get("score") else 0.5,
                "lesson": lesson,
            })

        return results
