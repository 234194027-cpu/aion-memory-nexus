from pydantic import BaseModel
from typing import Optional, List

class AgentBeforeStartRequest(BaseModel):
    agent_id: str
    task: str
    project_id: Optional[str] = None
    repo_id: Optional[str] = None
    recall_level: Optional[str] = "work_context"
    top_k: Optional[int] = 8

class AgentBeforeStartResponse(BaseModel):
    context_pack: dict

class AgentAfterEndRequest(BaseModel):
    agent_id: str
    project_id: Optional[str] = None
    repo_id: Optional[str] = None
    workspace_id: Optional[str] = None
    session_summary: str
    decisions: Optional[List[dict]] = []
    actions: Optional[List[dict]] = []
    artifacts: Optional[List[dict]] = []
    raw_transcript_ref: Optional[str] = None

class AgentAfterEndResponse(BaseModel):
    event_id: str
    formal_memory_count: int
    processing_status: str
    message: Optional[str] = None

class AgentCreateRequest(BaseModel):
    agent_name: str
    agent_type: str = "custom"
    default_recall_level: str = "work_context"
    llm_provider: Optional[str] = None
    llm_model: Optional[str] = None
    llm_api_key: Optional[str] = None
    llm_api_base: Optional[str] = None
    llm_temperature: float = 0.7
    llm_max_tokens: int = 4096
    mission: Optional[str] = None
    role: Optional[str] = None
    goals: Optional[str] = None
    constraints: Optional[str] = None
    instructions: Optional[str] = None

class AgentUpdateRequest(BaseModel):
    agent_name: Optional[str] = None
    agent_type: Optional[str] = None
    default_recall_level: Optional[str] = None
    llm_provider: Optional[str] = None
    llm_model: Optional[str] = None
    llm_api_key: Optional[str] = None
    llm_api_base: Optional[str] = None
    llm_temperature: Optional[float] = None
    llm_max_tokens: Optional[int] = None
    mission: Optional[str] = None
    role: Optional[str] = None
    goals: Optional[str] = None
    constraints: Optional[str] = None
    instructions: Optional[str] = None
