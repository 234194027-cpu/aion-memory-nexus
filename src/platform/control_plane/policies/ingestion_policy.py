"""Ingestion Control Policy - 输入控制策略

决定什么可以进入 Event Layer

控制内容：
- chat 是否写入
- agent 是否允许写入
- obsidian 是否过滤
- API 是否可信

输出：ALLOW / REJECT / SANITIZE
"""

import logging
from typing import Dict, Any

from src.platform.control_plane.decision import DecisionAction

logger = logging.getLogger(__name__)


# 可信源配置
TRUSTED_SOURCES = {
    "chat",
    "obsidian",
    "codex",
    "openclaw",
    "chatgpt",
}

# 需要过滤的内容模式
FILTER_PATTERNS = [
    r"^[\s\n\r]*$",  # 空白内容
    r"^(好的|收到|明白|了解|知道了|ok|OK)$",  # 短确认噪音
    r"^(test|测试|debug|调试).{0,10}$",  # 测试内容
]

# 内容长度限制
MIN_CONTENT_LENGTH = 10
MAX_CONTENT_LENGTH = 50000


class IngestionPolicy:
    """输入控制策略
    
    评估事件是否允许进入 Event Layer
    """

    async def evaluate(
        self,
        event_data: Dict[str, Any],
        user_id: str,
    ) -> Dict[str, Any]:
        """评估事件输入
        
        Args:
            event_data: 事件数据
            user_id: 用户ID
            
        Returns:
            决策结果字典
        """
        content = event_data.get("content", "")
        source = event_data.get("source", "")
        agent_type = event_data.get("agent_type")
        
        # 1. 检查空白内容
        if not content.strip():
            return {
                "action": DecisionAction.REJECT,
                "reason": "empty_content",
                "metadata": {},
            }
        
        # 2. 检查测试/噪音内容；要先于长度判断，保证用户可理解的拒绝原因。
        import re
        for pattern in FILTER_PATTERNS:
            if re.match(pattern, content, re.IGNORECASE):
                return {
                    "action": DecisionAction.REJECT,
                    "reason": f"filtered_pattern: {pattern}",
                    "metadata": {"pattern": pattern},
                }

        # 3. 检查内容长度
        if len(content) < MIN_CONTENT_LENGTH:
            return {
                "action": DecisionAction.REJECT,
                "reason": f"content_too_short: {len(content)} < {MIN_CONTENT_LENGTH}",
                "metadata": {"content_length": len(content)},
            }

        if len(content) > MAX_CONTENT_LENGTH:
            return {
                "action": DecisionAction.SANITIZE,
                "reason": f"content_truncated: {len(content)} > {MAX_CONTENT_LENGTH}",
                "metadata": {
                    "original_length": len(content),
                    "truncated_length": MAX_CONTENT_LENGTH,
                },
            }
        
        # 4. 检查来源可信度
        effective_source = agent_type or source
        if effective_source and effective_source not in TRUSTED_SOURCES:
            logger.warning(f"Untrusted source: {effective_source}")
            # 不拒绝，但记录警告
            return {
                "action": DecisionAction.ALLOW,
                "reason": "allowed_with_warning",
                "metadata": {
                    "source": effective_source,
                    "trusted": False,
                    "warning": "untrusted_source",
                },
            }
        
        # 5. 通过所有检查
        return {
            "action": DecisionAction.ALLOW,
            "reason": "all_checks_passed",
            "metadata": {
                "source": effective_source,
                "trusted": True,
                "content_length": len(content),
            },
        }
