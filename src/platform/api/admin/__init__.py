"""Admin API routes - split into sub-modules for maintainability."""
from fastapi import APIRouter

router = APIRouter()

# Import and include sub-routers from new locations
from src.platform.api.admin import agents, llm_providers, wecom, system  # noqa: E402

router.include_router(agents.router, prefix="/agents", tags=["admin-agents"])
router.include_router(llm_providers.router, prefix="/custom-llm-providers", tags=["admin-llm-providers"])
router.include_router(wecom.router, prefix="/wecom", tags=["admin-wecom"])
router.include_router(system.router, prefix="/system", tags=["admin-system"])
