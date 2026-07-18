"""Task System (Gen 3 / Cognitive OS).

把决策、行动、笔记组织为带状态的任务 (LifeTask)。

- 状态机: todo -> doing -> done
  - todo  -> doing  / blocked / abandoned
  - doing -> done   / blocked / abandoned
  - blocked -> doing / abandoned  (可解锁)
  - done / abandoned 是终态, 不再转移
- 链接到 memory / decision 时校验 owner
- auto_extract_tasks_from_recent_memories: 调 LLM 从最近 N 天
  memory_type=TASK 的 CommittedMemory 抽取候选任务并实际创建

v3 新增:
- decompose_task: 将大任务拆解为子任务
- assign_agent: 将任务分配给指定 agent
- complete_task_with_memory: 完成任务并通过 Working Agent 治理结果记忆
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.shared.llm.providers import get_llm_provider
from src.shared.llm.model_gateway import ModelGateway
from src.execution.models.agent_profile import AgentProfile
from src.memory.models.committed_memory import CommittedMemory, CommittedStatus
from src.memory.models.memory_type import MemoryType
from src.memory.models.raw_event import (
    ProcessingStatus,
    RawEvent,
    SensitivityLevel,
    SourceType,
    VisibilityScope,
)
from src.cognition.models.decision_record import DecisionRecord
from src.execution.models.life_task import LifeTask
from src.execution.schemas.os import (
    VALID_TASK_PRIORITIES,
    VALID_TASK_STATUSES,
)
from src.shared.ids.id_generator import generate_event_id, generate_task_id
from src.shared.utils.hash import compute_content_hash
from src.execution.prompts.task_system import build_extract_prompt, build_decompose_prompt

logger = logging.getLogger(__name__)


# status -> set of allowed next statuses
ALLOWED_TRANSITIONS = {
    "todo": {"doing", "blocked", "abandoned"},
    "doing": {"done", "blocked", "abandoned"},
    "blocked": {"doing", "abandoned", "todo"},
    "done": set(),
    "abandoned": set(),
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _ensure_utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _safe_json_list(text: Optional[str]) -> List[str]:
    if not text:
        return []
    try:
        v = json.loads(text)
        if isinstance(v, list):
            return [str(x) for x in v if x]
    except Exception:
        pass
    return []


def _to_dict(task: LifeTask) -> dict:
    return {
        "id": task.id,
        "user_id": task.user_id,
        "title": task.title,
        "description": task.description,
        "status": task.status,
        "priority": task.priority,
        "project_id": task.project_id,
        "parent_task_id": task.parent_task_id,
        "linked_memory_ids": _safe_json_list(task.linked_memory_ids),
        "linked_decision_ids": _safe_json_list(task.linked_decision_ids),
        "due_at": task.due_at,
        "started_at": task.started_at,
        "completed_at": task.completed_at,
        "created_at": task.created_at,
        "updated_at": task.updated_at,
        "assigned_agent_id": task.assigned_agent_id,
        "priority_score": task.priority_score if task.priority_score is not None else 0.5,
        "sub_tasks_count": task.sub_tasks_count if task.sub_tasks_count is not None else 0,
    }


class TaskSystem:
    def __init__(self, db: AsyncSession):
        self.db = db

    # ------------------------------------------------------------------ CRUD

    async def create_task(
        self,
        user_id: str,
        *,
        title: str,
        description: Optional[str] = None,
        priority: str = "P2",
        project_id: Optional[str] = None,
        parent_task_id: Optional[str] = None,
        linked_memory_ids: Optional[List[str]] = None,
        linked_decision_ids: Optional[List[str]] = None,
        due_at: Optional[datetime] = None,
        priority_score: float = 0.5,
    ) -> LifeTask:
        if not title or not title.strip():
            raise ValueError("title is required")
        if priority not in VALID_TASK_PRIORITIES:
            raise ValueError(f"invalid priority: {priority}")

        if linked_memory_ids:
            await self._validate_memory_owner(user_id, linked_memory_ids)
        if linked_decision_ids:
            await self._validate_decision_owner(user_id, linked_decision_ids)

        now = _now()
        task = LifeTask(
            id=generate_task_id(),
            user_id=user_id,
            title=title.strip(),
            description=description,
            status="todo",
            priority=priority,
            project_id=project_id,
            parent_task_id=parent_task_id,
            linked_memory_ids=json.dumps(linked_memory_ids or [], ensure_ascii=False),
            linked_decision_ids=json.dumps(linked_decision_ids or [], ensure_ascii=False),
            due_at=_ensure_utc(due_at),
            started_at=None,
            completed_at=None,
            created_at=now,
            updated_at=now,
            priority_score=priority_score,
        )
        self.db.add(task)
        await self.db.commit()
        await self.db.refresh(task)
        return task

    async def update_status(
        self, user_id: str, task_id: str, new_status: str,
        *,
        result_summary: Optional[str] = None,
    ) -> LifeTask:
        if new_status not in VALID_TASK_STATUSES:
            raise ValueError(f"invalid status: {new_status}")

        task = await self.get_task(user_id, task_id)
        allowed = ALLOWED_TRANSITIONS.get(task.status, set())
        if new_status not in allowed:
            raise ValueError(
                f"illegal transition: {task.status} -> {new_status} "
                f"(allowed: {sorted(allowed)})"
            )

        now = _now()
        task.status = new_status
        task.updated_at = now
        if new_status == "doing" and task.started_at is None:
            task.started_at = now
        if new_status == "done" and task.completed_at is None:
            task.completed_at = now
        if new_status == "todo":
            task.started_at = None
            task.completed_at = None
        if new_status == "abandoned":
            task.completed_at = task.completed_at or now

        await self.db.commit()
        await self.db.refresh(task)

        # v3: 当状态变为 done 且提供了 result_summary 时，自动写入 memory
        if new_status == "done" and result_summary:
            await self.complete_task_with_memory(
                user_id, task_id, result_summary=result_summary
            )
            await self.db.refresh(task)

        return task

    async def update_task(
        self,
        user_id: str,
        task_id: str,
        *,
        title: Optional[str] = None,
        description: Optional[str] = None,
        priority: Optional[str] = None,
        project_id: Optional[str] = None,
        due_at: Optional[datetime] = None,
    ) -> LifeTask:
        task = await self.get_task(user_id, task_id)
        if title is not None:
            if not title.strip():
                raise ValueError("title cannot be empty")
            task.title = title.strip()
        if description is not None:
            task.description = description
        if priority is not None:
            if priority not in VALID_TASK_PRIORITIES:
                raise ValueError(f"invalid priority: {priority}")
            task.priority = priority
        if project_id is not None:
            task.project_id = project_id
        if due_at is not None:
            task.due_at = _ensure_utc(due_at)
        task.updated_at = _now()
        await self.db.commit()
        await self.db.refresh(task)
        return task

    async def list_tasks(
        self,
        user_id: str,
        *,
        status: Optional[str] = None,
        project_id: Optional[str] = None,
        priority: Optional[str] = None,
        limit: int = 50,
    ) -> List[LifeTask]:
        if status and status not in VALID_TASK_STATUSES:
            raise ValueError(f"invalid status: {status}")
        if priority and priority not in VALID_TASK_PRIORITIES:
            raise ValueError(f"invalid priority: {priority}")

        filters = [LifeTask.user_id == user_id]
        if status:
            filters.append(LifeTask.status == status)
        if project_id:
            filters.append(LifeTask.project_id == project_id)
        if priority:
            filters.append(LifeTask.priority == priority)

        result = await self.db.execute(
            select(LifeTask)
            .where(and_(*filters))
            .order_by(LifeTask.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def get_task(self, user_id: str, task_id: str) -> LifeTask:
        result = await self.db.execute(
            select(LifeTask).where(LifeTask.id == task_id)
        )
        task = result.scalar_one_or_none()
        if task is None:
            raise LookupError(f"task not found: {task_id}")
        if task.user_id != user_id:
            raise PermissionError("task does not belong to user")
        return task

    # ------------------------------------------------------------------ links

    async def link_to_memory(
        self, user_id: str, task_id: str, memory_id: str
    ) -> LifeTask:
        task = await self.get_task(user_id, task_id)
        await self._validate_memory_owner(user_id, [memory_id])
        existing = _safe_json_list(task.linked_memory_ids)
        if memory_id not in existing:
            existing.append(memory_id)
        task.linked_memory_ids = json.dumps(existing, ensure_ascii=False)
        task.updated_at = _now()
        await self.db.commit()
        await self.db.refresh(task)
        return task

    async def link_to_decision(
        self, user_id: str, task_id: str, decision_id: str
    ) -> LifeTask:
        task = await self.get_task(user_id, task_id)
        await self._validate_decision_owner(user_id, [decision_id])
        existing = _safe_json_list(task.linked_decision_ids)
        if decision_id not in existing:
            existing.append(decision_id)
        task.linked_decision_ids = json.dumps(existing, ensure_ascii=False)
        task.updated_at = _now()
        await self.db.commit()
        await self.db.refresh(task)
        return task

    # ------------------------------------------------------------------ v3: decompose

    async def decompose_task(
        self, user_id: str, task_id: str, *, max_sub_tasks: int = 5
    ) -> Dict:
        """将大任务拆解为子任务。

        调用 LLM 分析任务，输出子任务列表。
        创建子任务后更新父任务的 sub_tasks_count。
        返回 {"parent_task_id": str, "sub_tasks": [LifeTask], "decomposition_rationale": str}
        """
        parent = await self.get_task(user_id, task_id)

        # 调用 LLM 拆解
        rationale = ""
        sub_task_specs: List[dict] = []
        try:
            provider = get_llm_provider()
            prompt = build_decompose_prompt(
                title=parent.title,
                description=parent.description or "",
                priority=parent.priority,
                max_sub_tasks=max_sub_tasks,
            )
            raw = await ModelGateway(provider).generate_text(prompt, temperature=0.3, max_tokens=1500, prompt_id="task-decompose", prompt_version="v1")
            sub_task_specs = self._parse_decompose_payload(raw)
        except Exception as e:
            logger.warning("decompose_task LLM failed: %s", e)

        if not sub_task_specs:
            # 兜底：拆成 2 个通用子任务
            sub_task_specs = [
                {"title": f"{parent.title} - 第一部分", "description": "拆解后的第一个子任务", "priority_score": 0.8},
                {"title": f"{parent.title} - 第二部分", "description": "拆解后的第二个子任务", "priority_score": 0.6},
            ]
            rationale = "LLM 拆解失败，使用默认拆分"
        else:
            rationale = f"LLM 自动拆解为 {len(sub_task_specs)} 个子任务"

        created_sub_tasks: List[LifeTask] = []
        for spec in sub_task_specs[:max_sub_tasks]:
            try:
                sub_task = await self.create_task(
                    user_id=user_id,
                    title=spec.get("title") or "未命名子任务",
                    description=spec.get("description") or "",
                    priority=parent.priority,
                    project_id=parent.project_id,
                    parent_task_id=parent.id,
                    priority_score=float(spec.get("priority_score") or 0.5),
                )
                created_sub_tasks.append(sub_task)
            except Exception as e:
                logger.warning("decompose_task create sub_task failed: %s", e)

        # 更新父任务的 sub_tasks_count
        parent.sub_tasks_count = len(created_sub_tasks)
        parent.updated_at = _now()
        await self.db.commit()
        await self.db.refresh(parent)

        return {
            "parent_task_id": parent.id,
            "sub_tasks": created_sub_tasks,
            "decomposition_rationale": rationale,
        }

    # ------------------------------------------------------------------ v3: assign_agent

    async def assign_agent(
        self, user_id: str, task_id: str, agent_id: str
    ) -> LifeTask:
        """将任务分配给指定 agent。校验 agent 归属。"""
        task = await self.get_task(user_id, task_id)

        # 校验 agent 归属
        result = await self.db.execute(
            select(AgentProfile).where(
                and_(
                    AgentProfile.id == agent_id,
                    AgentProfile.user_id == user_id,
                )
            )
        )
        agent = result.scalar_one_or_none()
        if agent is None:
            raise LookupError(f"agent not found or not owned by user: {agent_id}")

        task.assigned_agent_id = agent_id
        task.updated_at = _now()
        await self.db.commit()
        await self.db.refresh(task)
        return task

    # ------------------------------------------------------------------ v3: complete_task_with_memory

    async def complete_task_with_memory(
        self, user_id: str, task_id: str, *, result_summary: str
    ) -> Dict:
        """完成任务并将用户提供的结果交给 Working Agent 自动治理。

        1. 更新 task status=done
        2. 创建可追溯 RawEvent
        3. 由 Working Agent 建案、记录证据并决定是否生成正式记忆
        4. 返回 {"task": LifeTask, "memory_id": str}
        """
        task = await self.get_task(user_id, task_id)

        # 如果还没 done，先更新
        if task.status != "done":
            task = await self.update_status(user_id, task_id, "done")

        # 所有自动正式记忆必须经过 Working Agent；任务服务只提供用户证据。
        now = _now()
        title = f"任务完成: {task.title}"
        event_content = f"{title}\n{result_summary}"
        from src.memory.services.event_ingestion import EventIngestionService
        event = (
            await EventIngestionService(self.db).append(
                user_id=user_id,
                content=event_content,
                source_type=SourceType.MANUAL,
                source_id=f"task_completion:{task.id}",
                occurred_at=now,
                event_metadata={
                "task_id": task.id,
                "task_title": task.title,
                "user_supplied_result": True,
                },
                sensitivity=SensitivityLevel.NORMAL,
                visibility_scope=VisibilityScope.PERSONAL,
                processing_status=ProcessingStatus.COMPLETED,
            )
        ).event

        from src.execution.runtime.working_coordinator import WorkingCoordinator

        memory_ids = await WorkingCoordinator(self.db).materialize_preclassified(
            event=event,
            proposals=(
                {
                    "memory_type": "fact",
                    "title": title,
                    "content": result_summary,
                    "confidence": 0.9,
                    "importance": 0.7,
                    "sensitivity": "normal",
                    "entities": ["task_completion", task.id],
                    "reason": "explicit_user_task_completion",
                },
            ),
            origin="task_completion",
        )
        if not memory_ids:
            raise RuntimeError("working_agent_did_not_materialize_task_memory")
        memory_id = memory_ids[0]

        # 更新 task 的 linked_memory_ids
        existing = _safe_json_list(task.linked_memory_ids)
        if memory_id not in existing:
            existing.append(memory_id)
        task.linked_memory_ids = json.dumps(existing, ensure_ascii=False)
        task.updated_at = _now()
        await self.db.commit()
        await self.db.refresh(task)

        return {
            "task": task,
            "memory_id": memory_id,
        }

    # ------------------------------------------------------------------ helpers

    async def _validate_memory_owner(self, user_id: str, memory_ids: List[str]) -> None:
        if not memory_ids:
            return
        result = await self.db.execute(
            select(CommittedMemory.id).where(
                and_(
                    CommittedMemory.id.in_(memory_ids),
                    CommittedMemory.user_id == user_id,
                )
            )
        )
        owned = {row[0] for row in result.all()}
        missing = [m for m in memory_ids if m not in owned]
        if missing:
            raise PermissionError(
                f"memories not owned by user or not found: {missing}"
            )

    async def _validate_decision_owner(
        self, user_id: str, decision_ids: List[str]
    ) -> None:
        if not decision_ids:
            return
        result = await self.db.execute(
            select(DecisionRecord.id).where(
                and_(
                    DecisionRecord.id.in_(decision_ids),
                    DecisionRecord.user_id == user_id,
                )
            )
        )
        owned = {row[0] for row in result.all()}
        missing = [d for d in decision_ids if d not in owned]
        if missing:
            raise PermissionError(
                f"decisions not owned by user or not found: {missing}"
            )

    def _parse_decompose_payload(self, raw) -> List[dict]:
        """解析 LLM 返回的子任务 JSON 数组。"""
        from src.shared.utils.llm_output import extract_json_list
        data = extract_json_list(raw)
        if not data:
            return []
        return [item for item in data if isinstance(item, dict) and item.get("title")]

    # ------------------------------------------------------------------ auto extract

    async def auto_extract_tasks_from_recent_memories(
        self,
        user_id: str,
        *,
        days: int = 7,
        limit: int = 10,
    ) -> List[LifeTask]:
        """从最近 N 天 memory_type=TASK 的 CommittedMemory 抽取候选并实际创建。

        ✅ 幂等: 同一 memory_id 在已存在的 task 中则跳过, 不重复创建。
        """
        since = _now() - timedelta(days=days)
        result = await self.db.execute(
            select(CommittedMemory)
            .where(
                and_(
                    CommittedMemory.user_id == user_id,
                    CommittedMemory.status == CommittedStatus.ACTIVE,
                    CommittedMemory.memory_type == MemoryType.TASK,
                    CommittedMemory.created_at >= since,
                )
            )
            .order_by(CommittedMemory.created_at.desc())
            .limit(50)
        )
        memories = list(result.scalars().all())
        scanned = len(memories)
        if not memories:
            return []

        already_covered_ids = await self._memory_ids_already_in_tasks(user_id)

        candidates: List[dict] = []
        try:
            provider = get_llm_provider()
            prompt = self._build_extract_prompt(memories)
            raw = await ModelGateway(provider).generate_text(prompt, temperature=0.2, max_tokens=1500, prompt_id="task-extract", prompt_version="v1")
            candidates = self._parse_extract_payload(raw)
        except Exception as e:
            logger.warning("auto_extract LLM failed: %s", e)
            candidates = []

        if not candidates:
            candidates = [
                {
                    "title": m.title,
                    "description": (m.body or "")[:200],
                    "priority": "P2",
                    "linked_memory_ids": [m.id],
                }
                for m in memories
            ]

        created: List[LifeTask] = []
        skipped_dup = 0
        for cand in candidates[:limit]:
            linked_ids = cand.get("linked_memory_ids") or []
            if linked_ids and all(mid in already_covered_ids for mid in linked_ids):
                skipped_dup += 1
                continue
            try:
                task = await self.create_task(
                    user_id=user_id,
                    title=cand.get("title") or "未命名任务",
                    description=cand.get("description") or "",
                    priority=cand.get("priority") or "P2",
                    linked_memory_ids=linked_ids,
                )
                created.append(task)
                for mid in linked_ids:
                    already_covered_ids.add(mid)
            except Exception as e:
                logger.warning("auto_extract create_task failed: %s", e)

        if skipped_dup:
            logger.info("auto_extract: skipped %d duplicate candidates", skipped_dup)

        if created:
            from src.execution.services.audit_logger import AuditLogger
            await AuditLogger.log(
                self.db,
                user_id=user_id,
                action="task_auto_extract",
                actor_type="user",
                actor_id=user_id,
                target_type="task",
                target_id=created[0].id if created else None,
                detail={"created_count": len(created), "scanned_count": scanned},
            )

        return created

    async def _memory_ids_already_in_tasks(self, user_id: str) -> set:
        """返回所有 user 已有任务中链接过的 memory_id 集合, 用于幂等去重。"""
        result = await self.db.execute(
            select(LifeTask.linked_memory_ids).where(LifeTask.user_id == user_id)
        )
        covered: set = set()
        for row in result.all():
            text = row[0]
            for mid in _safe_json_list(text):
                covered.add(mid)
        return covered

    def _build_extract_prompt(self, memories: List[CommittedMemory]) -> str:
        lines = []
        for i, m in enumerate(memories[:30]):
            lines.append(
                f"[{i+1}] id={m.id} 标题={m.title} 内容={(m.body or '')[:200]}"
            )
        block = "\n".join(lines) if lines else "（无）"
        return build_extract_prompt(block, max_count=min(len(memories), 10))

    def _parse_extract_payload(self, raw) -> List[dict]:
        from src.shared.utils.llm_output import extract_json_list
        data = extract_json_list(raw)
        if not data:
            return []
        return [item for item in data if isinstance(item, dict) and item.get("title")]


def task_to_response(task: LifeTask) -> dict:
    """把 LifeTask 转成 schemas.os.TaskResponse 兼容 dict。"""
    return _to_dict(task)
