"""Read-only user data portability endpoint."""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from src.memory.services.data_portability import DataPortabilityService
from src.shared.db.database import get_db
from src.shared.security.dependencies import get_current_user


router = APIRouter()


@router.get("/export")
async def export_account_data(
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    payload = await DataPortabilityService(db).export_for_user(user.id)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return JSONResponse(
        content=payload,
        headers={"Content-Disposition": f'attachment; filename="life-memory-export-{timestamp}.json"'},
    )
