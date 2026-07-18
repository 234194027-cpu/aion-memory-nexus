from pydantic import BaseModel
from typing import Optional, List

class MemorySearchRequest(BaseModel):
    query: str
    project_id: Optional[str] = None
    memory_types: Optional[List[str]] = None
    recall_level: Optional[str] = "work_context"
    top_k: Optional[int] = 10
    # 真分页参数（可选）：传了 page 启用真分页，否则保持原 top_k 行为
    page: Optional[int] = None
    page_size: Optional[int] = None

class MemorySearchResponse(BaseModel):
    answer: str
    memories: List[dict]
    source_refs: List[dict]
    confidence: float
    warnings: List[str]
    # 真分页元数据（仅启用真分页时返回）
    total: Optional[int] = None
    page: Optional[int] = None
    page_size: Optional[int] = None

class MemoryForgetRequest(BaseModel):
    action: str
    # supersede 流程可选字段：提供新内容时创建新记忆并标记旧记忆为 SUPERSEDED
    new_title: Optional[str] = None
    new_body: Optional[str] = None
    new_memory_type: Optional[str] = None


class ContextReconstructionRequest(BaseModel):
    question: str
    project_id: Optional[str] = None
    recall_level: Optional[str] = "work_context"
    top_k: Optional[int] = 20


class ContextReconstructionResponse(BaseModel):
    context_summary: str
    decision_history: List[dict]
    patterns: List[dict]
    conflicts: List[dict]
    relevant_memories: List[dict]
    entities: List[str]
    meta: Optional[dict] = None


class MemoryAskRequest(BaseModel):
    question: str
    project_id: Optional[str] = None
    recall_level: Optional[str] = "work_context"
    top_k: Optional[int] = 20
    agent_id: Optional[str] = None
    temperature: Optional[float] = 0.4
    max_tokens: Optional[int] = 1500


class MemoryAskSourceRef(BaseModel):
    memory_id: str
    title: str
    quote: Optional[str] = None
    source_type: Optional[str] = None


class MemoryAskMemoryItem(BaseModel):
    id: str
    title: str
    body: str
    memory_type: str
    confidence: float
    importance: float
    similarity: Optional[float] = None
    tags: Optional[List[str]] = []
    epistemic_status: Optional[str] = None
    valid_from: Optional[str] = None
    valid_until: Optional[str] = None


class MemoryAskResponse(BaseModel):
    answer: str
    confidence: float
    memories: List[MemoryAskMemoryItem]
    source_refs: List[MemoryAskSourceRef]
    context_summary: Optional[str] = None
    warnings: List[str] = []
    meta: dict
