"""Life Timeline (Gen 3 / Cognitive OS).

按时间聚合用户的:
- CommittedMemory     -> entry_kind=memory
- DecisionRecord      -> entry_kind=decision
- LifeTask            -> entry_kind=task
- WeeklyReview        -> entry_kind=review

写入 `life_timeline_entries` 表 (append-only)。
多次 rebuild 会写入重复 entries, 通过 UNIQUE(ref_id, entry_kind) 约束在写入前去重。

get_timeline 只读, 直接从 LifeTimelineEntry 表读, 不必每次都 rebuild。

高级视图:
- get_decision_chains   : 决策链 (决策间因果关系)
- get_project_evolution : 项目演化 (里程碑 + 阶段 + 健康度)
- get_cognitive_shifts  : 认知变化 (观点/偏好转变)
- get_behavior_trends   : 行为趋势 (纯统计)
"""
from __future__ import annotations

import json
import logging
from collections import Counter, OrderedDict
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.shared.llm.providers import get_llm_provider
from src.shared.llm.model_gateway import ModelGateway
from src.memory.models.committed_memory import CommittedMemory, CommittedStatus
from src.cognition.models.decision_record import DecisionRecord
from src.execution.models.life_task import LifeTask
from src.execution.models.life_timeline_entry import LifeTimelineEntry
from src.cognition.models.weekly_review import WeeklyReview
from src.execution.prompts.simulation import (
    build_cognitive_shift_prompt,
    build_decision_chain_prompt,
    build_project_evolution_prompt,
)
from src.execution.schemas.os import VALID_TIMELINE_KINDS
from src.shared.ids.id_generator import generate_timeline_entry_id

logger = logging.getLogger(__name__)

SNIPPET_MAX_LEN = 200


def _trunc(text: Optional[str], limit: int = SNIPPET_MAX_LEN) -> str:
    if not text:
        return ""
    s = str(text).strip()
    if len(s) <= limit:
        return s
    return s[: limit - 1] + "…"


def _date_str(value) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, str):
        return value[:10]
    try:
        return value.strftime("%Y-%m-%d")
    except Exception:
        return None


def _entry_to_dict(e: LifeTimelineEntry) -> dict:
    return {
        "id": e.id,
        "user_id": e.user_id,
        "entry_date": e.entry_date,
        "entry_kind": e.entry_kind,
        "ref_id": e.ref_id,
        "title": e.title,
        "snippet": e.snippet,
        "importance": float(e.importance or 0.0),
        "project_id": e.project_id,
        "created_at": e.created_at,
    }


