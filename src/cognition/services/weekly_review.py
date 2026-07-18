"""Weekly Review (Gen 2).

周期性任务: 一周聚合本周新记忆 + 决策 + 模式, 生成结构化周报。

- 默认 week_start = 上一个周一 (datetime.now() - timedelta(days=now.weekday() + 7))
- 查询本周 [week_start, week_start+6] 窗口:
    * 新建的 CommittedMemory (按 created_at)
    * 新建的 DecisionRecord (按 decided_at)
    * 状态变为 resolved 的 DecisionRecord (按 resolved_at)
- LLM 输出严格 JSON: highlights / open_questions / summary / *_count
- 解析失败 -> summary 写 "周报生成失败: {msg}", 其余字段留空数组
- 默认 dry_run=True 不持久化, dry_run=False 才入库
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.shared.llm.providers import get_llm_provider
from src.shared.llm.model_gateway import ModelGateway
from src.memory.models.committed_memory import CommittedMemory
from src.cognition.models.decision_record import DecisionRecord
from src.cognition.models.weekly_review import WeeklyReview
from src.shared.ids.id_generator import generate_weekly_review_id
from src.cognition.prompts.weekly_review import build_weekly_review_prompt


def _last_monday(now: Optional[datetime] = None) -> datetime:
    """上一个周一 00:00 UTC。"""
    now = now or datetime.now(timezone.utc)
    days_back = now.weekday() + 7
    monday = (now - timedelta(days=days_back)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return monday


def _monday_of_this_week(now: Optional[datetime] = None) -> datetime:
    now = now or datetime.now(timezone.utc)
    monday = (now - timedelta(days=now.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return monday


def _to_str_list(val) -> List[str]:
    """将值安全地转换为字符串列表。"""
    if not val or not isinstance(val, list):
        return []
    return [str(v) for v in val if v is not None]


class WeeklyReviewService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def generate(
        self,
        user_id: str,
        *,
        week_start: Optional[str] = None,
        dry_run: bool = True,
    ) -> Dict:
        if week_start:
            try:
                start_dt = datetime.strptime(week_start, "%Y-%m-%d").replace(
                    tzinfo=timezone.utc
                )
            except ValueError as e:
                raise ValueError(f"week_start must be YYYY-MM-DD: {e}")
        else:
            start_dt = _last_monday()

        end_dt = start_dt + timedelta(days=6, hours=23, minutes=59, seconds=59)
        week_end_str = end_dt.strftime("%Y-%m-%d")
        week_start_str = start_dt.strftime("%Y-%m-%d")

        memories = await self._collect_memories(user_id, start_dt, end_dt)
        new_decisions = await self._collect_new_decisions(user_id, start_dt, end_dt)
        resolved_decisions = await self._collect_resolved_decisions(
            user_id, start_dt, end_dt
        )

        prompt = self._build_prompt(
            week_start_str=week_start_str,
            week_end_str=week_end_str,
            memories=memories,
            new_decisions=new_decisions,
            resolved_decisions=resolved_decisions,
        )

        summary_text = ""
        key_decisions: List[Dict] = []
        important_insights: List[str] = []
        repeated_themes: List[str] = []
        conflicts_or_changes: List[Dict] = []
        risks_to_watch: List[str] = []
        suggested_focus_next_week: List[str] = []
        persona_observations: List[str] = []
        open_loops: List[str] = []
        cited_memories: List[str] = []
        cited_decisions: List[str] = []
        highlights: List[str] = []
        open_questions: List[str] = []
        warnings: List[str] = []

        try:
            raw = await ModelGateway(get_llm_provider()).generate_text(
                prompt,
                temperature=0.3,
                max_tokens=2000,
                prompt_id="weekly-review",
                prompt_version="v1",
            )
            if not isinstance(raw, str):
                raw = str(raw)
            data = self._parse_json_payload(raw)

            summary_text = str(data.get("summary", "")).strip()

            # 8 段输出解析
            kd = data.get("key_decisions") or []
            key_decisions = kd if isinstance(kd, list) else []
            important_insights = _to_str_list(data.get("important_insights"))
            repeated_themes = _to_str_list(data.get("repeated_themes"))
            cc = data.get("conflicts_or_changes") or []
            conflicts_or_changes = cc if isinstance(cc, list) else []
            risks_to_watch = _to_str_list(data.get("risks_to_watch"))
            suggested_focus_next_week = _to_str_list(data.get("suggested_focus_next_week"))
            persona_observations = _to_str_list(data.get("persona_observations"))
            open_loops = _to_str_list(data.get("open_loops"))
            cited_memories = _to_str_list(data.get("cited_memories"))
            cited_decisions = _to_str_list(data.get("cited_decisions"))

            # 向后兼容旧格式
            highlights = _to_str_list(data.get("highlights"))
            open_questions = _to_str_list(data.get("open_questions"))

            # 如果新字段为空但旧字段有值，用旧字段填充
            if not important_insights and highlights:
                important_insights = highlights
            if not open_loops and open_questions:
                open_loops = open_questions

        except Exception as e:
            warnings.append(f"llm_error: {e}")
            summary_text = f"周报生成失败: {e}"

        word_count = len(summary_text)

        new_memories_refs = [
            {"id": m.id, "title": m.title, "memory_type": m.memory_type.value if hasattr(m.memory_type, "value") else str(m.memory_type)}
            for m in memories
        ]
        decision_refs = [
            {
                "id": d.id,
                "title": d.title,
                "status": d.status,
                "decided_at": d.decided_at.isoformat() if d.decided_at else None,
            }
            for d in (new_decisions + resolved_decisions)
        ]

        result: Dict = {
            "user_id": user_id,
            "week_start": week_start_str,
            "week_end": week_end_str,
            "new_memories": new_memories_refs,
            "decisions": decision_refs,
            "summary": summary_text,
            "key_decisions": key_decisions,
            "important_insights": important_insights,
            "repeated_themes": repeated_themes,
            "conflicts_or_changes": conflicts_or_changes,
            "risks_to_watch": risks_to_watch,
            "suggested_focus_next_week": suggested_focus_next_week,
            "persona_observations": persona_observations,
            "open_loops": open_loops,
            "cited_memories": cited_memories,
            "cited_decisions": cited_decisions,
            "highlights": highlights,
            "open_questions": open_questions,
            "word_count": word_count,
            "new_memories_count": len(new_memories_refs),
            "decisions_count": len(decision_refs),
            "warnings": warnings,
            "dry_run": dry_run,
        }

        if not dry_run:
            review_id = generate_weekly_review_id()
            review = WeeklyReview(
                id=review_id,
                user_id=user_id,
                week_start=week_start_str,
                week_end=week_end_str,
                new_memories_json=json.dumps(new_memories_refs, ensure_ascii=False),
                decisions_json=json.dumps(decision_refs, ensure_ascii=False),
                highlights_json=json.dumps(important_insights, ensure_ascii=False),
                open_questions_json=json.dumps(open_loops, ensure_ascii=False),
                summary=summary_text,
                word_count=word_count,
                persona_observations_json=json.dumps(persona_observations, ensure_ascii=False),
                open_loops_json=json.dumps(open_loops, ensure_ascii=False),
                risks_to_watch_json=json.dumps(risks_to_watch, ensure_ascii=False),
                suggested_focus_json=json.dumps(suggested_focus_next_week, ensure_ascii=False),
                created_at=datetime.now(timezone.utc),
            )
            self.db.add(review)
            try:
                await self.db.commit()
                await self.db.refresh(review)
                result["id"] = review.id
                result["persisted"] = True
            except Exception as e:
                await self.db.rollback()
                warnings.append(f"persist_error: {e}")
                result["persisted"] = False
        else:
            result["persisted"] = False

        return result

    async def latest(self, user_id: str) -> Optional[WeeklyReview]:
        result = await self.db.execute(
            select(WeeklyReview)
            .where(WeeklyReview.user_id == user_id)
            .order_by(WeeklyReview.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def history(self, user_id: str, *, limit: int = 12) -> List[WeeklyReview]:
        result = await self.db.execute(
            select(WeeklyReview)
            .where(WeeklyReview.user_id == user_id)
            .order_by(WeeklyReview.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def _collect_memories(
        self, user_id: str, start_dt: datetime, end_dt: datetime
    ) -> List[CommittedMemory]:
        result = await self.db.execute(
            select(CommittedMemory)
            .where(
                and_(
                    CommittedMemory.user_id == user_id,
                    CommittedMemory.created_at >= start_dt,
                    CommittedMemory.created_at <= end_dt,
                )
            )
            .order_by(CommittedMemory.created_at.desc())
            .limit(50)
        )
        return list(result.scalars().all())

    async def _collect_new_decisions(
        self, user_id: str, start_dt: datetime, end_dt: datetime
    ) -> List[DecisionRecord]:
        result = await self.db.execute(
            select(DecisionRecord)
            .where(
                and_(
                    DecisionRecord.user_id == user_id,
                    DecisionRecord.decided_at >= start_dt,
                    DecisionRecord.decided_at <= end_dt,
                )
            )
            .order_by(DecisionRecord.decided_at.desc())
            .limit(50)
        )
        return list(result.scalars().all())

    async def _collect_resolved_decisions(
        self, user_id: str, start_dt: datetime, end_dt: datetime
    ) -> List[DecisionRecord]:
        result = await self.db.execute(
            select(DecisionRecord)
            .where(
                and_(
                    DecisionRecord.user_id == user_id,
                    DecisionRecord.resolved_at != None,  # noqa: E711
                    DecisionRecord.resolved_at >= start_dt,
                    DecisionRecord.resolved_at <= end_dt,
                )
            )
            .order_by(DecisionRecord.resolved_at.desc())
            .limit(50)
        )
        return list(result.scalars().all())

    def _build_prompt(
        self,
        *,
        week_start_str: str,
        week_end_str: str,
        memories: List[CommittedMemory],
        new_decisions: List[DecisionRecord],
        resolved_decisions: List[DecisionRecord],
    ) -> str:
        memory_lines = []
        for i, m in enumerate(memories[:30]):
            mtype = m.memory_type.value if hasattr(m.memory_type, "value") else str(m.memory_type)
            memory_lines.append(
                f"[{i+1}] ({mtype}) id={m.id} 标题={m.title} 内容={m.body[:200]}"
            )
        memory_block = "\n".join(memory_lines) if memory_lines else "（本周无新记忆）"

        new_lines = []
        for d in new_decisions[:30]:
            new_lines.append(
                f"- id={d.id} 标题={d.title} 状态={d.status} 决定={d.decision} 理由={d.rationale}"
            )
        new_block = "\n".join(new_lines) if new_lines else "- （本周无新建决策）"

        resolved_lines = []
        for d in resolved_decisions[:30]:
            resolved_lines.append(
                f"- id={d.id} 标题={d.title} 结果={d.actual_outcome}"
            )
        resolved_block = "\n".join(resolved_lines) if resolved_lines else "- （本周无已结决策）"

        prompt = build_weekly_review_prompt(
            week_start_str=week_start_str,
            week_end_str=week_end_str,
            memory_block=memory_block,
            new_block=new_block,
            resolved_block=resolved_block,
            new_memories_count=len(memories),
            decisions_count=len(new_decisions) + len(resolved_decisions),
        )
        return prompt

    def _parse_json_payload(self, raw: str) -> Dict:
        if not raw:
            return {}
        text = raw.strip()
        if text.startswith("```"):
            text = text.strip("`")
            if text.startswith("json"):
                text = text[4:]
            text = text.strip()
        try:
            return json.loads(text)
        except Exception:
            start = text.find("{")
            end = text.rfind("}")
            if start >= 0 and end > start:
                return json.loads(text[start:end + 1])
            return {}
