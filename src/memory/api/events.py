from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete, func
from datetime import datetime, timezone
from src.shared.db.database import get_db
from src.memory.models.raw_event import RawEvent, SourceType, SensitivityLevel, VisibilityScope, ProcessingStatus
from src.memory.models.memory_source import MemorySource
from src.memory.models.committed_memory import CommittedMemory
from src.execution.models.memory_work import MemoryWorkCase, MemoryWorkEvidence
from src.memory.services.deletion_service import (
    delete_from_vector_index,
    record_lifecycle_audit,
    tombstone_memory,
)
from src.memory.schemas.events import EventCreate, EventResponse
from src.shared.security.dependencies import get_current_user
from src.memory.services.event_ingestion import EventIngestionService, trigger_ingested_event

router = APIRouter()

@router.post("/", response_model=EventResponse)
async def create_event(
    event: EventCreate,
    db: AsyncSession = Depends(get_db),
    user = Depends(get_current_user),
):
    try:
        source_type = SourceType(event.source_type)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid source_type")
    
    try:
        sensitivity = SensitivityLevel(event.sensitivity)
    except ValueError:
        sensitivity = SensitivityLevel.NORMAL
    
    try:
        visibility_scope = VisibilityScope(event.visibility_scope)
    except ValueError:
        visibility_scope = VisibilityScope.PROJECT
    
    try:
        ingested = await EventIngestionService(db).append(
            user_id=user.id,
            content=event.content,
            source_type=source_type,
            source_id=event.agent_id,
            agent_id=event.agent_id,
            project_id=event.project_id,
            repo_id=event.repo_id,
            event_metadata=event.event_metadata or {},
            sensitivity=sensitivity,
            visibility_scope=visibility_scope,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await db.commit()
    trigger_ingested_event(ingested.event.id)
    
    return {"event_id": ingested.event.id, "processing_status": "queued"}

@router.get("/")
async def list_events(
    page: int = 1,
    page_size: int = 20,
    limit: int = None,
    db: AsyncSession = Depends(get_db),
    user = Depends(get_current_user),
):
    # 兼容旧 limit 参数：传了 limit 就用 limit 作为 page_size，page=1
    if limit is not None:
        page = 1
        page_size = min(max(limit, 1), 500)
    else:
        page = max(page, 1)
        page_size = min(max(page_size, 1), 100)

    offset = (page - 1) * page_size

    # 总数查询
    count_result = await db.execute(
        select(func.count(RawEvent.id)).where(RawEvent.user_id == user.id)
    )
    total = count_result.scalar_one()

    # 分页查询
    result = await db.execute(
        select(RawEvent)
        .where(RawEvent.user_id == user.id)
        .order_by(RawEvent.ingested_at.desc())
        .offset(offset)
        .limit(page_size)
    )
    events = result.scalars().all()
    return {
        "items": [
            {
                "id": event.id,
                "source_type": event.source_type.value if event.source_type else None,
                "source_id": event.source_id,
                "agent_id": event.agent_id,
                "project_id": event.project_id,
                "repo_id": event.repo_id,
                "occurred_at": event.occurred_at,
                "ingested_at": event.ingested_at,
                "content": event.content,
                "metadata": event.event_metadata or {},
                "sensitivity": event.sensitivity.value if event.sensitivity else None,
                "visibility_scope": event.visibility_scope.value if event.visibility_scope else None,
                "processing_status": event.processing_status.value if event.processing_status else None,
            }
            for event in events
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
    }

@router.get("/{event_id}")
async def get_event(
    event_id: str,
    db: AsyncSession = Depends(get_db),
    user = Depends(get_current_user),
):
    result = await db.execute(
        RawEvent.__table__.select().where(RawEvent.id == event_id).where(RawEvent.user_id == user.id)
    )
    event = result.mappings().first()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    return dict(event)

@router.delete("/{event_id}")
async def delete_event(
    event_id: str,
    db: AsyncSession = Depends(get_db),
    user = Depends(get_current_user),
):
    """删除指定事件、来源链接和仅依赖该事件的工作案件。"""
    result = await db.execute(
        select(RawEvent).where(RawEvent.id == event_id).where(RawEvent.user_id == user.id)
    )
    event = result.scalar_one_or_none()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    source_result = await db.execute(
        select(MemorySource).where(MemorySource.raw_event_id == event_id)
    )
    event_sources = source_result.scalars().all()
    affected_memory_ids = {source.memory_id for source in event_sources}
    tombstoned_memory_ids = []
    tombstone_counts: dict[str, dict[str, int]] = {}
    for memory_id in affected_memory_ids:
        other_source = await db.scalar(
            select(MemorySource.id)
            .where(MemorySource.memory_id == memory_id)
            .where(MemorySource.raw_event_id != event_id)
            .limit(1)
        )
        if other_source is None:
            memory = await db.scalar(
                select(CommittedMemory)
                .where(CommittedMemory.id == memory_id)
                .where(CommittedMemory.user_id == user.id)
            )
            if memory is not None:
                tombstone_counts[memory.id] = await tombstone_memory(db, memory)
                tombstoned_memory_ids.append(memory.id)

    await db.execute(delete(MemorySource).where(MemorySource.raw_event_id == event_id))

    from src.memory.services.graph_projection import queue_source_deletion

    await queue_source_deletion(
        db,
        user_id=event.user_id,
        project_id=event.project_id,
        source_kind="raw_event",
        source_id=event.id,
        source_revision=event.content_hash or event.id,
    )

    case_ids = list(
        (
            await db.execute(
                select(MemoryWorkEvidence.case_id).where(
                    MemoryWorkEvidence.user_id == user.id,
                    MemoryWorkEvidence.raw_event_id == event_id,
                )
            )
        ).scalars()
    )
    await db.execute(
        delete(MemoryWorkEvidence).where(
            MemoryWorkEvidence.user_id == user.id,
            MemoryWorkEvidence.raw_event_id == event_id,
        )
    )

    # 删除事件本身
    await db.delete(event)
    await db.flush()
    deleted_case_count = 0
    for case_id in set(case_ids):
        remaining = await db.scalar(
            select(func.count(MemoryWorkEvidence.id)).where(
                MemoryWorkEvidence.case_id == case_id,
                MemoryWorkEvidence.user_id == user.id,
            )
        )
        if not remaining:
            case = await db.scalar(
                select(MemoryWorkCase).where(
                    MemoryWorkCase.id == case_id,
                    MemoryWorkCase.user_id == user.id,
                )
            )
            if case is not None:
                await db.delete(case)
                deleted_case_count += 1
    for memory_id in tombstoned_memory_ids:
        await record_lifecycle_audit(
            db,
            user_id=user.id,
            action="raw_event_delete_tombstone",
            target_type="committed_memory",
            target_id=memory_id,
            affected_counts=tombstone_counts[memory_id],
        )
    await record_lifecycle_audit(
        db,
        user_id=user.id,
        action="delete",
        target_type="raw_event",
        target_id=event_id,
        affected_counts={
            "memory_work_cases_deleted": deleted_case_count,
            "committed_memories_tombstoned": len(tombstoned_memory_ids),
        },
    )
    await db.commit()
    for memory_id in tombstoned_memory_ids:
        delete_from_vector_index(memory_id)

    return {"status": "deleted", "event_id": event_id}
