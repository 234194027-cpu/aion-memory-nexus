"""Memory Gate - 记忆门禁控制器

统一控制所有记忆的写入和读取行为

职责：
- commit 是否允许
- write 是否允许
- 提供统一的记忆访问控制入口
"""

import logging
from typing import Dict, Any, List

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from src.platform.control_plane.router import ControlPlaneRouter
from src.platform.control_plane.decision import DecisionAction
from src.memory.models.committed_memory import CommittedMemory, CommittedStatus

logger = logging.getLogger(__name__)


class MemoryGate:
    """记忆门禁控制器
    
    所有记忆的写入和读取必须经过此门禁
    """

    def __init__(self, db: AsyncSession):
        self.db = db
        self.router = ControlPlaneRouter(db)

    async def can_write_memory(
        self,
        memory_data: Dict[str, Any],
        user_id: str,
    ) -> Dict[str, Any]:
        """检查是否允许写入记忆
        
        Args:
            memory_data: 记忆数据
            user_id: 用户ID
            
        Returns:
            决策结果
        """
        decision = await self.router.decide_memory_write(memory_data, user_id)
        
        return {
            "allowed": decision.action == DecisionAction.ALLOW,
            "action": decision.action.value,
            "reason": decision.reason,
            "metadata": decision.metadata,
            "decision_id": decision.decision_id,
        }

    async def can_access_memory(
        self,
        memory_id: str,
        user_id: str,
        access_type: str = "read",
    ) -> Dict[str, Any]:
        """检查是否允许访问记忆
        
        Args:
            memory_id: 记忆ID
            user_id: 用户ID
            access_type: 访问类型 (read/write/delete)
            
        Returns:
            决策结果
        """
        # 查询记忆
        result = await self.db.execute(
            select(CommittedMemory).where(CommittedMemory.id == memory_id)
        )
        memory = result.scalar_one_or_none()
        
        if not memory:
            return {
                "allowed": False,
                "reason": "memory_not_found",
                "access_type": access_type,
            }
        
        # 检查所有权
        if memory.user_id != user_id:
            return {
                "allowed": False,
                "reason": "not_authorized",
                "access_type": access_type,
            }
        
        # 检查敏感度限制
        if access_type in ("write", "delete") and memory.sensitivity in ("private", "sensitive"):
            return {
                "allowed": True,
                "reason": "allowed_with_caution",
                "access_type": access_type,
                "sensitivity": memory.sensitivity,
                "requires_confirmation": True,
            }
        
        return {
            "allowed": True,
            "reason": "access_granted",
            "access_type": access_type,
        }

    async def filter_accessible_memories(
        self,
        memory_ids: List[str],
        user_id: str,
        recall_level: str = "work_context",
    ) -> List[str]:
        """过滤出可访问的记忆ID列表
        
        Args:
            memory_ids: 记忆ID列表
            user_id: 用户ID
            recall_level: 召回级别
            
        Returns:
            可访问的记忆ID列表
        """
        if not memory_ids:
            return []
        
        # 查询记忆
        result = await self.db.execute(
            select(CommittedMemory).where(CommittedMemory.id.in_(memory_ids))
        )
        memories = result.scalars().all()
        
        # 过滤
        accessible_ids = []
        for memory in memories:
            # 检查所有权
            if memory.user_id != user_id:
                continue
            
            # 检查状态
            if memory.status != CommittedStatus.ACTIVE:
                continue
            
            # 检查敏感度（根据召回级别）
            from src.platform.control_plane.policies.retrieval_policy import RECALL_LEVEL_CONFIG
            config = RECALL_LEVEL_CONFIG.get(recall_level, RECALL_LEVEL_CONFIG["work_context"])
            
            if memory.sensitivity not in config["allowed_sensitivity"]:
                continue
            
            accessible_ids.append(memory.id)
        
        return accessible_ids

    async def get_memory_gate_stats(self, user_id: str) -> Dict[str, Any]:
        """获取记忆门禁统计信息
        
        Args:
            user_id: 用户ID
            
        Returns:
            统计信息
        """
        # 统计各状态记忆数量
        result = await self.db.execute(
            select(CommittedMemory).where(CommittedMemory.user_id == user_id)
        )
        all_memories = result.scalars().all()
        
        stats = {
            "total": len(all_memories),
            "active": sum(1 for m in all_memories if m.status == CommittedStatus.ACTIVE),
            "superseded": sum(1 for m in all_memories if m.status == CommittedStatus.SUPERSEDED),
            "revoked": sum(1 for m in all_memories if m.status == CommittedStatus.REVOKED),
            "forgotten": sum(1 for m in all_memories if m.status == CommittedStatus.FORGOTTEN),
            "by_sensitivity": {},
            "by_type": {},
        }
        
        # 按敏感度统计
        for memory in all_memories:
            sens = memory.sensitivity
            stats["by_sensitivity"][sens] = stats["by_sensitivity"].get(sens, 0) + 1
        
        # 按类型统计
        for memory in all_memories:
            mem_type = memory.memory_type.value
            stats["by_type"][mem_type] = stats["by_type"].get(mem_type, 0) + 1
        
        return stats
