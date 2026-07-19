"""Read-only analysis and user-authored relation APIs for governed memory."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.cognition.models.conflict_record import ConflictRecord
from src.cognition.schemas.governance import (
    ConflictCheckRequest,
    ConflictCheckResponse,
    ConflictRecordResponse,
    DuplicateFindRequest,
    DuplicateFindResponse,
    MemoryRelationCreateRequest,
    MemoryRelationResponse,
)
from src.execution.models.memory_relation import MemoryRelation
from src.memory.models.committed_memory import CommittedMemory
from src.memory.services.conflict_checker import ConflictChecker
from src.memory.services.deduplicator import MemoryDeduplicator
from src.shared.db.database import get_db
from src.shared.ids.id_generator import generate_memory_relation_id
from src.shared.security.dependencies import get_current_user


logger = logging.getLogger(__name__)
router = APIRouter()
VALID_RELATION_TYPES = {
    "supports", "contradicts", "supersedes", "duplicates", "updates",
    "explains", "belongs_to", "caused_by", "resulted_in",
}


@router.post("/conflicts/check", response_model=ConflictCheckResponse)
async def check_conflicts(
    request: ConflictCheckRequest,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """Analyze a statement without creating or mutating formal memory."""
    try:
        result = await ConflictChecker(db).check(
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
    except Exception as exc:
        logger.exception("check_conflicts failed: %s", exc)
        return ConflictCheckResponse(
            user_id=user.id,
            has_conflict=False,
            conflicts=[],
            similar_memories=[],
            warnings=["server_error"],
            checked_at=datetime.now(timezone.utc).isoformat(),
        )


@router.post("/duplicates/find", response_model=DuplicateFindResponse)
async def find_duplicates(
    request: DuplicateFindRequest,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """Run bounded duplicate analysis; Working Agent remains the only writer."""
    try:
        if request.memory_id:
            await _assert_memory_owned(db, request.memory_id, user.id)
        pairs = await MemoryDeduplicator(db).find_duplicates(
            user_id=user.id,
            memory_id=request.memory_id,
            similarity_threshold=request.similarity_threshold,
            top_k=request.top_k,
        )
        return DuplicateFindResponse(user_id=user.id, pairs=pairs, scanned=len(pairs), warnings=[])
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("find_duplicates failed: %s", exc)
        return DuplicateFindResponse(user_id=user.id, pairs=[], scanned=0, warnings=["server_error"])


@router.get("/relations", response_model=list[MemoryRelationResponse])
async def list_relations(
    source_memory_id: Optional[str] = Query(None),
    target_memory_id: Optional[str] = Query(None),
    relation_type: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    query = select(MemoryRelation).where(MemoryRelation.user_id == user.id)
    if source_memory_id:
        query = query.where(MemoryRelation.source_memory_id == source_memory_id)
    if target_memory_id:
        query = query.where(MemoryRelation.target_memory_id == target_memory_id)
    if relation_type:
        query = query.where(MemoryRelation.relation_type == relation_type)
    rows = list((await db.execute(query.order_by(MemoryRelation.created_at.desc()).limit(100))).scalars())
    return [_relation_response(row) for row in rows]


@router.post("/relations", response_model=MemoryRelationResponse)
async def create_relation(
    request: MemoryRelationCreateRequest,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """Create an explicit user-authored relation, never a model-inferred fact."""
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
        return _relation_response(existing)
    relation = MemoryRelation(
        id=generate_memory_relation_id(),
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
    except Exception as exc:
        await db.rollback()
        logger.exception("create_relation failed: %s", exc)
        raise HTTPException(status_code=500, detail="create_relation_failed") from exc
    return _relation_response(relation)


@router.get("/conflicts", response_model=list[ConflictRecordResponse])
async def list_conflicts(
    status: Optional[str] = Query(None),
    severity: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    query = select(ConflictRecord).where(ConflictRecord.user_id == user.id)
    if status:
        query = query.where(ConflictRecord.status == status)
    if severity:
        query = query.where(ConflictRecord.severity == severity)
    rows = list((await db.execute(query.order_by(ConflictRecord.created_at.desc()).limit(100))).scalars())
    return [_conflict_response(row) for row in rows]


@router.get("/conflicts/{conflict_id}", response_model=ConflictRecordResponse)
async def get_conflict(
    conflict_id: str,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    record = await db.scalar(select(ConflictRecord).where(ConflictRecord.id == conflict_id))
    if record is None:
        raise HTTPException(status_code=404, detail=f"conflict_not_found: {conflict_id}")
    if record.user_id != user.id:
        raise HTTPException(status_code=403, detail="not_authorized_for_conflict")
    return _conflict_response(record)


@router.patch("/conflicts/{conflict_id}", response_model=ConflictRecordResponse)
async def update_conflict(
    conflict_id: str,
    body: dict,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    record = await db.scalar(select(ConflictRecord).where(ConflictRecord.id == conflict_id))
    if record is None:
        raise HTTPException(status_code=404, detail=f"conflict_not_found: {conflict_id}")
    if record.user_id != user.id:
        raise HTTPException(status_code=403, detail="not_authorized_for_conflict")
    new_status = body.get("status")
    if new_status in {"open", "acknowledged", "resolved", "ignored"}:
        record.status = new_status
        if new_status == "resolved":
            record.resolved_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(record)
    return _conflict_response(record)


def _relation_response(relation: MemoryRelation) -> MemoryRelationResponse:
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


def _conflict_response(record: ConflictRecord) -> ConflictRecordResponse:
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


async def _load_user_memory(db: AsyncSession, memory_id: str, user_id: str) -> CommittedMemory:
    memory = await db.scalar(select(CommittedMemory).where(CommittedMemory.id == memory_id))
    if memory is None:
        raise HTTPException(status_code=404, detail=f"memory_not_found: {memory_id}")
    if memory.user_id != user_id:
        raise HTTPException(status_code=403, detail="not_authorized_for_memory")
    return memory


async def _assert_memory_owned(db: AsyncSession, memory_id: str, user_id: str) -> None:
    await _load_user_memory(db, memory_id, user_id)
