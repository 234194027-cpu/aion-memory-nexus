import asyncio
from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.execution.api.cognitive_orchestration import execute_tool
from src.execution.models.agent_profile import AgentProfile, AgentType
from src.execution.schemas.orchestration import ToolExecuteRequest
from src.execution.services.tool_permission import ToolPermissionService
from src.execution.models.user import User
from src.shared.db.database import async_session, init_db
from src.shared.security.auth import get_password_hash, hash_token


def test_tool_execute_requires_explicit_agent_permission():
    async def run():
        await init_db()
        suffix = uuid4().hex
        user = User(
            id=f"tool-security-user-{suffix}",
            email=f"tool-security-{suffix}@example.com",
            hashed_password=get_password_hash("test123456"),
        )
        agent = AgentProfile(
            id=f"tool-security-agent-{suffix}",
            user_id=user.id,
            agent_name="tool-security-agent",
            agent_type=AgentType.CODEX,
            token_hash=hash_token(f"tool-security-token-{suffix}"),
            api_token_hash=hash_token(f"tool-security-token-{suffix}"),
            status=True,
        )

        async with async_session() as db:
            db.add(user)
            db.add(agent)
            await db.commit()

            request = ToolExecuteRequest(
                agent_id=agent.id,
                tool_name="read_memory",
                params={"query": "nothing"},
            )
            with pytest.raises(HTTPException) as denied:
                await execute_tool(request=request, db=db, user=user)
            assert denied.value.status_code == 403

            service = ToolPermissionService(db)
            await service.grant(user_id=user.id, agent_id=agent.id, tool_name="read_memory")

            allowed = await execute_tool(request=request, db=db, user=user)
            assert allowed.status == "success"

    asyncio.run(run())
