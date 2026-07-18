"""Gen 2 / Memory Governance API — Conflict / Dedup / Rewrite 端点。

- 全部挂 ``/api/memory`` 前缀, 子路径全为静态名 (conflicts/, duplicates/, rewriter/),
  不会与已有 ``/{memory_id}`` 路由冲突。
- 权限: 全部走 ``get_current_user``; 跨 user 访问返回 403。
- LLM 异常/数据缺失时永远不抛 5xx, 返回降级响应。
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from src.shared.db.database import get_db
from src.shared.security.dependencies import get_current_user
from src.memory.models.committed_memory import CommittedMemory
from src.cognition.models.conflict_record import ConflictRecord
from src.execution.models.memory_relation import MemoryRelation
from src.cognition.schemas.governance import (
    ConflictCheckRequest,
    ConflictCheckResponse,
    ConflictRecordResponse,
    DuplicateFindRequest,
    DuplicateFindResponse,
    DuplicateMergeRequest,
    DuplicateMergeResponse,
    HygieneApplyRequest,
    HygieneApplyResponse,
    HygieneRunRequest,
    HygieneRunResponse,
    MemoryRelationCreateRequest,
    MemoryRelationResponse,
    RewriterApplyRequest,
    RewriterApplyResponse,
    RewriterRunRequest,
    RewriterRunResponse,
)
from src.memory.services.conflict_checker import ConflictChecker
from src.memory.services.deduplicator import MemoryDeduplicator
from src.memory.services.memory_rewriter import MemoryRewriter, VALID_RELATION_TYPES
from src.memory.tasks.memory_hygiene import (
    hygiene_suggestions_to_rewrite_proposals,
    run_nightly_hygiene,
)
from src.shared.ids.id_generator import generate_memory_relation_id

logger = logging.getLogger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# POST /api/memory/conflicts/check
# ---------------------------------------------------------------------------


@router.post("/conflicts/check", response_model=ConflictCheckResponse)
async def check_conflicts(
    request: ConflictCheckRequest,
    db: AsyncSession = Depends(get_db),
    user = Depends(get_current_user),
):
    """检查拟治理内容与已有原则/decision/insight 是否矛盾。"""
    try:
        checker = ConflictChecker(db)
        result = await checker.check(
            user_id=user.id,
            candidate={
                "body": request.body,
                "title": request.title,
                "memory_type": request.memory_type,
            },
            recall_level=request.recall_level or "work_context",
        )
        return ConflictCheckResponse(
            user_id=result.get("user_id", user.id),
            has_conflict=bool(result.get("has_conflict")),
            conflicts=result.get("conflicts", []),
            similar_memories=result.get("similar_memories", []),
            warnings=result.get("warnings", []),
            checked_at=result.get("checked_at"),
        )
    except Exception as e:
        logger.exception(f"check_conflicts failed: {e}")
        return ConflictCheckResponse(
            user_id=user.id,
            has_conflict=False,
            conflicts=[],
            similar_memories=[],
            warnings=["server_error"],
            checked_at=datetime.now(timezone.utc).isoformat(),
        )


# ---------------------------------------------------------------------------
# POST /api/memory/duplicates/find
# ---------------------------------------------------------------------------


@router.post("/duplicates/find", response_model=DuplicateFindResponse)
async def find_duplicates(
    request: DuplicateFindRequest,
    db: AsyncSession = Depends(get_db),
    user = Depends(get_current_user),
):
    """查找用户库中相似度 >= threshold 的 memory 对。"""
    try:
        dedup = MemoryDeduplicator(db)
        if request.memory_id:
            await _assert_memory_owned(db, request.memory_id, user.id)
        pairs = await dedup.find_duplicates(
            user_id=user.id,
            memory_id=request.memory_id,
            similarity_threshold=request.similarity_threshold,
            top_k=request.top_k,
        )
        return DuplicateFindResponse(
            user_id=user.id,
            pairs=pairs,
            scanned=len(pairs),
            warnings=[],
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"find_duplicates failed: {e}")
        return DuplicateFindResponse(
            user_id=user.id,
            pairs=[],
            scanned=0,
            warnings=["server_error"],
        )


# ---------------------------------------------------------------------------
# POST /api/memory/duplicates/merge
# ---------------------------------------------------------------------------


@router.post("/duplicates/merge", response_model=DuplicateMergeResponse)
async def merge_duplicates(
    request: DuplicateMergeRequest,
    db: AsyncSession = Depends(get_db),
    user = Depends(get_current_user),
):
    """合并两条 memory: secondary 标记 SUPERSEDED, primary body 替换。"""
    if request.primary_memory_id == request.secondary_memory_id:
        raise HTTPException(status_code=400, detail="primary and secondary must be different")

    primary = await _load_user_memory(db, request.primary_memory_id, user.id)
    secondary = await _load_user_memory(db, request.secondary_memory_id, user.id)

    dedup = MemoryDeduplicator(db)
    try:
        await dedup.merge(
            primary_memory_id=primary.id,
            secondary_memory_id=secondary.id,
            merged_body=request.merged_body,
            expected_user_id=user.id,
        )
    except LookupError as le:
        raise HTTPException(status_code=404, detail=str(le))
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        logger.exception(f"merge_duplicates failed: {e}")
        raise HTTPException(status_code=500, detail="merge_failed")

    return DuplicateMergeResponse(
        primary_memory_id=primary.id,
        secondary_memory_id=secondary.id,
        secondary_status="superseded",
        merged_body_preview=(request.merged_body or "")[:200],
        merged_at=datetime.now(timezone.utc).isoformat(),
    )


# ---------------------------------------------------------------------------
# POST /api/memory/rewriter/run
# ---------------------------------------------------------------------------


@router.post("/rewriter/run", response_model=RewriterRunResponse)
async def run_rewriter(
    request: RewriterRunRequest,
    db: AsyncSession = Depends(get_db),
    user = Depends(get_current_user),
):
    """生成 rewrite proposals。``dry_run=True`` (默认) 不写库, 只返回结构。"""
    rewriter = MemoryRewriter(db)
    result = await rewriter.rewrite(
        user_id=user.id,
        target_types=request.target_types,
        max_clusters=request.max_clusters,
    )

    applied = False
    if not request.dry_run and result.get("proposals"):
        apply_result = await rewriter.apply_proposals(user_id=user.id, proposals=result["proposals"])
        applied = True
        result["applied"] = True
        result["rewritten_count"] = apply_result.get("applied_count", 0)
        result.setdefault("warnings", []).append(
            f"applied {apply_result.get('applied_count', 0)} of "
            f"{len(result.get('proposals', []))} proposals; "
            f"failed {len(apply_result.get('failed', []))}"
        )

    return RewriterRunResponse(
        user_id=result.get("user_id", user.id),
        rewritten_count=int(result.get("rewritten_count", 0)),
        merges_proposed=int(result.get("merges_proposed", 0)),
        proposals=result.get("proposals", []),
        applied=bool(applied),
        generated_at=result.get("generated_at", datetime.now(timezone.utc).isoformat()),
        warnings=result.get("warnings", []),
    )


# ---------------------------------------------------------------------------
# POST /api/memory/rewriter/apply
# ---------------------------------------------------------------------------


@router.post("/rewriter/apply", response_model=RewriterApplyResponse)
async def apply_rewriter(
    request: RewriterApplyRequest,
    db: AsyncSession = Depends(get_db),
    user = Depends(get_current_user),
):
    """应用审核过的 rewrite proposals。"""
    rewriter = MemoryRewriter(db)
    result = await rewriter.apply_proposals(
        user_id=user.id,
        proposals=[p.model_dump() if hasattr(p, "model_dump") else p.dict() for p in request.proposals],
    )
    return RewriterApplyResponse(
        user_id=user.id,
        applied_count=int(result.get("applied_count", 0)),
        failed=result.get("failed", []),
        applied_at=result.get("applied_at", datetime.now(timezone.utc).isoformat()),
    )


# ---------------------------------------------------------------------------
# POST /api/memory/hygiene/run
# POST /api/memory/hygiene/apply
# ---------------------------------------------------------------------------


@router.post("/hygiene/run", response_model=HygieneRunResponse)
async def run_hygiene_review(
    request: HygieneRunRequest,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """Run suggestion-only memory hygiene for the current user."""
    result = await run_nightly_hygiene(
        db,
        user.id,
        dedup_threshold=request.dedup_threshold,
        importance_floor=request.importance_floor,
        max_pairs_per_user=request.max_pairs_per_user,
    )
    return HygieneRunResponse(
        user_id=result.get("user_id", user.id),
        duplicate_pairs=result.get("duplicate_pairs", []),
        stale_conflicts=result.get("stale_conflicts", []),
        memory_evolution=result.get("memory_evolution", {}),
        hygiene_suggestions=result.get("hygiene_suggestions", []),
        ran_at=result.get("ran_at", datetime.now(timezone.utc).isoformat()),
        stats=result.get("stats", {}),
        warnings=result.get("warnings", []),
    )


@router.post("/hygiene/apply", response_model=HygieneApplyResponse)
async def apply_hygiene_suggestions(
    request: HygieneApplyRequest,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """Apply explicitly approved hygiene suggestions through MemoryRewriter."""
    if not request.approved:
        raise HTTPException(status_code=400, detail="hygiene_apply_requires_approval")

    suggestions = [
        item.model_dump() if hasattr(item, "model_dump") else item.dict()
        for item in request.suggestions
    ]
    converted = hygiene_suggestions_to_rewrite_proposals(suggestions)
    proposals = converted["proposals"]
    unsupported = converted["unsupported"]

    if request.dry_run or not proposals:
        return HygieneApplyResponse(
            user_id=user.id,
            approved=request.approved,
            dry_run=request.dry_run,
            proposals=proposals,
            unsupported=unsupported,
            applied_count=0,
            failed=[],
            applied_at=datetime.now(timezone.utc).isoformat(),
        )

    rewriter = MemoryRewriter(db)
    result = await rewriter.apply_proposals(user_id=user.id, proposals=proposals)
    return HygieneApplyResponse(
        user_id=user.id,
        approved=request.approved,
        dry_run=request.dry_run,
        proposals=proposals,
        unsupported=unsupported,
        applied_count=int(result.get("applied_count", 0)),
        failed=result.get("failed", []),
        applied_at=result.get("applied_at", datetime.now(timezone.utc).isoformat()),
    )


# ---------------------------------------------------------------------------
# GET /api/memory/relations
# POST /api/memory/relations
# ---------------------------------------------------------------------------


@router.get("/relations", response_model=list[MemoryRelationResponse])
async def list_relations(
    source_memory_id: Optional[str] = Query(None),
    target_memory_id: Optional[str] = Query(None),
    relation_type: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """列出 memory relations（可按 source/target/type 过滤）。"""
    query = select(MemoryRelation).where(MemoryRelation.user_id == user.id)
    if source_memory_id:
        query = query.where(MemoryRelation.source_memory_id == source_memory_id)
    if target_memory_id:
        query = query.where(MemoryRelation.target_memory_id == target_memory_id)
    if relation_type:
        query = query.where(MemoryRelation.relation_type == relation_type)
    query = query.order_by(MemoryRelation.created_at.desc()).limit(100)
    result = await db.execute(query)
    relations = result.scalars().all()
    return [
        MemoryRelationResponse(
            id=r.id,
            source_memory_id=r.source_memory_id,
            target_memory_id=r.target_memory_id,
            relation_type=r.relation_type,
            reason=r.reason,
            confidence=r.confidence,
            valid_from=r.valid_from.isoformat() if r.valid_from else None,
            valid_until=r.valid_until.isoformat() if r.valid_until else None,
            created_at=r.created_at.isoformat() if r.created_at else "",
        )
        for r in relations
    ]


@router.post("/relations", response_model=MemoryRelationResponse)
async def create_relation(
    request: MemoryRelationCreateRequest,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """创建一条 memory relation。"""
    relation_type = request.relation_type.strip()
    if relation_type not in VALID_RELATION_TYPES:
        raise HTTPException(status_code=400, detail="invalid_relation_type")
    confidence = 0.5 if request.confidence is None else request.confidence
    if not 0.0 <= confidence <= 1.0:
        raise HTTPException(status_code=400, detail="confidence_must_be_between_0_and_1")
    if request.valid_from and request.valid_until and request.valid_until < request.valid_from:
        raise HTTPException(status_code=400, detail="valid_until_must_not_precede_valid_from")
    await _assert_memory_owned(db, request.source_memory_id, user.id)
    await _assert_memory_owned(db, request.target_memory_id, user.id)
    if request.source_memory_id == request.target_memory_id:
        raise HTTPException(status_code=400, detail="source and target must be different")

    existing = await db.scalar(
        select(MemoryRelation).where(
            MemoryRelation.user_id == user.id,
            MemoryRelation.source_memory_id == request.source_memory_id,
            MemoryRelation.target_memory_id == request.target_memory_id,
            MemoryRelation.relation_type == relation_type,
        )
    )
    if existing:
        return MemoryRelationResponse(
            id=existing.id,
            source_memory_id=existing.source_memory_id,
            target_memory_id=existing.target_memory_id,
            relation_type=existing.relation_type,
            reason=existing.reason,
            confidence=existing.confidence,
            valid_from=existing.valid_from.isoformat() if existing.valid_from else None,
            valid_until=existing.valid_until.isoformat() if existing.valid_until else None,
            created_at=existing.created_at.isoformat() if existing.created_at else "",
        )

    rel_id = generate_memory_relation_id()
    relation = MemoryRelation(
        id=rel_id,
        user_id=user.id,
        source_memory_id=request.source_memory_id,
        target_memory_id=request.target_memory_id,
        relation_type=relation_type,
        reason=request.reason,
        confidence=confidence,
        valid_from=request.valid_from,
        valid_until=request.valid_until,
    )
    db.add(relation)
    try:
        await db.commit()
        await db.refresh(relation)
    except Exception as e:
        await db.rollback()
        logger.exception(f"create_relation failed: {e}")
        raise HTTPException(status_code=500, detail="create_relation_failed")

    return MemoryRelationResponse(
        id=relation.id,
        source_memory_id=relation.source_memory_id,
        target_memory_id=relation.target_memory_id,
        relation_type=relation.relation_type,
        reason=relation.reason,
        confidence=relation.confidence,
        valid_from=relation.valid_from.isoformat() if relation.valid_from else None,
        valid_until=relation.valid_until.isoformat() if relation.valid_until else None,
        created_at=relation.created_at.isoformat() if relation.created_at else "",
    )


# ---------------------------------------------------------------------------
# GET /api/memory/conflicts
# GET /api/memory/conflicts/{conflict_id}
# PATCH /api/memory/conflicts/{conflict_id}
# ---------------------------------------------------------------------------


@router.get("/conflicts", response_model=list[ConflictRecordResponse])
async def list_conflicts(
    status: Optional[str] = Query(None),
    severity: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """列出历史冲突记录。"""
    query = select(ConflictRecord).where(ConflictRecord.user_id == user.id)
    if status:
        query = query.where(ConflictRecord.status == status)
    if severity:
        query = query.where(ConflictRecord.severity == severity)
    query = query.order_by(ConflictRecord.created_at.desc()).limit(100)
    result = await db.execute(query)
    records = result.scalars().all()
    return [
        ConflictRecordResponse(
            id=c.id,
            user_id=c.user_id,
            conflict_type=c.conflict_type,
            current_statement=c.current_statement,
            past_statement=c.past_statement,
            severity=c.severity,
            interpretation=c.interpretation,
            recommended_action=c.recommended_action,
            confidence=c.confidence,
            status=c.status,
            created_at=c.created_at.isoformat() if c.created_at else "",
        )
        for c in records
    ]


@router.get("/conflicts/{conflict_id}", response_model=ConflictRecordResponse)
async def get_conflict(
    conflict_id: str,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """获取冲突详情。"""
    result = await db.execute(
        select(ConflictRecord).where(ConflictRecord.id == conflict_id)
    )
    record = result.scalar_one_or_none()
    if not record:
        raise HTTPException(status_code=404, detail=f"conflict_not_found: {conflict_id}")
    if record.user_id != user.id:
        raise HTTPException(status_code=403, detail="not_authorized_for_conflict")

    return ConflictRecordResponse(
        id=record.id,
        user_id=record.user_id,
        conflict_type=record.conflict_type,
        current_statement=record.current_statement,
        past_statement=record.past_statement,
        severity=record.severity,
        interpretation=record.interpretation,
        recommended_action=record.recommended_action,
        confidence=record.confidence,
        status=record.status,
        created_at=record.created_at.isoformat() if record.created_at else "",
    )


@router.patch("/conflicts/{conflict_id}", response_model=ConflictRecordResponse)
async def update_conflict(
    conflict_id: str,
    body: dict,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """更新冲突状态。"""
    result = await db.execute(
        select(ConflictRecord).where(ConflictRecord.id == conflict_id)
    )
    record = result.scalar_one_or_none()
    if not record:
        raise HTTPException(status_code=404, detail=f"conflict_not_found: {conflict_id}")
    if record.user_id != user.id:
        raise HTTPException(status_code=403, detail="not_authorized_for_conflict")

    new_status = body.get("status")
    if new_status and new_status in ("open", "acknowledged", "resolved", "ignored"):
        record.status = new_status
        if new_status == "resolved":
            record.resolved_at = datetime.now(timezone.utc)

    try:
        await db.commit()
        await db.refresh(record)
    except Exception as e:
        await db.rollback()
        logger.exception(f"update_conflict failed: {e}")
        raise HTTPException(status_code=500, detail="update_conflict_failed")

    return ConflictRecordResponse(
        id=record.id,
        user_id=record.user_id,
        conflict_type=record.conflict_type,
        current_statement=record.current_statement,
        past_statement=record.past_statement,
        severity=record.severity,
        interpretation=record.interpretation,
        recommended_action=record.recommended_action,
        confidence=record.confidence,
        status=record.status,
        created_at=record.created_at.isoformat() if record.created_at else "",
    )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


async def _load_user_memory(db: AsyncSession, memory_id: str, user_id: str) -> CommittedMemory:
    result = await db.execute(select(CommittedMemory).where(CommittedMemory.id == memory_id))
    memory = result.scalar_one_or_none()
    if not memory:
        raise HTTPException(status_code=404, detail=f"memory_not_found: {memory_id}")
    if memory.user_id != user_id:
        raise HTTPException(status_code=403, detail="not_authorized_for_memory")
    return memory


async def _assert_memory_owned(db: AsyncSession, memory_id: str, user_id: str) -> None:
    await _load_user_memory(db, memory_id, user_id)
