"""Control Plane Decision Types - 决策类型定义"""

from enum import Enum


class DecisionAction(str, Enum):
    """决策动作类型"""
    ALLOW = "allow"
    REJECT = "reject"
    MODIFY = "modify"
    ROUTE = "route"
    QUEUE = "queue"
    SANITIZE = "sanitize"


class DecisionTarget(str, Enum):
    """决策目标类型"""
    EVENT_INGESTION = "event_ingestion"
    MEMORY_ADMISSION = "memory_admission"
    MEMORY_WRITE = "memory_write"
    RETRIEVAL = "retrieval"
    AGENT_PERMISSION = "agent_permission"
    SYSTEM_EVOLUTION = "system_evolution"
