"""Read and rebuild endpoints for the source-backed knowledge workspace."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from src.cognition.schemas.knowledge_workspace import (
    KnowledgeGraphResponse,
    TimelineResponse,
    WikiPageDetailResponse,
    WikiPageListItem,
    WikiRebuildResponse,
)
from src.cognition.services.knowledge_workspace import KnowledgeWorkspaceService
from src.shared.db.database import get_db
from src.shared.security.dependencies import get_current_user


router = APIRouter()


@router.get("/graph", response_model=KnowledgeGraphResponse)
async def get_graph(
    limit: int = Query(default=120, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    return await KnowledgeWorkspaceService(db).graph(user.id, limit=limit)


@router.get("/timeline", response_model=TimelineResponse)
async def get_timeline(
    limit: int = Query(default=100, ge=1, le=300),
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    return await KnowledgeWorkspaceService(db).timeline(user.id, limit=limit)


@router.get("/wiki", response_model=list[WikiPageListItem])
async def list_wiki(
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    return await KnowledgeWorkspaceService(db).list_wiki(user.id)


@router.get("/wiki/{slug}", response_model=WikiPageDetailResponse)
async def get_wiki_page(
    slug: str,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    page = await KnowledgeWorkspaceService(db).wiki_detail(user.id, slug)
    if page is None:
        raise HTTPException(status_code=404, detail="knowledge_page_not_found")
    return page


@router.post("/wiki/rebuild", response_model=WikiRebuildResponse)
async def rebuild_wiki(
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    return await KnowledgeWorkspaceService(db).rebuild_wiki(user.id)
