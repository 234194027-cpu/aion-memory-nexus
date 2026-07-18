"""CIP (Cognitive Ingestion Protocol) schemas.

All external inputs MUST go through POST /api/event/ingest.
Three supported sources: chat, obsidian, agent.
"""

from pydantic import BaseModel, Field
from typing import Optional, Literal


class IngestEventRequest(BaseModel):
    """Unified ingestion request — the ONLY entry point for all inputs.

    source MUST be one of: "chat", "obsidian", "agent"
    """
    source: Literal["chat", "obsidian", "agent"] = Field(
        ...,
        description="Input source: chat | obsidian | agent",
    )
    content: str = Field(
        ...,
        min_length=1,
        max_length=50000,
        description="Raw content to ingest",
    )
    agent_type: Optional[str] = Field(
        None,
        description="Agent type when source=agent (e.g. codex, openclaw, mcp)",
    )
    event_metadata: Optional[dict] = Field(
        default_factory=dict,
        description="Arbitrary metadata (note_type, task_id, status, etc.)",
    )


class IngestEventResponse(BaseModel):
    event_id: str
    source: str
    processing_status: str
    message: str = "Event ingested. Memory Agent will process shortly."


class EmbeddingBackfillRequest(BaseModel):
    batch_size: int = Field(default=20, ge=1, le=100)


class EmbeddingBackfillResponse(BaseModel):
    total_pending: int
    processed: int
    success: int
    failed: int
