"""Control Plane Router - 决策路由器

核心职责：
- 接收所有系统行为请求
- 根据策略引擎做出决策
- 输出 allow/reject/modify/route
- 记录所有决策到审计日志
"""

import logging
from datetime import datetime, timezone
from typing import Dict, Any
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from src.platform.control_plane.decision import DecisionAction, DecisionTarget
from src.platform.control_plane.policies.ingestion_policy import IngestionPolicy
from src.platform.control_plane.policies.memory_policy import MemoryAdmissionPolicy, MemoryWritePolicy
from src.platform.control_plane.policies.agent_policy import AgentPermissionPolicy
from src.platform.control_plane.policies.retrieval_policy import RetrievalPolicy
from src.platform.control_plane.policies.evolution_policy import EvolutionPolicy

logger = logging.getLogger(__name__)





@dataclass
class DecisionResult:
    """决策结果"""
    action: DecisionAction
    target: DecisionTarget
    reason: str
    metadata: Dict[str, Any]
    decided_at: datetime
    decision_id: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "action": self.action.value,
            "target": self.target.value,
            "reason": self.reason,
            "metadata": self.metadata,
            "decided_at": self.decided_at.isoformat(),
            "decision_id": self.decision_id,
        }


class ControlPlaneRouter:
    """Control Plane 决策路由器
    
    所有系统行为必须经过此路由器进行决策
    """

    def __init__(self, db: AsyncSession):
        self.db = db
        self.ingestion_policy = IngestionPolicy()
        self.memory_admission_policy = MemoryAdmissionPolicy()
        self.memory_write_policy = MemoryWritePolicy()
        self.agent_permission_policy = AgentPermissionPolicy(db)
        self.retrieval_policy = RetrievalPolicy()
        self.evolution_policy = EvolutionPolicy()

    async def decide_ingestion(
        self,
        event_data: Dict[str, Any],
        user_id: str,
    ) -> DecisionResult:
        """决策：事件是否允许进入 Event Layer
        
        Args:
            event_data: 事件数据
            user_id: 用户ID
            
        Returns:
            DecisionResult: allow/reject/sanitize
        """
        from src.shared.ids.id_generator import generate_audit_log_id
        
        decision = await self.ingestion_policy.evaluate(event_data, user_id)
        
        result = DecisionResult(
            action=decision["action"],
            target=DecisionTarget.EVENT_INGESTION,
            reason=decision["reason"],
            metadata=decision.get("metadata", {}),
            decided_at=datetime.now(timezone.utc),
            decision_id=generate_audit_log_id(),
        )
        
        # 记录决策到审计日志
        await self._log_decision(result, user_id, event_data)
        
        logger.info(
            f"Ingestion decision: {result.action.value} - {result.reason}"
        )
        
        return result

    async def decide_memory_admission(
        self,
        event_data: Dict[str, Any],
        user_id: str,
    ) -> DecisionResult:
        """决策：事件是否允许进入 Memory Agent
        
        Args:
            event_data: 事件数据
            user_id: 用户ID
            
        Returns:
            DecisionResult: allow/reject/queue
        """
        from src.shared.ids.id_generator import generate_audit_log_id
        
        decision = await self.memory_admission_policy.evaluate(event_data, user_id)
        
        result = DecisionResult(
            action=decision["action"],
            target=DecisionTarget.MEMORY_ADMISSION,
            reason=decision["reason"],
            metadata=decision.get("metadata", {}),
            decided_at=datetime.now(timezone.utc),
            decision_id=generate_audit_log_id(),
        )
        
        await self._log_decision(result, user_id, event_data)
        
        logger.info(
            f"Memory admission decision: {result.action.value} - {result.reason}"
        )
        
        return result

    async def decide_memory_write(
        self,
        proposal_data: Dict[str, Any],
        user_id: str,
    ) -> DecisionResult:
        """决策：工作 Agent 的证据化提案是否允许写入 CommittedMemory
        
        Args:
            proposal_data: 工作 Agent 正式记忆提案
            user_id: 用户ID
            
        Returns:
            DecisionResult: allow/reject/modify
        """
        from src.shared.ids.id_generator import generate_audit_log_id
        
        decision = await self.memory_write_policy.evaluate(proposal_data, user_id)
        
        result = DecisionResult(
            action=decision["action"],
            target=DecisionTarget.MEMORY_WRITE,
            reason=decision["reason"],
            metadata=decision.get("metadata", {}),
            decided_at=datetime.now(timezone.utc),
            decision_id=generate_audit_log_id(),
        )
        
        await self._log_decision(result, user_id, proposal_data)
        
        logger.info(
            f"Memory write decision: {result.action.value} - {result.reason}"
        )
        
        return result

    async def decide_agent_permission(
        self,
        agent_id: str,
        tool_name: str,
        user_id: str,
    ) -> DecisionResult:
        """决策：Agent 是否允许执行工具
        
        Args:
            agent_id: Agent ID
            tool_name: 工具名称
            user_id: 用户ID
            
        Returns:
            DecisionResult: allow/reject
        """
        from src.shared.ids.id_generator import generate_audit_log_id
        
        decision = await self.agent_permission_policy.check_permission(
            agent_id, tool_name, user_id
        )
        
        result = DecisionResult(
            action=decision["action"],
            target=DecisionTarget.AGENT_PERMISSION,
            reason=decision["reason"],
            metadata=decision.get("metadata", {}),
            decided_at=datetime.now(timezone.utc),
            decision_id=generate_audit_log_id(),
        )
        
        await self._log_decision(result, user_id, {
            "agent_id": agent_id,
            "tool_name": tool_name,
        })
        
        logger.info(
            f"Agent permission decision: {result.action.value} - {result.reason}"
        )
        
        return result

    async def decide_retrieval(
        self,
        query: str,
        user_id: str,
        recall_level: str = "work_context",
    ) -> DecisionResult:
        """决策：检索时应该使用哪些记忆
        
        Args:
            query: 查询内容
            user_id: 用户ID
            recall_level: 召回级别
            
        Returns:
            DecisionResult: allow with filtered_memory_set
        """
        from src.shared.ids.id_generator import generate_audit_log_id
        
        decision = await self.retrieval_policy.evaluate(
            query, user_id, recall_level
        )
        
        result = DecisionResult(
            action=decision["action"],
            target=DecisionTarget.RETRIEVAL,
            reason=decision["reason"],
            metadata=decision.get("metadata", {}),
            decided_at=datetime.now(timezone.utc),
            decision_id=generate_audit_log_id(),
        )
        
        await self._log_decision(result, user_id, {"query": query})
        
        logger.info(
            f"Retrieval decision: {result.action.value} - {result.reason}"
        )
        
        return result

    async def decide_evolution(
        self,
        evolution_request: Dict[str, Any],
        user_id: str,
    ) -> DecisionResult:
        """决策：系统进化操作是否允许
        
        Args:
            evolution_request: 进化操作请求
            user_id: 用户ID
            
        Returns:
            DecisionResult: allow/reject
        """
        from src.shared.ids.id_generator import generate_audit_log_id
        
        decision = await self.evolution_policy.evaluate(evolution_request, user_id)
        
        result = DecisionResult(
            action=decision["action"],
            target=DecisionTarget.SYSTEM_EVOLUTION,
            reason=decision["reason"],
            metadata=decision.get("metadata", {}),
            decided_at=datetime.now(timezone.utc),
            decision_id=generate_audit_log_id(),
        )
        
        await self._log_decision(result, user_id, evolution_request)
        
        logger.info(
            f"Evolution decision: {result.action.value} - {result.reason}"
        )
        
        return result

    async def _log_decision(
        self,
        result: DecisionResult,
        user_id: str,
        context: Dict[str, Any],
    ) -> None:
        """记录决策到审计日志"""
        try:
            from src.execution.services.audit_logger import AuditLogger
            
            await AuditLogger.log(
                self.db,
                user_id=user_id,
                action=f"control_plane_{result.target.value}",
                actor_type="system",
                actor_id="control_plane",
                target_type=result.target.value,
                target_id=result.decision_id,
                detail={
                    "decision": result.to_dict(),
                    "context": context,
                },
            )
        except Exception as e:
            logger.error(f"Failed to log control plane decision: {e}")
