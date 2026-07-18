"""Tool Permission Service (Gen 3).

管理哪些 agent 能用哪些工具:
- read_memory / add_memory / update_memory / delete_memory
- read_decision / create_decision / update_decision
- read_task / create_task / update_task / link_task
- send_message / execute_code

策略: deny-by-default
- 有显式 allow -> 允许
- 有显式 deny  -> 拒绝
- 否则默认 deny

grant 自动去重: unique on (agent_id, tool_name), 重复 grant 会覆盖 scope。
revoke 不存在不报错 (返回 None)。
"""
from __future__ import annotations

from typing import Dict, List, Optional

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.execution.models.agent_permission import AgentPermission
from src.shared.ids.id_generator import generate_agent_permission_id


AVAILABLE_TOOLS = {
    "read_memory",
    "add_memory",
    "update_memory",
    "delete_memory",
    "read_decision",
    "create_decision",
    "update_decision",
    "read_task",
    "create_task",
    "update_task",
    "link_task",
    "manage_task",
    "send_message",
    "execute_code",
    "read_file",
}

VALID_SCOPES = {"allow", "deny"}


class ToolPermissionService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def grant(
        self,
        user_id: str,
        agent_id: str,
        tool_name: str,
        scope: str = "allow",
    ) -> AgentPermission:
        if tool_name not in AVAILABLE_TOOLS:
            raise ValueError(
                f"unknown tool: {tool_name}; must be one of {sorted(AVAILABLE_TOOLS)}"
            )
        if scope not in VALID_SCOPES:
            raise ValueError(f"invalid scope: {scope}; must be one of {sorted(VALID_SCOPES)}")

        existing = await self._find(user_id=user_id, agent_id=agent_id, tool_name=tool_name)
        if existing is not None:
            if existing.scope != scope:
                existing.scope = scope
                await self.db.commit()
                await self.db.refresh(existing)

            from src.execution.services.audit_logger import AuditLogger
            await AuditLogger.log(
                self.db,
                user_id=user_id,
                action="permission_grant",
                actor_type="user",
                actor_id=user_id,
                target_type="agent_permission",
                target_id=existing.id,
                detail={"agent_id": agent_id, "tool_name": tool_name, "scope": scope},
            )
            return existing

        perm = AgentPermission(
            id=generate_agent_permission_id(),
            user_id=user_id,
            agent_id=agent_id,
            tool_name=tool_name,
            scope=scope,
        )
        self.db.add(perm)
        await self.db.commit()
        await self.db.refresh(perm)

        from src.execution.services.audit_logger import AuditLogger
        await AuditLogger.log(
            self.db,
            user_id=user_id,
            action="permission_grant",
            actor_type="user",
            actor_id=user_id,
            target_type="agent_permission",
            target_id=perm.id,
            detail={"agent_id": agent_id, "tool_name": tool_name, "scope": scope},
        )

        return perm

    async def revoke(
        self,
        user_id: str,
        agent_id: str,
        tool_name: str,
    ) -> None:
        existing = await self._find(user_id=user_id, agent_id=agent_id, tool_name=tool_name)
        if existing is None:
            return
        perm_id = existing.id
        await self.db.delete(existing)
        await self.db.commit()

        from src.execution.services.audit_logger import AuditLogger
        await AuditLogger.log(
            self.db,
            user_id=user_id,
            action="permission_revoke",
            actor_type="user",
            actor_id=user_id,
            target_type="agent_permission",
            target_id=perm_id,
            detail={"agent_id": agent_id, "tool_name": tool_name},
        )

    async def list_permissions(
        self,
        user_id: str,
        agent_id: Optional[str] = None,
    ) -> List[AgentPermission]:
        conditions = [AgentPermission.user_id == user_id]
        if agent_id:
            conditions.append(AgentPermission.agent_id == agent_id)
        result = await self.db.execute(
            select(AgentPermission)
            .where(and_(*conditions))
            .order_by(AgentPermission.created_at.desc())
        )
        return list(result.scalars().all())

    async def check(
        self,
        user_id: str,
        agent_id: str,
        tool_name: str,
    ) -> Dict:
        existing = await self._find(user_id=user_id, agent_id=agent_id, tool_name=tool_name)
        if existing is None:
            return {
                "user_id": user_id,
                "agent_id": agent_id,
                "tool_name": tool_name,
                "allowed": False,
                "source": "default_deny",
            }
        if existing.scope == "deny":
            return {
                "user_id": user_id,
                "agent_id": agent_id,
                "tool_name": tool_name,
                "allowed": False,
                "source": "explicit_deny",
            }
        return {
            "user_id": user_id,
            "agent_id": agent_id,
            "tool_name": tool_name,
            "allowed": True,
            "source": "explicit_allow",
        }

    async def _find(
        self,
        *,
        user_id: str,
        agent_id: str,
        tool_name: str,
    ) -> Optional[AgentPermission]:
        result = await self.db.execute(
            select(AgentPermission).where(
                and_(
                    AgentPermission.user_id == user_id,
                    AgentPermission.agent_id == agent_id,
                    AgentPermission.tool_name == tool_name,
                )
            )
        )
        return result.scalar_one_or_none()