class LifeTimeline:
    def __init__(self, db: AsyncSession):
        self.db = db

    # ------------------------------------------------------------------ rebuild

    async def rebuild(
        self,
        user_id: str,
        *,
        since_date: Optional[str] = None,
        until_date: Optional[str] = None,
    ) -> Dict:
        """从源头重建时间线 entries。
        - 按 (ref_id, entry_kind) 去重, 已存在则跳过。
        - 返回聚合 dict: entry_count, by_date, highlights。
        """
        sources = await self._collect_sources(
            user_id, since_date=since_date, until_date=until_date
        )

        existing_pairs = await self._existing_pairs(user_id)
        now = datetime.now(timezone.utc)
        new_entries: List[LifeTimelineEntry] = []
        for src in sources:
            kind = src["entry_kind"]
            ref_id = src["ref_id"]
            if (ref_id, kind) in existing_pairs:
                continue
            entry = LifeTimelineEntry(
                id=generate_timeline_entry_id(),
                user_id=user_id,
                entry_date=src["entry_date"],
                entry_kind=kind,
                ref_id=ref_id,
                title=src["title"],
                snippet=src["snippet"],
                importance=src["importance"],
                project_id=src.get("project_id"),
                created_at=now,
            )
            self.db.add(entry)
            new_entries.append(entry)
        try:
            await self.db.commit()
        except Exception as e:
            await self.db.rollback()
            logger.warning("rebuild commit failed: %s", e)

        all_entries = await self._load_entries(
            user_id, since_date=since_date, until_date=until_date
        )

        by_date: "OrderedDict[str, List[dict]]" = OrderedDict()
        for e in all_entries:
            by_date.setdefault(e.entry_date, []).append(_entry_to_dict(e))

        highlights_sorted = sorted(
            all_entries, key=lambda x: (float(x.importance or 0.0), x.entry_date),
            reverse=True,
        )[:20]
        return {
            "user_id": user_id,
            "entry_count": len(all_entries),
            "by_date": {d: lst for d, lst in by_date.items()},
            "highlights": [_entry_to_dict(e) for e in highlights_sorted],
        }

    # ------------------------------------------------------------------ get

    async def get_timeline(
        self,
        user_id: str,
        *,
        since_date: Optional[str] = None,
        until_date: Optional[str] = None,
        kind: Optional[str] = None,
        project_id: Optional[str] = None,
        limit: int = 200,
    ) -> List[Dict]:
        if kind and kind not in VALID_TIMELINE_KINDS:
            raise ValueError(f"invalid kind: {kind}")

        entries = await self._load_entries(
            user_id, since_date=since_date, until_date=until_date,
            kind=kind, project_id=project_id, limit=limit,
        )
        return [_entry_to_dict(e) for e in entries]

    # ============================================================ advanced views

    # ------------------------------------------------------------------ decision chains

    async def get_decision_chains(
        self, user_id: str, *, project_id: Optional[str] = None, limit: int = 20
    ) -> List[Dict]:
        """返回决策链: 决策之间的因果关系。"""
        filters = [DecisionRecord.user_id == user_id]
        if project_id:
            filters.append(DecisionRecord.project_id == project_id)

        result = await self.db.execute(
            select(DecisionRecord)
            .where(and_(*filters))
            .order_by(DecisionRecord.decided_at.asc())
            .limit(max(1, min(100, int(limit))))
        )
        decisions = list(result.scalars().all())
        if not decisions:
            return []

        decisions_list = [
            {
                "decision_id": d.id,
                "title": d.title or "",
                "status": d.status or "open",
                "decided_at": _date_str(d.decided_at) or "",
                "outcome": (d.actual_outcome or d.expected_outcome or "")[:200],
            }
            for d in decisions
        ]

        # LLM 分析
        try:
            provider = get_llm_provider()
            prompt = build_decision_chain_prompt(decisions_list)
            response = await ModelGateway(provider).generate_text(prompt, temperature=0.3, max_tokens=2000, prompt_id="decision-chain", prompt_version="v1")
            chains = _parse_json_list(response)
            # 为每条链生成唯一 ID
            for i, chain in enumerate(chains):
                if not chain.get("chain_id"):
                    chain["chain_id"] = f"chain_{i+1:03d}"
            return chains
        except Exception as e:
            logger.warning("get_decision_chains LLM failed: %s", e)

        # 降级: 按时间顺序简单串联
        return [
            {
                "chain_id": "chain_001",
                "decisions": decisions_list,
                "pattern": "progressive_refinement",
                "summary": f"共 {len(decisions_list)} 条决策, 按时间顺序排列 (降级模式)。",
            }
        ]

    # ------------------------------------------------------------------ project evolution

    async def get_project_evolution(
        self, user_id: str, *, project_id: Optional[str] = None, limit: int = 20
    ) -> List[Dict]:
        """返回项目演化: 项目随时间的里程碑和阶段变化。"""
        proj_ids: List[str] = []
        if project_id:
            proj_ids = [project_id]
        else:
            # 发现所有 project_id
            mem_res = await self.db.execute(
                select(CommittedMemory.project_id)
                .where(
                    CommittedMemory.user_id == user_id,
                    CommittedMemory.status == CommittedStatus.ACTIVE,
                    CommittedMemory.project_id.isnot(None),
                )
                .distinct()
                .limit(50)
            )
            proj_ids = [row[0] for row in mem_res.all() if row[0]]

        results: List[Dict] = []
        for pid in proj_ids[: max(1, min(20, int(limit)))]:
            events: List[Dict] = []

            # memory
            mem_res = await self.db.execute(
                select(CommittedMemory)
                .where(
                    CommittedMemory.user_id == user_id,
                    CommittedMemory.project_id == pid,
                    CommittedMemory.status == CommittedStatus.ACTIVE,
                )
                .order_by(CommittedMemory.created_at.asc())
                .limit(200)
            )
            for m in mem_res.scalars().all():
                d = _date_str(m.created_at)
                if d:
                    events.append({
                        "date": d,
                        "title": m.title or "未命名记忆",
                        "kind": "memory",
                        "importance": float(m.importance or 0.5),
                        "ref_id": m.id,
                    })

            # decision
            dec_res = await self.db.execute(
                select(DecisionRecord)
                .where(
                    DecisionRecord.user_id == user_id,
                    DecisionRecord.project_id == pid,
                )
                .order_by(DecisionRecord.decided_at.asc())
                .limit(200)
            )
            for d in dec_res.scalars().all():
                dt = _date_str(d.decided_at)
                if dt:
                    events.append({
                        "date": dt,
                        "title": d.title or "未命名决策",
                        "kind": "decision",
                        "importance": 0.7,
                        "ref_id": d.id,
                        "outcome": (d.actual_outcome or d.expected_outcome or "")[:150],
                    })

            # task
            task_res = await self.db.execute(
                select(LifeTask)
                .where(
                    LifeTask.user_id == user_id,
                    LifeTask.project_id == pid,
                )
                .order_by(LifeTask.created_at.asc())
                .limit(200)
            )
            for t in task_res.scalars().all():
                dt = _date_str(t.created_at)
                if dt:
                    events.append({
                        "date": dt,
                        "title": t.title or "未命名任务",
                        "kind": "task",
                        "importance": 0.5,
                        "ref_id": t.id,
                        "status": t.status or "",
                    })

            events.sort(key=lambda e: e["date"])

            # LLM 分析
            try:
                provider = get_llm_provider()
                prompt = build_project_evolution_prompt(events)
                response = await ModelGateway(provider).generate_text(prompt, temperature=0.3, max_tokens=1500, prompt_id="project-evolution", prompt_version="v1")
                parsed = _parse_json_object(response)
                results.append({
                    "project_id": pid,
                    "milestones": parsed.get("milestones", []),
                    "current_phase": parsed.get("current_phase", "未知"),
                    "health_score": float(parsed.get("health_score", 0.5)),
                    "summary": parsed.get("summary", ""),
                })
                continue
            except Exception as e:
                logger.warning("get_project_evolution LLM failed for %s: %s", pid, e)

            # 降级: 返回时间排序的事件列表
            results.append({
                "project_id": pid,
                "milestones": [
                    {
                        "date": ev["date"],
                        "title": ev["title"],
                        "kind": ev["kind"],
                        "significance": "中",
                    }
                    for ev in events
                ],
                "current_phase": "未知",
                "health_score": 0.5,
                "summary": f"项目 {pid} 共 {len(events)} 条事件 (降级模式)。",
            })

        return results

    # ------------------------------------------------------------------ cognitive shifts

    async def get_cognitive_shifts(
        self, user_id: str, *, days: int = 90, limit: int = 20
    ) -> List[Dict]:
        """返回认知变化: 用户观点/偏好的转变。"""
        now = datetime.now(timezone.utc)
        since = now - timedelta(days=max(1, min(365, int(days))))
        half = since + (now - since) / 2

        # 前半段记忆
        before_res = await self.db.execute(
            select(CommittedMemory)
            .where(
                CommittedMemory.user_id == user_id,
                CommittedMemory.status == CommittedStatus.ACTIVE,
                CommittedMemory.created_at >= since,
                CommittedMemory.created_at < half,
            )
            .order_by(CommittedMemory.created_at.asc())
            .limit(100)
        )
        before_memories = [
            {
                "id": m.id,
                "title": m.title or "",
                "body": (m.body or "")[:200],
                "tags": m.tags or [],
                "created_at": _date_str(m.created_at) or "",
            }
            for m in before_res.scalars().all()
        ]

        # 后半段记忆
        after_res = await self.db.execute(
            select(CommittedMemory)
            .where(
                CommittedMemory.user_id == user_id,
                CommittedMemory.status == CommittedStatus.ACTIVE,
                CommittedMemory.created_at >= half,
            )
            .order_by(CommittedMemory.created_at.asc())
            .limit(100)
        )
        after_memories = [
            {
                "id": m.id,
                "title": m.title or "",
                "body": (m.body or "")[:200],
                "tags": m.tags or [],
                "created_at": _date_str(m.created_at) or "",
            }
            for m in after_res.scalars().all()
        ]

        if not before_memories or not after_memories:
            return []

        # LLM 分析
        try:
            provider = get_llm_provider()
            prompt = build_cognitive_shift_prompt(before_memories, after_memories)
            response = await ModelGateway(provider).generate_text(prompt, temperature=0.3, max_tokens=2000, prompt_id="cognitive-shift", prompt_version="v1")
            shifts = _parse_json_list(response)
            for i, s in enumerate(shifts):
                if not s.get("shift_id"):
                    s["shift_id"] = f"shift_{i+1:03d}"
                if not s.get("detected_at"):
                    s["detected_at"] = _date_str(now) or ""
            return shifts[: max(1, min(20, int(limit)))]
        except Exception as e:
            logger.warning("get_cognitive_shifts LLM failed: %s", e)

        # 降级: 返回空列表
        return []

    # ------------------------------------------------------------------ behavior trends

    async def get_behavior_trends(
        self, user_id: str, *, days: int = 90
    ) -> Dict:
        """返回行为趋势: 纯 SQL 统计, 不需要 LLM。"""
        now = datetime.now(timezone.utc)
        since = now - timedelta(days=max(1, min(365, int(days))))
        period_from = _date_str(since) or ""
        period_to = _date_str(now) or ""
        actual_days = max(1, (now - since).days)

        # 1. 记忆创建频率
        mem_count_res = await self.db.execute(
            select(
                func.date(CommittedMemory.created_at).label("d"),
                func.count().label("cnt"),
            )
            .where(
                CommittedMemory.user_id == user_id,
                CommittedMemory.status == CommittedStatus.ACTIVE,
                CommittedMemory.created_at >= since,
            )
            .group_by(func.date(CommittedMemory.created_at))
        )
        daily_counts = dict(mem_count_res.all())
        total_mem = sum(daily_counts.values())
        daily_avg = round(total_mem / actual_days, 2) if actual_days > 0 else 0.0

        # 趋势: 前半段 vs 后半段
        half = since + (now - since) / 2
        first_half_count = sum(v for d, v in daily_counts.items() if _parse_date(str(d)) < half)
        second_half_count = sum(v for d, v in daily_counts.items() if _parse_date(str(d)) >= half)
        half_days = max(1, actual_days // 2)
        first_rate = first_half_count / half_days
        second_rate = second_half_count / half_days
        if second_rate > first_rate * 1.2:
            mem_trend = "increasing"
        elif second_rate < first_rate * 0.8:
            mem_trend = "decreasing"
        else:
            mem_trend = "stable"

        # 2. 任务完成率
        task_total_res = await self.db.execute(
            select(func.count())
            .select_from(LifeTask)
            .where(
                LifeTask.user_id == user_id,
                LifeTask.created_at >= since,
            )
        )
        task_total = task_total_res.scalar() or 0

        task_done_res = await self.db.execute(
            select(func.count())
            .select_from(LifeTask)
            .where(
                LifeTask.user_id == user_id,
                LifeTask.created_at >= since,
                LifeTask.status == "done",
            )
        )
        task_done = task_done_res.scalar() or 0
        task_rate = round(task_done / task_total, 4) if task_total > 0 else 0.0

        # 3. 决策频率
        dec_total_res = await self.db.execute(
            select(func.count())
            .select_from(DecisionRecord)
            .where(
                DecisionRecord.user_id == user_id,
                DecisionRecord.decided_at >= since,
            )
        )
        dec_total = dec_total_res.scalar() or 0
        dec_per_week = round(dec_total / max(1, actual_days / 7), 2)

        # 4. 活跃小时
        mem_hours_res = await self.db.execute(
            select(
                func.extract("hour", CommittedMemory.created_at).label("h"),
                func.count().label("cnt"),
            )
            .where(
                CommittedMemory.user_id == user_id,
                CommittedMemory.status == CommittedStatus.ACTIVE,
                CommittedMemory.created_at >= since,
            )
            .group_by(func.extract("hour", CommittedMemory.created_at))
            .order_by(func.count().desc())
            .limit(5)
        )
        most_active_hours = [int(row[0]) for row in mem_hours_res.all() if row[0] is not None]

        # 5. 高频标签
        mem_tags_res = await self.db.execute(
            select(CommittedMemory.tags)
            .where(
                CommittedMemory.user_id == user_id,
                CommittedMemory.status == CommittedStatus.ACTIVE,
                CommittedMemory.created_at >= since,
                CommittedMemory.tags.isnot(None),
            )
            .limit(500)
        )
        tag_counter: Counter = Counter()
        for (tags_json,) in mem_tags_res.all():
            if isinstance(tags_json, str):
                try:
                    tags_json = json.loads(tags_json)
                except Exception:
                    continue
            if isinstance(tags_json, list):
                for t in tags_json:
                    tag_counter[str(t).strip().lower()] += 1
        top_topics = [t for t, _ in tag_counter.most_common(10)]

        # 6. 一句话总结
        parts = []
        parts.append(f"{actual_days}天内共创建 {total_mem} 条记忆")
        if mem_trend == "increasing":
            parts.append("记忆创建频率上升")
        elif mem_trend == "decreasing":
            parts.append("记忆创建频率下降")
        else:
            parts.append("记忆创建频率稳定")
        if task_total > 0:
            parts.append(f"任务完成率 {task_done}/{task_total} ({task_rate:.0%})")
        parts.append(f"决策 {dec_total} 次 (每周 {dec_per_week} 次)")
        insight = ", ".join(parts) + "。"

        return {
            "period": {"from": period_from, "to": period_to},
            "memory_creation_rate": {"daily_avg": daily_avg, "trend": mem_trend},
            "task_completion_rate": {
                "completed": task_done,
                "total": task_total,
                "rate": task_rate,
            },
            "decision_frequency": {"total": dec_total, "per_week": dec_per_week},
            "most_active_hours": most_active_hours,
            "top_topics": top_topics,
            "insight": insight,
        }

    # ------------------------------------------------------------------ helpers

    async def _existing_pairs(self, user_id: str) -> set:
        result = await self.db.execute(
            select(LifeTimelineEntry.ref_id, LifeTimelineEntry.entry_kind).where(
                LifeTimelineEntry.user_id == user_id
            )
        )
        return {(row[0], row[1]) for row in result.all()}

    async def _load_entries(
        self,
        user_id: str,
        *,
        since_date: Optional[str] = None,
        until_date: Optional[str] = None,
        kind: Optional[str] = None,
        project_id: Optional[str] = None,
        limit: int = 200,
    ) -> List[LifeTimelineEntry]:
        filters = [LifeTimelineEntry.user_id == user_id]
        if since_date:
            filters.append(LifeTimelineEntry.entry_date >= since_date)
        if until_date:
            filters.append(LifeTimelineEntry.entry_date <= until_date)
        if kind:
            filters.append(LifeTimelineEntry.entry_kind == kind)
        if project_id:
            filters.append(LifeTimelineEntry.project_id == project_id)

        result = await self.db.execute(
            select(LifeTimelineEntry)
            .where(and_(*filters))
            .order_by(LifeTimelineEntry.entry_date.desc(), LifeTimelineEntry.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def _collect_sources(
        self,
        user_id: str,
        *,
        since_date: Optional[str] = None,
        until_date: Optional[str] = None,
    ) -> List[Dict]:
        sources: List[Dict] = []

        # memory
        mem_filters = [
            CommittedMemory.user_id == user_id,
            CommittedMemory.status == CommittedStatus.ACTIVE,
        ]
        if since_date:
            mem_filters.append(CommittedMemory.created_at >= _parse_date(since_date))
        if until_date:
            mem_filters.append(CommittedMemory.created_at <= _end_of_day(until_date))
        mem_res = await self.db.execute(
            select(CommittedMemory)
            .where(and_(*mem_filters))
            .order_by(CommittedMemory.created_at.desc())
            .limit(500)
        )
        for m in mem_res.scalars().all():
            d = _date_str(m.created_at)
            if not d:
                continue
            sources.append({
                "entry_kind": "memory",
                "ref_id": m.id,
                "title": m.title or "未命名记忆",
                "snippet": _trunc(m.body),
                "importance": float(m.importance or 0.5),
                "project_id": m.project_id,
                "entry_date": d,
            })

        # decision
        dec_filters = [DecisionRecord.user_id == user_id]
        if since_date:
            dec_filters.append(DecisionRecord.decided_at >= _parse_date(since_date))
        if until_date:
            dec_filters.append(DecisionRecord.decided_at <= _end_of_day(until_date))
        dec_res = await self.db.execute(
            select(DecisionRecord)
            .where(and_(*dec_filters))
            .order_by(DecisionRecord.decided_at.desc())
            .limit(500)
        )
        for d in dec_res.scalars().all():
            dt = _date_str(d.decided_at)
            if not dt:
                continue
            sources.append({
                "entry_kind": "decision",
                "ref_id": d.id,
                "title": d.title or "未命名决策",
                "snippet": _trunc(d.decision or d.context or ""),
                "importance": 0.7,
                "project_id": d.project_id,
                "entry_date": dt,
            })

        # task
        task_filters = [LifeTask.user_id == user_id]
        if since_date:
            task_filters.append(LifeTask.created_at >= _parse_date(since_date))
        if until_date:
            task_filters.append(LifeTask.created_at <= _end_of_day(until_date))
        task_res = await self.db.execute(
            select(LifeTask)
            .where(and_(*task_filters))
            .order_by(LifeTask.created_at.desc())
            .limit(500)
        )
        for t in task_res.scalars().all():
            dt = _date_str(t.created_at)
            if not dt:
                continue
            sources.append({
                "entry_kind": "task",
                "ref_id": t.id,
                "title": t.title or "未命名任务",
                "snippet": _trunc(t.description or ""),
                "importance": 0.5,
                "project_id": t.project_id,
                "entry_date": dt,
            })

        # review
        rev_filters = [WeeklyReview.user_id == user_id]
        if since_date:
            rev_filters.append(WeeklyReview.week_start >= since_date)
        if until_date:
            rev_filters.append(WeeklyReview.week_start <= until_date)
        rev_res = await self.db.execute(
            select(WeeklyReview)
            .where(and_(*rev_filters))
            .order_by(WeeklyReview.week_start.desc())
            .limit(200)
        )
        for r in rev_res.scalars().all():
            sources.append({
                "entry_kind": "review",
                "ref_id": r.id,
                "title": f"周报 {r.week_start} ~ {r.week_end}",
                "snippet": _trunc(r.summary or ""),
                "importance": 0.8,
                "project_id": None,
                "entry_date": r.week_start,
            })

        return sources


def _parse_date(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc)


def _end_of_day(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%d").replace(
        hour=23, minute=59, second=59, tzinfo=timezone.utc,
    )


# ------------------------------------------------------------------ JSON 解析辅助


def _parse_json_list(text: str) -> List[Dict]:
    """从 LLM 响应中提取 JSON 数组。"""
    from src.shared.utils.llm_output import extract_json_list
    result = extract_json_list(text)
    return result if result else []


def _parse_json_object(text: str) -> Dict:
    """从 LLM 响应中提取 JSON 对象。"""
    from src.shared.utils.llm_output import extract_json_object
    result = extract_json_object(text)
    return result if result else {}
