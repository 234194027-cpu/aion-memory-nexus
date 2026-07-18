from pydantic import BaseModel
from typing import Optional

class EventCreate(BaseModel):
    source_type: str
    agent_id: Optional[str] = None
    project_id: Optional[str] = None
    repo_id: Optional[str] = None
    content: str
    event_metadata: Optional[dict] = None
    sensitivity: Optional[str] = "normal"
    visibility_scope: Optional[str] = "project"

class EventResponse(BaseModel):
    event_id: str
    processing_status: str