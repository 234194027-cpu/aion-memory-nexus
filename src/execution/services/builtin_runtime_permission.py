"""Permission adapter for fixed internal profiles without granting external agents new tools."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.execution.models.agent_permission import AgentPermission


CONVERSATIONAL_RUNTIME_ID = "runtime:conversational"
WORKING_RUNTIME_ID = "runtime:working"


class BuiltinRuntimePermissionService:
    """Read-only internal tools are allowed unless a user explicitly denies the fixed profile."""

    def __init__(self, db: AsyncSession, *, allowed_tools: frozenset[str]) -> None:
        self.db = db
        self.allowed_tools = allowed_tools

    async def check(self, user_id: str, agent_id: str, tool_name: str) -> dict[str, object]:
        if agent_id not in {CONVERSATIONAL_RUNTIME_ID, WORKING_RUNTIME_ID} or tool_name not in self.allowed_tools:
            return {"allowed": False, "source": "profile_deny"}
        permission = (await self.db.execute(
            select(AgentPermission).where(
                AgentPermission.user_id == user_id,
                AgentPermission.agent_id == agent_id,
                AgentPermission.tool_name == tool_name,
            )
        )).scalar_one_or_none()
        if permission is not None and permission.scope == "deny":
            return {"allowed": False, "source": "explicit_deny"}
        return {"allowed": True, "source": "built_in_read_only"}
