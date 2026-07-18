"""Memory Policies - 记忆控制策略

包含：
- MemoryAdmissionPolicy: 决定 Event 是否可以进入 Memory Agent
- MemoryWritePolicy: 校验工作 Agent 的正式记忆提案
"""

import logging
from typing import Dict, Any

from src.platform.control_plane.decision import DecisionAction

logger = logging.getLogger(__name__)


# Memory Admission 阈值
ADMISSION_MIN_IMPORTANCE = 0.3
ADMISSION_NOISE_PATTERNS = [
    r"^(嗯|哦|啊|呢|吧|吗|哈|嘿|嗨)",
    r"^(好的|收到|明白|了解|知道了)",
    r"^(谢谢|感谢|thx|thanks)",
]

# Memory Write 阈值
WRITE_MIN_IMPORTANCE = 0.5
WRITE_MIN_CONFIDENCE = 0.5


class MemoryAdmissionPolicy:
    """记忆准入策略
    
    决定 Event 是否值得进入 Memory Agent 分析
    """

    async def evaluate(
        self,
        event_data: Dict[str, Any],
        user_id: str,
    ) -> Dict[str, Any]:
        """评估事件是否允许进入 Memory Agent
        
        Args:
            event_data: 事件数据
            user_id: 用户ID
            
        Returns:
            决策结果字典
        """
        content = event_data.get("content", "")
        # 1. 检查是否为噪音内容
        import re
        for pattern in ADMISSION_NOISE_PATTERNS:
            if re.match(pattern, content, re.IGNORECASE):
                return {
                    "action": DecisionAction.REJECT,
                    "reason": f"noise_pattern: {pattern}",
                    "metadata": {"pattern": pattern},
                }
        
        # 2. 检查内容是否有实质意义（至少包含一个名词或动词）
        # 简化检查：内容长度 > 20 且不是纯标点
        if len(content) < 10:
            return {
                "action": DecisionAction.QUEUE,
                "reason": "insufficient_content",
                "metadata": {"content_length": len(content)},
            }
        
        # 3. 检查是否有明确的记忆价值指标
        # 例如：包含数字、日期、决策关键词等
        value_indicators = [
            r"\d{4}[-/]\d{1,2}[-/]\d{1,2}",  # 日期
            r"\d+%",  # 百分比
            r"(决定|选择|认为|应该|必须|不能)",  # 决策词
            r"(重要|关键|核心|本质)",  # 重要性词
            r"(经验|教训|总结|反思)",  # 反思词
        ]
        
        has_value_indicator = any(
            re.search(pattern, content) for pattern in value_indicators
        )
        
        if not has_value_indicator:
            return {
                "action": DecisionAction.QUEUE,
                "reason": "no_value_indicator",
                "metadata": {"content_length": len(content)},
            }
        
        # 4. 通过检查，允许进入 Memory Agent
        return {
            "action": DecisionAction.ALLOW,
            "reason": "admission_approved",
            "metadata": {
                "content_length": len(content),
                "has_value_indicator": True,
            },
        }


class MemoryWritePolicy:
    """记忆写入控制策略
    
    决定工作 Agent 的证据化提案是否允许写入 CommittedMemory
    """

    async def evaluate(
        self,
        proposal_data: Dict[str, Any],
        user_id: str,
    ) -> Dict[str, Any]:
        """评估工作 Agent 的正式记忆提案是否允许写入
        
        Args:
            proposal_data: 工作 Agent 的证据化提案
            user_id: 用户ID
            
        Returns:
            决策结果字典
        """
        importance = float(proposal_data.get("importance", 0.0))
        confidence = float(proposal_data.get("confidence", 0.0))
        memory_type = proposal_data.get("memory_type", "fact")
        sensitivity = proposal_data.get("sensitivity", "normal")
        epistemic_status = str(proposal_data.get("epistemic_status", ""))
        
        # 1. 检查重要性阈值
        if importance < WRITE_MIN_IMPORTANCE:
            return {
                "action": DecisionAction.REJECT,
                "reason": f"importance_too_low: {importance} < {WRITE_MIN_IMPORTANCE}",
                "metadata": {"importance": importance},
            }
        
        # 2. 检查置信度阈值
        if confidence < WRITE_MIN_CONFIDENCE:
            return {
                "action": DecisionAction.REJECT,
                "reason": f"confidence_too_low: {confidence} < {WRITE_MIN_CONFIDENCE}",
                "metadata": {"confidence": confidence},
            }
        
        # 3. Agent/模型观点不能被提升为用户事实。
        if epistemic_status in {
            "agent_assertion", "assistant_supplied", "external_claim", "model_inference"
        }:
            return {
                "action": DecisionAction.REJECT,
                "reason": "non_user_assertion",
                "metadata": {"epistemic_status": epistemic_status},
            }

        # 4. 敏感内容可在用户隔离范围保存，但不得扩大读取或主动触达权限。
        return {
            "action": DecisionAction.ALLOW,
            "reason": "autonomous_governance_allowed",
            "metadata": {
                "importance": importance,
                "confidence": confidence,
                "memory_type": memory_type,
                "sensitivity": sensitivity,
                "requires_review": False,
            },
        }
