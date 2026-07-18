from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession
from src.shared.db.database import get_db
from src.shared.security.dependencies import get_current_user
import logging

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/config")
async def get_wecom_config(
    db: AsyncSession = Depends(get_db),
    user = Depends(get_current_user),
):
    from src.shared.config import settings
    from src.platform.channels.wecom import get_wecom_bot
    
    bot = get_wecom_bot()
    bot_status = bot.get_status() if bot else {}
    
    return {
        "enabled": bool(settings.WECOM_BOT_ID and settings.WECOM_BOT_SECRET),
        "bot_id": settings.WECOM_BOT_ID[:6] + "..." if settings.WECOM_BOT_ID else "",
        "secret_configured": bool(settings.WECOM_BOT_SECRET),
        "default_agent_id": settings.WECOM_DEFAULT_AGENT_ID,
        "connection_type": "websocket",
        "bot_status": bot_status,
    }


@router.post("/connect")
async def admin_wecom_connect(
    db: AsyncSession = Depends(get_db),
    user = Depends(get_current_user),
):
    from src.platform.channels.wecom_handlers import start_wecom_long_connection

    result = await start_wecom_long_connection()
    if result["status"] == "not_configured":
        raise HTTPException(status_code=400, detail="WeCom bot is not configured")
    return result


@router.post("/disconnect")
async def admin_wecom_disconnect(
    db: AsyncSession = Depends(get_db),
    user = Depends(get_current_user),
):
    from src.platform.channels.wecom import get_wecom_bot
    
    bot = get_wecom_bot()
    if not bot:
        raise HTTPException(status_code=400, detail="WeCom bot is not configured")
    
    from src.platform.channels.wecom_handlers import stop_wecom_long_connection

    result = await stop_wecom_long_connection()
    if result["status"] == "not_configured":
        raise HTTPException(status_code=400, detail="WeCom bot is not configured")
    return result


@router.post("/test-message")
async def test_wecom_message(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user = Depends(get_current_user),
):
    from src.platform.channels.wecom import get_wecom_bot
    
    try:
        body = await request.json()
    except ValueError:
        body = {}
    
    user_id = body.get("user_id")
    content = body.get("content", "这是一条测试消息")
    
    if not user_id:
        raise HTTPException(status_code=400, detail="user_id is required")
    
    bot = get_wecom_bot()
    if not bot:
        raise HTTPException(status_code=400, detail="WeCom bot is not configured")
    
    result = await bot.send_text_message(user_id, content)
    
    return {
        "success": result.get("errcode") == 0,
        "result": result,
    }
