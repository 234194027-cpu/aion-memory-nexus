from pydantic import BaseModel
from typing import Optional, List

class ObsidianSyncRequest(BaseModel):
    memory_ids: Optional[List[str]] = None
    sync_type: Optional[str] = "both"

class ObsidianSyncResponse(BaseModel):
    success: bool
    exported_count: int
    imported_count: int