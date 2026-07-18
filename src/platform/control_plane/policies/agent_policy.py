"""Agent Permission Policy - Agent权限控制策略

决定哪些 Agent 可以做什么

控制：
- Codex 能不能写 Event
- OpenClaw 能不能访问 memory
- Claude 能不能做 retrieval
- 是否允许 tool execution

输出：permission_policy
"""

import logging
from typing import Dict, Any

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_

from src.platform.control_plane.decision import DecisionAction
from src.execution.models.agent_permission import AgentPermission

logger = logging.getLogger(__name__)


# 默认权限配置
DEFAULT_AGENT_PERMISSIONS = {
    "codex": {
        "read_memory": True,
        "add_memory": False,  # 需要通过 Control Plane
        "update_memory": False,
        "delete_memory": False,
        "read_decision": True,
        "create_decision": True,
        "update_decision": True,
        "read_task": True,
        "create_task": True,
        "update_task": True,
        "link_task": True,
        "send_message": True,
        "execute_code": True,
    },
    "openclaw": {
        "read_memory": True,
        "add_memory": False,
        "update_memory": False,
        "delete_memory": False,
        "read_decision": True,
        "create_decision": False,
        "update_decision": False,
        "read_task": True,
        "create_task": False,
        "update_task": False,
        "link_task": False,
        "send_message": True,
        "execute_code": False,
    },
    "chatgpt": {
        "read_memory": True,
        "add_memory": False,
        "update_memory": False,
        "delete_memory": False,
        "read_decision": True,
        "create_decision": False,
        "update_decision": False,
        "read_task": True,
        "create_task": False,
        "update_task": False,
        "link_task": False,
        "send_message": True,
        "execute_code": False,
    },
}


class AgentPermissionPolicy:
    """Agent权限控制策略
    
    管理 Agent 的工具访问权限
    """

    def __init__(self, db: AsyncSession):
        self.db = db

    async def check_permission(
        self,
        agent_id: str,
        tool_name: str,
        user_id: str,
    ) -> Dict[str, Any]:
        """检查 Agent 是否允许使用工具
        
        Args:
            agent_id: Agent ID
            tool_name: 工具名称
            user_id: 用户ID
            
        Returns:
            决策结果字典
        """
        # 1. 查找显式权限配置
        result = await self.db.execute(
            select(AgentPermission).where(
                and_(
                    AgentPermission.user_id == user_id,
                    AgentPermission.agent_id == agent_id,
                    AgentPermission.tool_name == tool_name,
                )
            )
        )
        permission = result.scalar_one_or_none()
        
        if permission is not None:
            # 有显式配置
            if permission.scope == "allow":
                return {
                    "action": DecisionAction.ALLOW,
                    "reason": "explicit_allow",
                    "metadata": {
                        "agent_id": agent_id,
                        "tool_name": tool_name,
                        "source": "explicit_allow",
                    },
                }
            else:
                return {
                    "action": DecisionAction.REJECT,
                    "reason": "explicit_deny",
                    "metadata": {
                        "agent_id": agent_id,
                        "tool_name": tool_name,
                        "source": "explicit_deny",
                    },
                }
        
        # 2. 查找默认权限配置
        default_perms = DEFAULT_AGENT_PERMISSIONS.get(agent_id, {})
        if tool_name in default_perms:
            allowed = default_perms[tool_name]
            if allowed:
                return {
                    "action": DecisionAction.ALLOW,
                    "reason": "default_allow",
                    "metadata": {
                        "agent_id": agent_id,
                        "tool_name": tool_name,
                        "source": "default_allow",
                    },
                }
            else:
                return {
                    "action": DecisionAction.REJECT,
                    "reason": "default_deny",
                    "metadata": {
                        "agent_id": agent_id,
                        "tool_name": tool_name,
                        "source": "default_deny",
                    },
                }
        
        # 3. 未知 Agent 或工具，默认拒绝
        return {
            "action": DecisionAction.REJECT,
            "reason": "unknown_agent_or_tool",
            "metadata": {
                "agent_id": agent_id,
                "tool_name": tool_name,
                "source": "default_deny",
            },
        }

    async def get_agent_capabilities(
        self,
        agent_id: str,
        user_id: str,
    ) -> Dict[str, bool]:
        """获取 Agent 的所有能力
        
        Args:
            agent_id: Agent ID
            user_id: 用户ID
            
        Returns:
            工具名称到是否允许的映射
        """
        # 获取所有显式配置
        result = await self.db.execute(
            select(AgentPermission).where(
                and_(
                    AgentPermission.user_id == user_id,
                    AgentPermission.agent_id == agent_id,
                )
            )
        )
        permissions = result.scalars().all()
        
        # 构建权限映射
        capabilities = {}
        explicit_tools = set()
        
        for perm in permissions:
            capabilities[perm.tool_name] = (perm.scope == "allow")
            explicit_tools.add(perm.tool_name)
        
        # 补充默认权限
        default_perms = DEFAULT_AGENT_PERMISSIONS.get(agent_id, {})
        for tool_name, allowed in default_perms.items():
            if tool_name not in explicit_tools:
                capabilities[tool_name] = allowed
        
        return capabilities
