"""Evolution Control Policy - 系统进化控制策略

决定系统如何"变聪明"，而不是乱变

控制：
- memory rewriter 是否允许执行
- persona 是否更新
- conflict 是否升级
- decision 是否归档

输出：evolution_actions
"""

import logging
from typing import Dict, Any

from src.platform.control_plane.decision import DecisionAction

logger = logging.getLogger(__name__)


# 进化操作配置
EVOLUTION_OPERATIONS = {
    "memory_rewrite": {
        "requires_approval": True,
        "max_batch_size": 50,
        "allowed_actions": ["merge", "rewrite", "archive", "link"],
    },
    "persona_update": {
        "requires_approval": False,
        "min_memories_required": 5,
        "max_updates_per_day": 3,
    },
    "conflict_resolution": {
        "requires_approval": True,
        "auto_resolve_threshold": 0.8,  # 置信度阈值
    },
    "decision_archive": {
        "requires_approval": False,
        "min_age_days": 30,
        "allowed_statuses": ["resolved", "outdated"],
    },
}


class EvolutionPolicy:
    """系统进化控制策略
    
    决定系统进化操作是否允许
    """

    async def evaluate(
        self,
        evolution_request: Dict[str, Any],
        user_id: str,
    ) -> Dict[str, Any]:
        """评估进化操作请求
        
        Args:
            evolution_request: 进化操作请求
            user_id: 用户ID
            
        Returns:
            决策结果字典
        """
        operation_type = evolution_request.get("operation_type", "")
        
        # 1. 验证操作类型
        if operation_type not in EVOLUTION_OPERATIONS:
            return {
                "action": DecisionAction.REJECT,
                "reason": f"unknown_operation_type: {operation_type}",
                "metadata": {"operation_type": operation_type},
            }
        
        config = EVOLUTION_OPERATIONS[operation_type]
        
        # 2. 根据操作类型进行特定检查
        if operation_type == "memory_rewrite":
            return await self._evaluate_memory_rewrite(evolution_request, config)
        elif operation_type == "persona_update":
            return await self._evaluate_persona_update(evolution_request, config)
        elif operation_type == "conflict_resolution":
            return await self._evaluate_conflict_resolution(evolution_request, config)
        elif operation_type == "decision_archive":
            return await self._evaluate_decision_archive(evolution_request, config)
        
        return {
            "action": DecisionAction.ALLOW,
            "reason": "evolution_approved",
            "metadata": {"operation_type": operation_type},
        }

    async def _evaluate_memory_rewrite(
        self,
        request: Dict[str, Any],
        config: Dict[str, Any],
    ) -> Dict[str, Any]:
        """评估记忆重写操作"""
        proposals = request.get("proposals", [])
        
        # 检查批量大小
        if len(proposals) > config["max_batch_size"]:
            return {
                "action": DecisionAction.MODIFY,
                "reason": f"batch_too_large: {len(proposals)} > {config['max_batch_size']}",
                "metadata": {
                    "original_size": len(proposals),
                    "max_size": config["max_batch_size"],
                    "truncated": True,
                },
            }
        
        # 检查操作类型
        for proposal in proposals:
            action = proposal.get("action", "")
            if action not in config["allowed_actions"]:
                return {
                    "action": DecisionAction.REJECT,
                    "reason": f"disallowed_action: {action}",
                    "metadata": {"disallowed_action": action},
                }
        
        # 检查是否需要审批
        if config["requires_approval"] and not request.get("approved", False):
            return {
                "action": DecisionAction.ROUTE,
                "reason": "requires_approval",
                "metadata": {
                    "route_to": "approval_queue",
                    "operation_type": "memory_rewrite",
                },
            }
        
        return {
            "action": DecisionAction.ALLOW,
            "reason": "memory_rewrite_approved",
            "metadata": {
                "proposal_count": len(proposals),
                "approved": True,
            },
        }

    async def _evaluate_persona_update(
        self,
        request: Dict[str, Any],
        config: Dict[str, Any],
    ) -> Dict[str, Any]:
        """评估画像更新操作"""
        evidence_count = request.get("evidence_count", 0)
        
        # 检查证据数量
        if evidence_count < config["min_memories_required"]:
            return {
                "action": DecisionAction.REJECT,
                "reason": f"insufficient_evidence: {evidence_count} < {config['min_memories_required']}",
                "metadata": {"evidence_count": evidence_count},
            }
        
        return {
            "action": DecisionAction.ALLOW,
            "reason": "persona_update_approved",
            "metadata": {
                "evidence_count": evidence_count,
                "auto_approved": not config["requires_approval"],
            },
        }

    async def _evaluate_conflict_resolution(
        self,
        request: Dict[str, Any],
        config: Dict[str, Any],
    ) -> Dict[str, Any]:
        """评估冲突解决操作"""
        confidence = request.get("confidence", 0.0)
        
        # 检查是否可以自动解决
        if confidence >= config["auto_resolve_threshold"]:
            return {
                "action": DecisionAction.ALLOW,
                "reason": "auto_resolve_approved",
                "metadata": {
                    "confidence": confidence,
                    "auto_resolve": True,
                },
            }
        
        # 需要人工审批
        if config["requires_approval"] and not request.get("approved", False):
            return {
                "action": DecisionAction.ROUTE,
                "reason": "requires_approval",
                "metadata": {
                    "route_to": "approval_queue",
                    "operation_type": "conflict_resolution",
                },
            }
        
        return {
            "action": DecisionAction.ALLOW,
            "reason": "conflict_resolution_approved",
            "metadata": {
                "confidence": confidence,
                "approved": True,
            },
        }

    async def _evaluate_decision_archive(
        self,
        request: Dict[str, Any],
        config: Dict[str, Any],
    ) -> Dict[str, Any]:
        """评估决策归档操作"""
        decision_age_days = request.get("decision_age_days", 0)
        decision_status = request.get("decision_status", "")
        
        # 检查决策年龄
        if decision_age_days < config["min_age_days"]:
            return {
                "action": DecisionAction.REJECT,
                "reason": f"decision_too_young: {decision_age_days} < {config['min_age_days']}",
                "metadata": {"decision_age_days": decision_age_days},
            }
        
        # 检查决策状态
        if decision_status not in config["allowed_statuses"]:
            return {
                "action": DecisionAction.REJECT,
                "reason": f"invalid_status: {decision_status}",
                "metadata": {"decision_status": decision_status},
            }
        
        return {
            "action": DecisionAction.ALLOW,
            "reason": "decision_archive_approved",
            "metadata": {
                "decision_age_days": decision_age_days,
                "decision_status": decision_status,
            },
        }
