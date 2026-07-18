"""Daily Briefing v2.1 — 一句话日报。

Phase 4 / Sprint 3 的"日均使用 ≥ 3"入口。

输出结构(故意做小, 保证 30 秒可读完):
{
  "headline": "今天最值得推进的一件事",
  "open_decision": {...},          # 1 个
  "old_conflict": {...},            # 1 个
  "echo_principle": {...},          # 1 条 30 天前的 principle
  "suggested_next_step": "...",     # 1 句话
  "generated_at": iso8601,
}
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


_MODE_DECISION = {"要不要", "该不该", "应不应该", "还是", "能否"}
_MODE_RECALL = {"上次", "以前", "过去", "曾经", "当年"}
_MODE_REVIEW = {"对不对", "对吗", "错没错", "复盘", "回顾"}
_MODE_PLANNING = {"接下来", "下一步", "怎么走", "怎么开始", "计划"}
_MODE_REFLECTION = {"为什么总是", "我是不是", "最近模式", "我的习惯"}


def classify_mode(question: str) -> str:
    """根据关键词自动推断 advisor 模式, 解决"用户懒得切换模式"问题。"""
    if not question:
        return "decision"
    q = question.strip()
    for kw in _MODE_PLANNING:
        if kw in q:
            return "planning"
    for kw in _MODE_REFLECTION:
        if kw in q:
            return "reflection"
    for kw in _MODE_REVIEW:
        if kw in q:
            return "review"
    for kw in _MODE_RECALL:
        if kw in q:
            return "recall"
    for kw in _MODE_DECISION:
        if kw in q:
            return "decision"
    return "decision"


async def build_daily_briefing(
    db: AsyncSession,
    user_id: str,
    *,
    principle_echo_days: int = 30,
) -> Dict:
    """组装今天的一行日报。"""
    open_decision = await _latest_open_decision(db, user_id)
    old_conflict = await _oldest_unresolved_conflict(db, user_id)
    echo = await _echo_principle(db, user_id, principle_echo_days)
    next_step = await _suggest_next_step(db, user_id, open_decision)

    headline = _compose_headline(open_decision, old_conflict, echo)

    return {
        "headline": headline,
        "open_decision": open_decision,
        "old_conflict": old_conflict,
        "echo_principle": echo,
        "suggested_next_step": next_step,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def _compose_headline(open_decision, old_conflict, echo) -> str:
    if open_decision:
        return f"今天推进: {open_decision.get('title', '(未命名决策)')}"
    if old_conflict:
        return f"今天清账: 一个未处理冲突({old_conflict.get('severity', 'low')})"
    if echo:
        return f"今天回想: {echo.get('title', '(未命名原则)')}"
    return "今天没有特别需要推进的事"


async def _latest_open_decision(db: AsyncSession, user_id: str) -> Optional[Dict]:
    try:
        from src.cognition.models.decision_record import DecisionRecord

        stmt = (
            select(DecisionRecord)
            .where(DecisionRecord.user_id == user_id, DecisionRecord.status == "open")
            .order_by(DecisionRecord.decided_at.desc())
            .limit(1)
        )
        rec = (await db.execute(stmt)).scalar_one_or_none()
        if not rec:
            return None
        return {
            "id": rec.id,
            "title": rec.title,
            "decided_at": rec.decided_at.isoformat() if rec.decided_at else None,
            "project_id": rec.project_id,
        }
    except Exception as e:
        logger.debug(f"_latest_open_decision: {e}")
        return None


async def _oldest_unresolved_conflict(db: AsyncSession, user_id: str) -> Optional[Dict]:
    try:
        from src.cognition.models.conflict_record import ConflictRecord

        stmt = (
            select(ConflictRecord)
            .where(
                ConflictRecord.user_id == user_id,
                ConflictRecord.status == "open",
            )
            .order_by(ConflictRecord.created_at.asc())
            .limit(1)
        )
        r = (await db.execute(stmt)).scalar_one_or_none()
        if not r:
            return None
        return {
            "id": r.id,
            "severity": r.severity,
            "conflict_type": r.conflict_type,
            "interpretation": r.interpretation,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
    except Exception as e:
        logger.debug(f"_oldest_unresolved_conflict: {e}")
        return None


async def _echo_principle(db: AsyncSession, user_id: str, days: int) -> Optional[Dict]:
    try:
        from src.memory.models.committed_memory import CommittedMemory, CommittedStatus
        from src.memory.models.memory_type import MemoryType

        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        stmt = (
            select(CommittedMemory)
            .where(
                CommittedMemory.user_id == user_id,
                CommittedMemory.status == CommittedStatus.ACTIVE,
                CommittedMemory.memory_type == MemoryType.PRINCIPLE,
                CommittedMemory.created_at <= cutoff,
            )
            .order_by(CommittedMemory.created_at.asc())
            .limit(1)
        )
        m = (await db.execute(stmt)).scalar_one_or_none()
        if not m:
            return None
        return {
            "id": m.id,
            "title": m.title,
            "body": m.body[:200],
            "created_at": m.created_at.isoformat() if m.created_at else None,
        }
    except Exception as e:
        logger.debug(f"_echo_principle: {e}")
        return None


async def _suggest_next_step(db: AsyncSession, user_id: str, open_decision: Optional[Dict]) -> str:
    if not open_decision:
        return "可以打开 advisor, 用一句话问今天该做什么。"
    return f"对决策 '{open_decision.get('title', '')}', 先给它一个 ETA 或拆 1 个下一步。"
