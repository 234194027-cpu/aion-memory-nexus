"""Retrieval Control Policy - 检索控制策略

决定查询时应该使用哪些 memory

控制：
- decision 优先级
- 时间权重
- persona bias 修正
- conflict 过滤

输出：filtered_memory_set
"""

import logging
from typing import Dict, Any

from src.platform.control_plane.decision import DecisionAction

logger = logging.getLogger(__name__)


# 召回级别配置
RECALL_LEVEL_CONFIG = {
    "task_only": {
        "allowed_types": ["task", "fact", "project_context"],
        "allowed_sensitivity": ["public"],
        "max_results": 10,
    },
    "work_context": {
        "allowed_types": [
            "decision", "insight", "fact", "project_context",
            "principle", "preference",
        ],
        "allowed_sensitivity": ["public", "normal"],
        "max_results": 20,
    },
    "personal_context": {
        "allowed_types": [
            "decision", "insight", "fact", "project_context",
            "principle", "preference", "persona_hypothesis",
        ],
        "allowed_sensitivity": ["public", "normal", "private"],
        "max_results": 30,
    },
    "full_trusted": {
        "allowed_types": None,  # 所有类型
        "allowed_sensitivity": ["public", "normal", "private", "sensitive"],
        "max_results": 50,
    },
}

# 决策类型优先级
DECISION_PRIORITY = {
    "decision": 1.0,
    "insight": 0.8,
    "principle": 0.7,
    "preference": 0.7,
    "fact": 0.6,
    "project_context": 0.5,
    "correction": 0.9,
    "timeline_event": 0.4,
    "persona_hypothesis": 0.6,
    "task": 0.2,
}


class RetrievalPolicy:
    """检索控制策略
    
    决定检索时应该使用哪些记忆
    """

    async def evaluate(
        self,
        query: str,
        user_id: str,
        recall_level: str = "work_context",
    ) -> Dict[str, Any]:
        """评估检索请求
        
        Args:
            query: 查询内容
            user_id: 用户ID
            recall_level: 召回级别
            
        Returns:
            决策结果字典
        """
        # 1. 验证召回级别
        if recall_level not in RECALL_LEVEL_CONFIG:
            logger.warning(f"Invalid recall_level: {recall_level}, using work_context")
            recall_level = "work_context"
        
        config = RECALL_LEVEL_CONFIG[recall_level]
        
        # 2. 构建过滤条件
        filter_config = {
            "allowed_types": config["allowed_types"],
            "allowed_sensitivity": config["allowed_sensitivity"],
            "max_results": config["max_results"],
        }
        
        # 3. 计算类型优先级权重
        type_weights = {}
        for mem_type, priority in DECISION_PRIORITY.items():
            if config["allowed_types"] is None or mem_type in config["allowed_types"]:
                type_weights[mem_type] = priority
        
        # 4. 返回决策结果
        return {
            "action": DecisionAction.ALLOW,
            "reason": "retrieval_approved",
            "metadata": {
                "recall_level": recall_level,
                "filter_config": filter_config,
                "type_weights": type_weights,
                "query_length": len(query),
            },
        }

    def get_type_boost(self, memory_type: str) -> float:
        """获取记忆类型的加权系数
        
        Args:
            memory_type: 记忆类型
            
        Returns:
            加权系数
        """
        return DECISION_PRIORITY.get(memory_type, 0.3)

    def should_include_memory(
        self,
        memory_type: str,
        sensitivity: str,
        recall_level: str,
    ) -> bool:
        """判断是否应该包含该记忆
        
        Args:
            memory_type: 记忆类型
            sensitivity: 敏感度
            recall_level: 召回级别
            
        Returns:
            是否应该包含
        """
        config = RECALL_LEVEL_CONFIG.get(recall_level, RECALL_LEVEL_CONFIG["work_context"])
        
        # 检查类型
        if config["allowed_types"] is not None:
            if memory_type not in config["allowed_types"]:
                return False
        
        # 检查敏感度
        if sensitivity not in config["allowed_sensitivity"]:
            return False
        
        return True
