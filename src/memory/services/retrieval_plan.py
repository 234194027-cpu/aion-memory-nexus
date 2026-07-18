"""Deterministic, auditable retrieval planning for conversational memory lookup."""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class RetrievalIntent(StrEnum):
    FACT = "fact"
    TIME = "time"
    REASON = "reason"
    CHANGE = "change"
    PERSON = "person"
    TASK = "task"
    OPEN_ITEM = "open_item"
    REFLECTION = "reflection"


@dataclass(frozen=True, slots=True)
class RetrievalPlan:
    intent: RetrievalIntent
    recall_level: str
    top_k: int


def build_retrieval_plan(query: str, *, requested_top_k: int | None = None) -> RetrievalPlan:
    text = query.lower()
    if any(token in text for token in ("为什么", "原因", "当初", "决定")):
        intent = RetrievalIntent.REASON
    elif any(token in text for token in ("什么时候", "哪年", "以前", "当时", "时间")):
        intent = RetrievalIntent.TIME
    elif any(token in text for token in ("改变", "纠正", "现在", "过去")):
        intent = RetrievalIntent.CHANGE
    elif any(token in text for token in ("谁", "人物", "朋友", "同事", "家人")):
        intent = RetrievalIntent.PERSON
    elif any(token in text for token in ("待办", "任务", "下一步", "截止")):
        intent = RetrievalIntent.TASK
    elif any(token in text for token in ("还没", "未完成", "开放", "跟进")):
        intent = RetrievalIntent.OPEN_ITEM
    elif any(token in text for token in ("反思", "模式", "建议", "怎么看")):
        intent = RetrievalIntent.REFLECTION
    else:
        intent = RetrievalIntent.FACT
    top_k = max(1, min(int(requested_top_k or 5), 20))
    recall_level = "task_only" if intent in {RetrievalIntent.TASK, RetrievalIntent.OPEN_ITEM} else "work_context"
    return RetrievalPlan(intent=intent, recall_level=recall_level, top_k=top_k)
