"""Cognitive Advisor API (v2.0).

端点:
- POST /api/advisor/decisions/track
- POST /api/advisor/decisions/{id}/outcome
- GET  /api/advisor/decisions
- GET  /api/advisor/decisions/{id}
- POST /api/advisor/ask
- POST /api/advisor/review/run
- GET  /api/advisor/review/latest
- GET  /api/advisor/review/history
- POST /api/advisor/memory/{id}/auto-track
- POST /api/advisor/decisions/{id}/review        (v2.0)
- GET  /api/advisor/decisions/{id}/reviews       (v2.0)
- GET  /api/advisor/conflicts                    (v2.0)
- GET  /api/advisor/conflicts/{id}               (v2.0)
- PATCH /api/advisor/conflicts/{id}              (v2.0)
- GET  /api/advisor/sessions                     (v2.0)

权限: get_current_user, 只能操作自己的数据。
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from src.shared.config import settings
from src.shared.db.database import get_db, async_session
from src.shared.security.dependencies import get_current_user
from src.memory.models.committed_memory import CommittedMemory
from src.cognition.models.decision_record import DecisionRecord
from src.execution.models.user import User
from src.cognition.models.weekly_review import WeeklyReview
from src.cognition.schemas.advisor import (
    AdvisorAskRequest,
    AdvisorAskResponse,
    AdvisorSessionListResponse,
    AdvisorSessionResponse,
    ConflictRecordListResponse,
    ConflictRecordResponse,
    ConflictStatusUpdateRequest,
    DecisionListResponse,
    DecisionOutcomeRequest,
    DecisionResponse,
    DecisionReviewRequest,
    DecisionReviewResponse,
    DecisionTrackRequest,
    WeeklyReviewGenerateRequest,
    WeeklyReviewHistoryResponse,
    WeeklyReviewResponse,
)
from src.cognition.models.advisor_session import AdvisorSession
from src.cognition.models.conflict_record import ConflictRecord, VALID_CONFLICT_STATUS
from src.cognition.models.decision_review import DecisionReview
from src.cognition.services.advisor_engine import AdvisorEngine
from src.cognition.services.decision_tracker import VALID_STATUSES, DecisionTracker
from src.cognition.services.weekly_review import WeeklyReviewService
from src.execution.services.ws_manager import ws_manager

router = APIRouter()


def _decision_to_response(d: DecisionRecord) -> DecisionResponse:
    return DecisionResponse(
        id=d.id,
        user_id=d.user_id,
        title=d.title or "",
        context=d.context or "",
        decision=d.decision or "",
        rationale=d.rationale or "",
        expected_outcome=d.expected_outcome,
        actual_outcome=d.actual_outcome,
        status=d.status or "open",
        linked_memory_id=d.linked_memory_id,
        project_id=d.project_id,
        decided_at=d.decided_at,
        resolved_at=d.resolved_at,
        review_count=d.review_count or 0,
        created_at=d.created_at,
        updated_at=d.updated_at,
    )


def _safe_json_loads(text: Optional[str], default):
    if not text:
        return default
    try:
        return json.loads(text)
    except Exception:
        return default


def _weekly_review_to_response(w: WeeklyReview) -> WeeklyReviewResponse:
    return WeeklyReviewResponse(
        id=w.id,
        user_id=w.user_id,
        week_start=w.week_start,
        week_end=w.week_end,
        new_memories=_safe_json_loads(w.new_memories_json, []) or [],
        decisions=_safe_json_loads(w.decisions_json, []) or [],
        highlights=_safe_json_loads(w.highlights_json, []) or [],
        open_questions=_safe_json_loads(w.open_questions_json, []) or [],
        summary=w.summary or "",
        word_count=w.word_count or 0,
        new_memories_count=len(_safe_json_loads(w.new_memories_json, []) or []),
        decisions_count=len(_safe_json_loads(w.decisions_json, []) or []),
        created_at=w.created_at,
        persisted=True,
    )


@router.post("/decisions/track", response_model=DecisionResponse)
async def track_decision(
    request: DecisionTrackRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """创建一条决策跟踪记录。"""
    tracker = DecisionTracker(db)
    try:
        record = await tracker.track_decision(
            user_id=user.id,
            title=request.title,
            context=request.context,
            decision=request.decision,
            rationale=request.rationale,
            expected_outcome=request.expected_outcome,
            project_id=request.project_id,
            linked_memory_id=request.linked_memory_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _decision_to_response(record)


@router.post("/decisions/{decision_id}/outcome", response_model=DecisionResponse)
async def update_decision_outcome(
    decision_id: str,
    request: DecisionOutcomeRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """补充决策的实际结果, 默认 status=resolved, 也可传 abandoned。"""
    result = await db.execute(
        select(DecisionRecord).where(DecisionRecord.id == decision_id)
    )
    existing = result.scalar_one_or_none()
    if existing is None:
        raise HTTPException(status_code=404, detail="Decision not found")
    if existing.user_id != user.id:
        raise HTTPException(status_code=403, detail="Not authorized")

    tracker = DecisionTracker(db)
    try:
        record = await tracker.update_outcome(
            decision_id=decision_id,
            actual_outcome=request.actual_outcome,
            status=request.status or "resolved",
        )
    except LookupError:
        raise HTTPException(status_code=404, detail="Decision not found")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _decision_to_response(record)


@router.get("/decisions", response_model=DecisionListResponse)
async def list_decisions(
    status: Optional[str] = Query(None, description="open / resolved / abandoned"),
    project_id: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """列出当前用户的决策, 默认按 decided_at 倒序。
    status=open 时等价于 DecisionTracker.list_open_decisions。"""
    if status and status not in VALID_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"invalid status: {status}; must be one of {sorted(VALID_STATUSES)}",
        )
    tracker = DecisionTracker(db)
    if status == "open":
        decisions = await tracker.list_open_decisions(
            user.id, project_id=project_id, limit=limit
        )
    else:
        decisions = await tracker.history(
            user.id, project_id=project_id, status=status, limit=limit
        )
    return DecisionListResponse(
        decisions=[_decision_to_response(d) for d in decisions],
        total=len(decisions),
    )


@router.get("/decisions/{decision_id}", response_model=DecisionResponse)
async def get_decision(
    decision_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(DecisionRecord).where(DecisionRecord.id == decision_id)
    )
    record = result.scalar_one_or_none()
    if record is None:
        raise HTTPException(status_code=404, detail="Decision not found")
    if record.user_id != user.id:
        raise HTTPException(status_code=403, detail="Not authorized")
    return _decision_to_response(record)


@router.post("/ask", response_model=AdvisorAskResponse)
async def advisor_ask(
    request: AdvisorAskRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """核心: 调用 AdvisorEngine 给出 recall / decision / review / planning / reflection。

    v2.1: mode 留空时自动分类(classify_mode), 解决"用户懒得切模式"问题。
    v2.1: 询问完成后异步写 usage_metrics(不阻塞返回)。
    """
    from src.cognition.services.daily_briefing import classify_mode
    from src.execution.services.usage_metrics import record_ask

    resolved_mode = request.mode or classify_mode(request.question)

    engine = AdvisorEngine(db)
    result = await engine.advise(
        user_id=user.id,
        question=request.question,
        mode=resolved_mode,
        recall_level=request.recall_level or "work_context",
        project_id=request.project_id,
        decision_ids=request.decision_ids,
    )

    try:
        session_id = None
        meta = result.get("meta") or {}
        session_id = meta.get("session_id") or result.get("session_id")
        await record_ask(
            db, user.id,
            session_id=session_id,
            mode=resolved_mode,
            confidence=result.get("confidence"),
        )
    except Exception:
        pass

    return AdvisorAskResponse(**result)


@router.post("/review/run", response_model=WeeklyReviewResponse)
async def run_weekly_review(
    request: WeeklyReviewGenerateRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """生成本周(默认)或指定 week_start 的周报。dry_run=True 默认不持久化。"""
    svc = WeeklyReviewService(db)
    try:
        result = await svc.generate(
            user_id=user.id,
            week_start=request.week_start,
            dry_run=bool(request.dry_run) if request.dry_run is not None else True,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return WeeklyReviewResponse(
        id=result.get("id", ""),
        user_id=result["user_id"],
        week_start=result["week_start"],
        week_end=result["week_end"],
        new_memories=result.get("new_memories", []),
        decisions=result.get("decisions", []),
        highlights=result.get("highlights", []),
        open_questions=result.get("open_questions", []),
        summary=result.get("summary", ""),
        word_count=result.get("word_count", 0),
        new_memories_count=result.get("new_memories_count", 0),
        decisions_count=result.get("decisions_count", 0),
        created_at=datetime.utcnow(),
        persisted=result.get("persisted", False),
    )


@router.get("/review/latest")
async def get_latest_review(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """最近一次生成的周报 (按 created_at 倒序)。找不到时返回 null (200 OK)。"""
    svc = WeeklyReviewService(db)
    review = await svc.latest(user.id)
    if review is None:
        return {"review": None}
    return {"review": _weekly_review_to_response(review)}


@router.get("/review/history", response_model=WeeklyReviewHistoryResponse)
async def get_review_history(
    limit: int = Query(12, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """历史周报 (按 created_at 倒序)。"""
    svc = WeeklyReviewService(db)
    reviews = await svc.history(user.id, limit=limit)
    return WeeklyReviewHistoryResponse(
        reviews=[_weekly_review_to_response(r) for r in reviews],
        total=len(reviews),
    )


@router.post("/memory/{memory_id}/auto-track", response_model=DecisionResponse)
async def auto_track_memory(
    memory_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """把一条 DECISION 类 committed memory 自动 track 成 DecisionRecord。
    已存在则返回 existing, memory 不是 DECISION 类返回 400。"""
    mem_result = await db.execute(
        select(CommittedMemory).where(CommittedMemory.id == memory_id)
    )
    memory = mem_result.scalar_one_or_none()
    if memory is None:
        raise HTTPException(status_code=404, detail="Memory not found")
    if memory.user_id != user.id:
        raise HTTPException(status_code=403, detail="Not authorized")

    tracker = DecisionTracker(db)
    record = await tracker.auto_track_from_committed_memory(user.id, memory_id)
    if record is None:
        raise HTTPException(
            status_code=400,
            detail="Memory is not of type DECISION; cannot auto-track.",
        )
    return _decision_to_response(record)


@router.websocket("/ws/ask/{user_id}")
async def websocket_advisor_ask(websocket: WebSocket, user_id: str):
    """WebSocket 实时 advisor 问答。需要 JWT 鉴权。"""
    # Authenticate via query parameter or subprotocol
    token = websocket.query_params.get("token")
    if not token:
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
            mode = data.get("mode", "decision")
            recall_level = data.get("recall_level", "work_context")
            project_id = data.get("project_id")
            if not question:
                await ws_manager.send_error(user_id, "question is required")
                continue

            try:
                async with async_session() as db:
                    advisor = AdvisorEngine(db)
                    result = await advisor.advise(
                        user_id=user_id,
                        question=question,
                        mode=mode,
                        recall_level=recall_level,
                        project_id=project_id,
                        decision_ids=data.get("decision_ids"),
                    )
                    await ws_manager.send_json(user_id, {
                        "event": "done",
                        "data": result,
                    })
            except Exception as e:
                await ws_manager.send_error(user_id, str(e))

    except (WebSocketDisconnect, Exception):
        ws_manager.disconnect(user_id)


# ── v2.0 端点 ────────────────────────────────────────────────────────────────


def _conflict_record_to_response(r: ConflictRecord) -> ConflictRecordResponse:
    """将 ConflictRecord 模型转换为响应。

    注意: 模型字段 (current_statement / past_statement / recommended_action)
    与 schema 字段 (current_content / past_content / suggested_resolution) 名称不同,
    这里做映射; 模型中不存在的字段 (如 acknowledged_at / updated_at) 返回 None。
    """
    # related_memory_ids 是 JSON 字符串, 解析出 past_memory_id
    past_memory_id = None
    try:
        related = json.loads(r.related_memory_ids or "[]")
        if isinstance(related, list) and related:
            past_memory_id = str(related[0])
    except Exception:
        pass

    return ConflictRecordResponse(
        id=r.id,
        user_id=r.user_id,
        conflict_type=r.conflict_type,
        interpretation=r.interpretation,
        severity=r.severity,
        status=r.status,
        current_content=r.current_statement,
        past_content=r.past_statement,
        current_memory_id=None,
        past_memory_id=past_memory_id,
        explanation=r.past_statement,
        suggested_resolution=r.recommended_action,
        project_id=None,
        detected_at=r.created_at,
        acknowledged_at=None,
        resolved_at=r.resolved_at,
        created_at=r.created_at,
        updated_at=None,
    )


def _advisor_session_to_response(s: AdvisorSession) -> AdvisorSessionResponse:
    """将 AdvisorSession 模型转换为响应。

    注意: 模型字段有限, risk_points 中保存了扩展 JSON 数据
    (见 AdvisorEngine._save_session); 这里解析出来填充 schema 各字段。
    """
    # risk_points 字段在持久化时被复用保存扩展数据
    raw_risk_points = _safe_json_loads(s.risk_points, [])
    extended = {}
    actual_risk_points = raw_risk_points
    if isinstance(raw_risk_points, dict) and "extended" in raw_risk_points:
        extended = raw_risk_points.get("extended") or {}
        actual_risk_points = raw_risk_points.get("risk_points") or []

    return AdvisorSessionResponse(
        id=s.id,
        user_id=s.user_id,
        question=s.question,
        advisor_mode=s.advisor_mode,
        answer=s.answer,
        direct_recommendation=s.direct_recommendation,
        confidence=s.confidence,
        uncertainty=s.uncertainty,
        historical_basis=extended.get("historical_basis") or [],
        risk_points=actual_risk_points if isinstance(actual_risk_points, list) else [],
        conflicts_or_changes=extended.get("conflicts_or_changes") or [],
        suggested_next_steps=extended.get("suggested_next_steps") or [],
        cited_memories=_safe_json_loads(s.cited_memory_ids, []),
        cited_decisions=_safe_json_loads(s.cited_decision_ids, []),
        meta=extended.get("meta") or {},
        project_id=extended.get("project_id"),
        created_at=s.created_at,
    )


# ── Decision Review CRUD ──────────────────────────────────────────────────────


@router.post("/decisions/{decision_id}/review", response_model=DecisionReviewResponse)
async def create_decision_review(
    decision_id: str,
    request: DecisionReviewRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """为指定决策创建一条复盘记录。"""
    result = await db.execute(
        select(DecisionRecord).where(DecisionRecord.id == decision_id)
    )
    decision = result.scalar_one_or_none()
    if decision is None:
        raise HTTPException(status_code=404, detail="Decision not found")
    if decision.user_id != user.id:
        raise HTTPException(status_code=403, detail="Not authorized")

    review = DecisionReview(
        id=uuid.uuid4().hex[:16],
        decision_id=decision_id,
        user_id=user.id,
        review_notes=request.review_notes,
        lessons_learned=request.lessons_learned,
        outcome_rating=request.outcome_rating,
    )
    db.add(review)

    # 更新 decision 的 review_count
    decision.review_count = (decision.review_count or 0) + 1
    db.add(decision)

    await db.commit()
    await db.refresh(review)

    return DecisionReviewResponse(
        id=review.id,
        decision_id=review.decision_id,
        user_id=review.user_id,
        review_notes=review.review_notes,
        lessons_learned=review.lessons_learned,
        outcome_rating=review.outcome_rating,
        created_at=review.created_at,
    )


@router.get("/decisions/{decision_id}/reviews")
async def list_decision_reviews(
    decision_id: str,
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """列出指定决策的所有复盘记录。"""
    # 验证决策存在且属于当前用户
    result = await db.execute(
        select(DecisionRecord).where(DecisionRecord.id == decision_id)
    )
    decision = result.scalar_one_or_none()
    if decision is None:
        raise HTTPException(status_code=404, detail="Decision not found")
    if decision.user_id != user.id:
        raise HTTPException(status_code=403, detail="Not authorized")

    stmt = (
        select(DecisionReview)
        .where(DecisionReview.decision_id == decision_id)
        .order_by(DecisionReview.created_at.desc())
        .limit(limit)
    )
    result = await db.execute(stmt)
    reviews = result.scalars().all()

    return {
        "reviews": [
            DecisionReviewResponse(
                id=r.id,
                decision_id=r.decision_id,
                user_id=r.user_id,
                review_notes=r.review_notes,
                lessons_learned=r.lessons_learned,
                outcome_rating=r.outcome_rating,
                created_at=r.created_at,
            )
            for r in reviews
        ],
        "total": len(reviews),
    }


# ── Conflict Record ───────────────────────────────────────────────────────────


@router.get("/conflicts", response_model=ConflictRecordListResponse)
async def list_conflicts(
    status: Optional[str] = Query(None, description="open / acknowledged / resolved / ignored"),
    conflict_type: Optional[str] = Query(None, description="冲突类型筛选"),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """列出当前用户的冲突记录。"""
    stmt = (
        select(ConflictRecord)
        .where(ConflictRecord.user_id == user.id)
        .order_by(ConflictRecord.created_at.desc())
        .limit(limit)
    )
    if status:
        stmt = stmt.where(ConflictRecord.status == status)
    if conflict_type:
        stmt = stmt.where(ConflictRecord.conflict_type == conflict_type)

    result = await db.execute(stmt)
    records = result.scalars().all()

    return ConflictRecordListResponse(
        conflicts=[_conflict_record_to_response(r) for r in records],
        total=len(records),
    )


@router.get("/conflicts/{conflict_id}", response_model=ConflictRecordResponse)
async def get_conflict(
    conflict_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """获取冲突记录详情。"""
    result = await db.execute(
        select(ConflictRecord).where(ConflictRecord.id == conflict_id)
    )
    record = result.scalar_one_or_none()
    if record is None:
        raise HTTPException(status_code=404, detail="Conflict not found")
    if record.user_id != user.id:
        raise HTTPException(status_code=403, detail="Not authorized")
    return _conflict_record_to_response(record)


@router.patch("/conflicts/{conflict_id}", response_model=ConflictRecordResponse)
async def update_conflict_status(
    conflict_id: str,
    request: ConflictStatusUpdateRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """更新冲突记录状态 (acknowledged / resolved / ignored)。"""
    if request.status not in VALID_CONFLICT_STATUS:
        raise HTTPException(
            status_code=400,
            detail=f"invalid status: {request.status}; must be one of {sorted(VALID_CONFLICT_STATUS)}",
        )

    result = await db.execute(
        select(ConflictRecord).where(ConflictRecord.id == conflict_id)
    )
    record = result.scalar_one_or_none()
    if record is None:
        raise HTTPException(status_code=404, detail="Conflict not found")
    if record.user_id != user.id:
        raise HTTPException(status_code=403, detail="Not authorized")

    record.status = request.status
    now = datetime.utcnow()
    if request.status == "resolved":
        record.resolved_at = now

    db.add(record)
    await db.commit()
    await db.refresh(record)

    return _conflict_record_to_response(record)


# ── Advisor Sessions ──────────────────────────────────────────────────────────


@router.get("/sessions", response_model=AdvisorSessionListResponse)
async def list_advisor_sessions(
    advisor_mode: Optional[str] = Query(None, description="recall / decision / review / planning / reflection"),
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """列出历史军师会话。"""
    stmt = (
        select(AdvisorSession)
        .where(AdvisorSession.user_id == user.id)
        .order_by(AdvisorSession.created_at.desc())
        .limit(limit)
    )
    if advisor_mode:
        stmt = stmt.where(AdvisorSession.advisor_mode == advisor_mode)

    result = await db.execute(stmt)
    sessions = result.scalars().all()

    return AdvisorSessionListResponse(
        sessions=[_advisor_session_to_response(s) for s in sessions],
        total=len(sessions),
    )
