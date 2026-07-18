"""Response contracts for the knowledge workspace's new APIs."""

from typing import Literal, Optional

from pydantic import BaseModel, Field


class GraphNodeResponse(BaseModel):
    id: str
    title: str
    memory_type: str
    importance: float
    confidence: float
    sensitivity: str
    occurred_at: Optional[str] = None


class GraphEdgeResponse(BaseModel):
    id: str
    source: str
    target: str
    relation_type: str
    confidence: float
    reason: Optional[str] = None
    valid_from: Optional[str] = None
    valid_until: Optional[str] = None
    created_at: Optional[str] = None


class KnowledgeGraphResponse(BaseModel):
    nodes: list[GraphNodeResponse]
    edges: list[GraphEdgeResponse]
    truncated: bool = False


class TimelineEntryResponse(BaseModel):
    memory_id: str
    title: str
    memory_type: str
    occurred_at: str
    time_basis: Literal["occurred_at", "recorded_at"]
    confidence: float
    importance: float
    tags: list[str] = Field(default_factory=list)
    epistemic_status: Optional[str] = None


class TimelineResponse(BaseModel):
    entries: list[TimelineEntryResponse]
    truncated: bool = False


class WikiPageListItem(BaseModel):
    slug: str
    title: str
    summary: str
    confidence: float
    source_count: int
    generated_at: str
    confidence_state: Literal["low", "review", "supported"]
    last_change_reason: Optional[str] = None
    related_slugs: list[str] = Field(default_factory=list)


class WikiPageDetailResponse(WikiPageListItem):
    memories: list[dict]
    source_refs: list[dict]
    version_history: list[dict] = Field(default_factory=list)


class WikiRebuildResponse(BaseModel):
    page_count: int
    association_count: int
    generated_at: str
