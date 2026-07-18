"""CIP (Cognitive Ingestion Protocol) — Unified Ingestion API.

TASK 1 + TASK 7: POST /api/event/ingest

All external inputs MUST go through this endpoint.
Three supported sources: chat, obsidian, agent.

Core Rules (CIP):
  - ALL inputs → RawEvent (Event Layer)
  - NO direct Memory writes allowed
  - NO structuring at ingestion time
  - Memory Agent processes events asynchronously
"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.shared.db.database import get_db
from src.memory.models.raw_event import SourceType, SensitivityLevel, VisibilityScope
from src.memory.schemas.ingest import IngestEventRequest, IngestEventResponse
from src.shared.security.dependencies import get_current_user
from src.memory.services.event_ingestion import EventIngestionService, trigger_ingested_event

router = APIRouter()

# source → SourceType mapping
SOURCE_MAP = {
    "chat": SourceType.MANUAL,
    "obsidian": SourceType.OBSIDIAN,
    "agent": SourceType.AGENT_API,
}

# agent_type sub-mapping for finer granularity
AGENT_TYPE_MAP = {
    "codex": SourceType.CODEX,
    "openclaw": SourceType.OPENCLAW,
    "chatgpt": SourceType.CHATGPT,
}


@router.post("/event/ingest", response_model=IngestEventResponse)
async def ingest_event(
    request: IngestEventRequest,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """
    CIP Unified Ingestion Endpoint.

    This is the ONLY entry point for all external inputs.
    Every input becomes a RawEvent. No exceptions.

    Supported sources:
      - chat:     User direct input (chat messages)
      - obsidian: Notes from Obsidian / external note systems
      - agent:    Agent outputs (Codex, OpenClaw, MCP, etc.)

    Rules:
      - NEVER writes to Memory or CommittedMemory
      - NEVER structures or extracts meaning
      - ONLY writes to RawEvent table
      - Memory Agent processes asynchronously after ingestion
    """
    # Map source to SourceType enum
    if request.source == "agent" and request.agent_type:
        source_type = AGENT_TYPE_MAP.get(request.agent_type, SourceType.AGENT_API)
    else:
        source_type = SOURCE_MAP.get(request.source, SourceType.MANUAL)

    # Merge metadata with source info
    metadata = dict(request.event_metadata or {})
    metadata["cip_source"] = request.source
    if request.agent_type:
        metadata["agent_type"] = request.agent_type

    sensitivity = SensitivityLevel.NORMAL
    sens_str = metadata.get("sensitivity", "normal")
    try:
        sensitivity = SensitivityLevel(sens_str)
    except ValueError:
        pass

    visibility = VisibilityScope.PROJECT
    vis_str = metadata.get("visibility_scope", "project")
    try:
        visibility = VisibilityScope(vis_str)
    except ValueError:
        pass

    ingested = await EventIngestionService(db).append(
        user_id=user.id,
        content=request.content,
        source_type=source_type,
        source_id=request.agent_type or request.source,
        agent_id=request.agent_type,
        project_id=metadata.get("project_id"),
        repo_id=metadata.get("repo_id"),
        event_metadata=metadata,
        sensitivity=sensitivity,
        visibility_scope=visibility,
    )
    await db.commit()
    trigger_ingested_event(ingested.event.id)

    return IngestEventResponse(
        event_id=ingested.event.id,
        source=request.source,
        processing_status="queued",
    )
