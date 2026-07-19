"""Read-only user data portability endpoint."""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from src.memory.services.data_portability import DataPortabilityService
from src.memory.services.account_deletion import AccountDeletionService
from src.shared.db.database import get_db
from src.shared.security.dependencies import get_current_user


router = APIRouter()


class AccountDeletionRequest(BaseModel):
    confirmation: str


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


@router.delete("/account")
async def delete_account_data(
    payload: AccountDeletionRequest,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    if payload.confirmation.strip() != "删除我的全部数据":
        raise HTTPException(status_code=400, detail="confirmation_phrase_mismatch")
    return await AccountDeletionService(db).delete_for_user(user.id)
