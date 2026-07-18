"""Usage Metrics v2.1 — Phase 4 / Sprint 3: 衡量"是否真的成为日常工具"。

关键指标:
- drop_to_committed_seconds: 甩出到入库的秒数(输入摩擦)
- questions_per_day: 每天 advisor 询问次数(使用频度)
- mode_distribution: 5 模式使用分布(Advisor 是否被充分使用)
- daily_active_streak: 连续活跃天数

存储: 写到 ``usage_event`` 表(若不存在, 自动 graceful no-op,
不阻塞主流程)。设计上 Phase 4 不引入新表, 优先复用 ``audit_log``:
- event_type = "usage_drop" / "usage_ask"
- target_id = memory_id 或 advisor_session_id
- detail = {"drop_seconds": ..., "mode": ...}
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Dict, Optional

from sqlalchemy.ext.asyncio import AsyncSession
from src.execution.models.audit_log import AuditLog
from src.execution.services.audit_logger import AuditLogger

logger = logging.getLogger(__name__)


async def record_drop(
    db: AsyncSession,
    user_id: str,
    *,
    memory_id: Optional[str] = None,
    drop_seconds: Optional[float] = None,
    channel: str = "api",
) -> None:
    """记录一次输入事件。失败不阻塞主流程。"""
    try:
        await AuditLogger.log(
            db,
            user_id=user_id,
            action="usage_drop",
            actor_type="user",
            actor_id=user_id,
            target_type="memory" if memory_id else "raw_event",
            target_id=memory_id or "",
            detail={
                "drop_seconds": drop_seconds,
                "channel": channel,
                "ts": datetime.now(timezone.utc).isoformat(),
            },
        )
    except Exception as e:
        logger.debug(f"record_drop failed: {e}")


async def record_ask(
    db: AsyncSession,
    user_id: str,
    *,
    session_id: Optional[str] = None,
    mode: Optional[str] = None,
    confidence: Optional[float] = None,
    adopted: Optional[bool] = None,
) -> None:
    """记录一次 advisor 询问。adopted 由 feedback API 后续更新。"""
    try:
        await AuditLogger.log(
            db,
            user_id=user_id,
            action="usage_ask",
            actor_type="user",
            actor_id=user_id,
            target_type="advisor_session",
            target_id=session_id or "",
            detail={
                "mode": mode,
                "confidence": confidence,
                "adopted": adopted,
                "ts": datetime.now(timezone.utc).isoformat(),
            },
        )
    except Exception as e:
        logger.debug(f"record_ask failed: {e}")


async def get_usage_summary(
    db: AsyncSession,
    user_id: str,
    *,
    days: int = 7,
) -> Dict:
    """聚合用户最近 N 天的 usage metrics, 供 WeeklyReview / dashboard 用。"""
    try:
        from sqlalchemy import select, and_
        from datetime import timedelta

        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        stmt = select(AuditLog).where(
            and_(
                AuditLog.user_id == user_id,
                AuditLog.action.in_(("usage_drop", "usage_ask")),
                AuditLog.created_at >= cutoff,
            )
        )
        rows = (await db.execute(stmt)).scalars().all()

        drops = 0
        asks = 0
        mode_dist: Dict[str, int] = {}
        active_dates: set = set()
        drop_seconds_list: list = []
        for r in rows:
            detail = r.detail or {}
            if isinstance(detail, str):
                import json
                try:
                    detail = json.loads(detail)
                except Exception:
                    detail = {}
            ts = detail.get("ts") or (r.created_at.isoformat() if r.created_at else None)
            if ts:
                active_dates.add(ts[:10])
            if r.action == "usage_drop":
                drops += 1
                if isinstance(detail.get("drop_seconds"), (int, float)):
                    drop_seconds_list.append(float(detail["drop_seconds"]))
            elif r.action == "usage_ask":
                asks += 1
                m = detail.get("mode")
                if m:
                    mode_dist[m] = mode_dist.get(m, 0) + 1

        avg_drop_seconds = (
            sum(drop_seconds_list) / len(drop_seconds_list)
            if drop_seconds_list
            else None
        )

        return {
            "user_id": user_id,
            "window_days": days,
            "drops": drops,
            "asks": asks,
            "mode_distribution": mode_dist,
            "active_days": len(active_dates),
            "avg_drop_seconds": avg_drop_seconds,
            "daily_active_streak": _calc_streak(sorted(active_dates)),
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as e:
        logger.warning(f"get_usage_summary failed: {e}")
        return {
            "user_id": user_id,
            "window_days": days,
            "drops": 0,
            "asks": 0,
            "mode_distribution": {},
            "active_days": 0,
            "avg_drop_seconds": None,
            "daily_active_streak": 0,
            "error": str(e),
        }


def _calc_streak(sorted_dates: list) -> int:
    """从排序好的日期列表计算连续活跃天数。"""
    if not sorted_dates:
        return 0
    from datetime import date

    try:
        days = [date.fromisoformat(d) for d in sorted_dates]
    except Exception:
        return 0
    streak = 1
    for i in range(len(days) - 1, 0, -1):
        if (days[i] - days[i - 1]).days == 1:
            streak += 1
        else:
            break
    return streak
