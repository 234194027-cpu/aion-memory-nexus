from fastapi import Depends, HTTPException
from fastapi.security import OAuth2PasswordBearer, APIKeyHeader
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError
from sqlalchemy import select
from datetime import datetime, timezone

from src.shared.config import settings
from src.shared.db.database import get_db
from src.execution.models.user import User
from src.execution.models.agent_profile import AgentProfile
from src.shared.security.auth import decode_access_token, get_password_hash

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login", auto_error=False)
agent_api_key_header = APIKeyHeader(name="X-Agent-Token", auto_error=False)

# 单用户模式：固定用户 ID
SOLO_USER_ID = "solo-user"

async def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db)
) -> User:
    """Resolve the only local owner in solo mode, otherwise use JWT auth."""
    if settings.SOLO_MODE:
        return await _get_or_create_solo_user(db)

    if token:
        payload = decode_access_token(token)
        if payload is None or "user_id" not in payload:
            raise HTTPException(status_code=401, detail="Invalid or expired token")

        result = await db.execute(select(User).where(User.id == payload["user_id"]))
        user = result.scalar_one_or_none()
        if user is None or not user.is_active:
            raise HTTPException(status_code=401, detail="User not found or inactive")
        return user

    if not settings.ALLOW_DEV_AUTH_FALLBACK:
        raise HTTPException(status_code=401, detail="Authentication required")

    return await _get_or_create_solo_user(db)


async def get_graph_admin_user(
    user: User = Depends(get_current_user),
) -> User:
    """Allow disposable graph operations only to the configured owner.

    Solo mode has exactly one local owner. Multi-user deployments must opt in
    with explicit immutable user IDs; an empty configuration fails closed.
    """
    if settings.SOLO_MODE:
        return user
    admin_ids = {
        item.strip()
        for item in settings.GRAPHITI_ADMIN_USER_IDS.split(",")
        if item.strip()
    }
    if user.id not in admin_ids:
        raise HTTPException(status_code=403, detail="graph_admin_required")
    return user


async def get_current_user_or_agent_owner(
    token: str = Depends(oauth2_scheme),
    agent_token: str = Depends(agent_api_key_header),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Resolve a user from JWT/solo auth or from an authenticated Agent token."""
    if agent_token:
        agent = await _get_agent_by_token(db, agent_token)
        result = await db.execute(select(User).where(User.id == agent.user_id))
        user = result.scalar_one_or_none()
        if user is None or not user.is_active:
            raise HTTPException(status_code=401, detail="Agent owner not found or inactive")
        return user

    return await get_current_user(token=token, db=db)

async def _get_or_create_solo_user(db: AsyncSession) -> User:
    # 先尝试查找固定 ID 的用户
    result = await db.execute(select(User).where(User.id == SOLO_USER_ID))
    user = result.scalar_one_or_none()
    if user is not None:
        return user
    
    # 如果固定 ID 用户不存在，查找数据库中任意用户
    result = await db.execute(select(User).limit(1))
    user = result.scalar_one_or_none()
    if user is not None:
        return user
    
    # 如果数据库为空，创建默认用户
    try:
        user = User(
            id=SOLO_USER_ID,
            email="solo@local",
            hashed_password=get_password_hash("solo"),
            is_active=True,
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)
    except IntegrityError:
        await db.rollback()
        # 用户已存在，重新查询
        result = await db.execute(select(User).limit(1))
        user = result.scalar_one()
    
    return user

async def get_current_agent(
    token: str = Depends(agent_api_key_header),
    db: AsyncSession = Depends(get_db)
) -> AgentProfile:
    """
    支持两种认证方式：
    1. 系统级 Token：使用 SYSTEM_API_TOKEN，自动匹配默认 Agent
    2. Agent 级 Token：使用具体 Agent 的 token
    """
    if token is None:
        raise HTTPException(status_code=401, detail="Agent token required")

    return await _get_agent_by_token(db, token)


async def _get_agent_by_token(db: AsyncSession, token: str) -> AgentProfile:
    # 先检查是否为系统级 Token
    from src.shared.config import get_system_api_token
    system_token = get_system_api_token()
    if token == system_token:
        # 系统级 Token，必须绑定默认 Agent（is_default=True），避免越权绑定到非默认 Agent
        result = await db.execute(
            select(AgentProfile).where(
                AgentProfile.is_default.is_(True),
                AgentProfile.status.is_(True)
            ).limit(1)
        )
        agent = result.scalar_one_or_none()
        if agent is None:
            # 兜底：若默认 Agent 不存在，回退到任意 active Agent（保证可用性）
            result = await db.execute(
                select(AgentProfile).where(
                    AgentProfile.status.is_(True)
                ).limit(1)
            )
            agent = result.scalar_one_or_none()
            if agent is None:
                raise HTTPException(status_code=401, detail="No active agent found for system token")
        agent.last_seen_at = datetime.now(timezone.utc)
        await db.commit()
        return agent

    # 否则按 Agent 级 Token 认证
    from src.shared.security.auth import hash_token
    token_hash = hash_token(token)
    result = await db.execute(
        select(AgentProfile).where(
            AgentProfile.api_token_hash == token_hash,
            AgentProfile.status.is_(True)
        )
    )
    agent = result.scalar_one_or_none()
    if agent is None:
        raise HTTPException(status_code=401, detail="Invalid agent token")
    agent.last_seen_at = datetime.now(timezone.utc)
    await db.commit()
    return agent
