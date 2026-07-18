"""Route registration and module assembly"""
from fastapi import FastAPI

from src.memory.api.events import router as events_router
from src.memory.api.memories import router as memories_router
from src.memory.api.governance import router as governance_router
from src.execution.api.agents import router as agents_router
from src.memory.api.obsidian import router as obsidian_router
from src.platform.api.admin import router as admin_router
from src.platform.api.media import router as media_router
from src.platform.api.wecom_channel import router as wecom_router
from src.cognition.api.persona import router as persona_router
from src.cognition.api.memory_governance import router as memory_governance_router
from src.cognition.api.cognitive_advisor import router as cognitive_advisor_router
from src.execution.api.cognitive_os import router as cognitive_os_router
from src.execution.api.cognitive_orchestration import router as cognitive_orchestration_router
from src.execution.api.runtime import router as runtime_router
from src.cognition.api.daily_briefing import router as daily_briefing_router
from src.cognition.api.weekly_review import router as weekly_review_router
from src.platform.api.system import router as system_router
from src.platform.api.auth import router as auth_router
from src.memory.api.ingest import router as ingest_router
from src.memory.api.graph import router as graph_router
from src.memory.api.embedding import router as embedding_router
from src.memory.api.data_portability import router as data_portability_router
from src.platform.api.control_plane import router as control_plane_router
from src.platform.api.cognitive_control import router as cognitive_control_router
from src.cognition.api.knowledge_workspace import router as knowledge_workspace_router


def register_routes(app: FastAPI):
    """Register all API routes"""
    app.include_router(events_router, prefix="/api/events", tags=["events"])
    # Register static /api/memory/* governance routes before /api/memory/{memory_id}.
    app.include_router(memory_governance_router, prefix="/api/memory", tags=["memory-governance"])
    app.include_router(memories_router, prefix="/api/memory", tags=["memory"])
    app.include_router(governance_router, prefix="/api/governance", tags=["governance"])
    app.include_router(agents_router, prefix="/api/agent", tags=["agents"])
    app.include_router(obsidian_router, prefix="/api/obsidian", tags=["obsidian"])
    app.include_router(data_portability_router, prefix="/api/data-portability", tags=["data-portability"])
    app.include_router(admin_router, prefix="/api/admin", tags=["admin"])
    app.include_router(media_router, prefix="/api/media", tags=["media"])
    app.include_router(wecom_router, prefix="/api/wecom", tags=["wecom"])
    app.include_router(auth_router, prefix="/api/auth", tags=["auth"])
    app.include_router(persona_router, prefix="/api/persona", tags=["persona"])
    app.include_router(cognitive_advisor_router, prefix="/api/advisor", tags=["advisor"])
    app.include_router(cognitive_os_router, prefix="/api/os", tags=["cognitive-os"])
    app.include_router(cognitive_orchestration_router, prefix="/api/orchestration", tags=["orchestration"])
    app.include_router(runtime_router, prefix="/api/runtime", tags=["runtime"])
    app.include_router(daily_briefing_router, prefix="/api/daily", tags=["daily-briefing"])
    app.include_router(weekly_review_router, prefix="/api/advisor/review", tags=["weekly-review"])
    
    # CIP (Cognitive Ingestion Protocol)
    app.include_router(ingest_router, prefix="/api", tags=["cip-ingest"])
    app.include_router(embedding_router, prefix="/api", tags=["embedding"])
    # Internal owner operations only; no Graphiti/MCP write route is registered.
    app.include_router(graph_router, prefix="/api/graph", tags=["graph-projection"])
    
    # Control Plane
    app.include_router(control_plane_router, prefix="/api/control-plane", tags=["control-plane"])
    app.include_router(cognitive_control_router, prefix="/api", tags=["cognitive-control"])
    app.include_router(knowledge_workspace_router, prefix="/api/knowledge-workspace", tags=["knowledge-workspace"])
    
    # System
    app.include_router(system_router, prefix="/api", tags=["system"])
