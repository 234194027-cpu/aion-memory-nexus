"""Daily Briefing API — Phase 4 / Sprint 3 日均使用的主入口。

GET  /api/daily/briefing   → 今天的一行日报
POST /api/quick_drop        → 零摩擦输入(一句自由文本 → 异步入库)
GET  /api/usage/metrics     → 最近 N 天的使用统计
"""
from __future__ import annotations

import time
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from src.shared.db.database import get_db
from src.shared.security.dependencies import get_current_user
from src.cognition.services.daily_briefing import build_daily_briefing
from src.execution.services.usage_metrics import get_usage_summary

router = APIRouter()


# ── GET /api/daily/briefing ──────────────────────────────────────────

@router.get("/briefing")
async def get_daily_briefing(
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    principle_echo_days: int = Query(30, ge=7, le=180),
):
    """返回今天的一句话日报。

    内容刻意做小(30 秒可读完):
    - 1 个 open decision
    - 1 个旧冲突
    - 1 条 30 天前的 principle
    - 1 个 AI 建议的下一步
    """
    return await build_daily_briefing(
        db,
        user.id,
        principle_echo_days=principle_echo_days,
    )


# ── POST /api/quick_drop ─────────────────────────────────────────────

class QuickDropRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=2000, description="自由文本，一句话或一段话")
    channel: str = Field("web", description="来源渠道: web / wecom / telegram / api")


class QuickDropResponse(BaseModel):
    accepted: bool
    event_id: Optional[str] = None
    message: str


@router.post("/quick_drop", response_model=QuickDropResponse)
async def quick_drop(
    req: QuickDropRequest,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """零摩擦输入: 接收一段自由文本，后台异步走完整 pipeline。

    - 不要求用户选 memory_type、title、tags 等元数据
    - 返回 accepted=True 表示已接受，后续异步处理(提取→去重→入库)
    - 处理完成后可通过 IM webhook 推回"已记住 / 发现冲突"
    """
    start_ts = time.time()

    try:
        from src.memory.models.raw_event import ProcessingStatus, SensitivityLevel, SourceType, VisibilityScope
        from src.memory.services.event_ingestion import EventIngestionService, trigger_ingested_event
        from datetime import datetime, timezone

        event = (
            await EventIngestionService(db).append(
                user_id=user.id,
                content=req.text,
                source_type=SourceType.MANUAL,
                source_id=req.channel,
                sensitivity=SensitivityLevel.NORMAL,
                visibility_scope=VisibilityScope.PROJECT,
                processing_status=ProcessingStatus.QUEUED,
                event_metadata={"channel": req.channel, "event_type": "quick_drop"},
            )
        ).event
        await db.commit()
        trigger_ingested_event(event.id)

        try:
            from src.execution.services.usage_metrics import record_drop
            drop_seconds = time.time() - start_ts
            await record_drop(db, user.id, memory_id=event.id, drop_seconds=drop_seconds, channel=req.channel)
        except Exception:
            pass

        return QuickDropResponse(
            accepted=True,
            event_id=event.id,
            message="已接受，后台正在处理。处理完成后会记录到你的记忆库。",
        )
    except Exception as e:
        return QuickDropResponse(
            accepted=False,
            message=f"输入失败: {e}",
        )


# ── GET /api/usage/metrics ───────────────────────────────────────────

@router.get("/metrics")
async def get_usage_metrics(
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    days: int = Query(7, ge=1, le=90),
):
    """返回用户最近 N 天的使用统计。"""
    return await get_usage_summary(db, user.id, days=days)
