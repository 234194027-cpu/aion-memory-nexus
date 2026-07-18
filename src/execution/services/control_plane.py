"""ControlPlane Service (Gen 3 / Cognitive OS) — 统一调度层。

核心职责:
1. 控制组件间的信息流
2. 管理写入权限（外部 Agent 只能写 RawEvent/工作案件，不能直接写正式记忆）
3. 提供 per-user 的记忆写入策略配置
4. 为 AdvisorEngine 组装上下文 (整合 retrieval + persona + conflicts)
5. 带控制检查的路由请求

设计要点:
- 封装 ContextRouter, 在其基础上增加权限控制和策略检查
- 写入策略: deny-by-default, 显式 allow 才放行
- 正式记忆只能由内置工作 Agent 的治理事务写入
- 上下文组装: 并行调用 RetrievalEngine + PersonaEngine + ConflictChecker
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from src.execution.services.context_router import ContextRouter
from src.memory.services.retrieval_engine import RetrievalEngine
from src.cognition.services.persona_engine import PersonaEngine
from src.memory.services.conflict_checker import ConflictChecker
from src.execution.services.tool_permission import ToolPermissionService

logger = logging.getLogger(__name__)


# 写入类型定义
WRITE_TYPE_WORK_CASE = "memory_work_case"
WRITE_TYPE_COMMITTED = "committed_memory"
WRITE_TYPE_DECISION = "decision"
WRITE_TYPE_TASK = "task"

VALID_WRITE_TYPES = {
    WRITE_TYPE_WORK_CASE,
    WRITE_TYPE_COMMITTED,
    WRITE_TYPE_DECISION,
    WRITE_TYPE_TASK,
}

# 默认写入策略
DEFAULT_WRITE_POLICY = {
    WRITE_TYPE_WORK_CASE: True,   # Agent 可以提交事件并形成工作案件
    WRITE_TYPE_COMMITTED: False,  # 需要显式授权才能写 CommittedMemory
    WRITE_TYPE_DECISION: True,    # 可以创建决策记录
    WRITE_TYPE_TASK: True,        # 可以创建任务
}

# 需要额外检查的写入类型
RESTRICTED_WRITE_TYPES = {WRITE_TYPE_COMMITTED}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ControlPlane:
    """统一调度层 — 控制信息流、权限、上下文组装。"""

    def __init__(self, db: AsyncSession):
        self.db = db
        self._router = ContextRouter(db)
        self._permission_service = ToolPermissionService(db)

    async def check_write_permission(
        self,
        user_id: str,
        agent_id: str,
        write_type: str,
    ) -> bool:
        """检查 agent 是否有权限执行指定类型的写入操作。

        规则:
        - write_type 必须是合法的写入类型
        - 工作案件写入允许
        - CommittedMemory 只允许由内置治理事务执行
        - 其他类型检查用户策略

        Args:
            user_id: 用户 ID
            agent_id: Agent ID
            write_type: 写入类型 (memory_work_case / committed_memory / decision / task)

        Returns:
            bool: 是否允许写入
        """
        if write_type not in VALID_WRITE_TYPES:
            logger.warning(
                f"check_write_permission: unknown write_type={write_type}, "
                f"agent_id={agent_id}, user_id={user_id}"
            )
            return False

        # 外部 Agent 可提交工作案件，但不能绕过工作 Agent 直接写正式记忆。
        if write_type == WRITE_TYPE_WORK_CASE:
            return True

        # CommittedMemory 写入需要检查工具权限
        if write_type == WRITE_TYPE_COMMITTED:
            # 检查是否有 add_memory 或 update_memory 工具权限
            for tool_name in ("add_memory", "update_memory"):
                result = await self._permission_service.check(
                    user_id=user_id,
                    agent_id=agent_id,
                    tool_name=tool_name,
                )
                if result.get("allowed"):
                    return True
            return False

        # 其他类型: 检查用户策略 (暂时默认允许)
        policy = await self.get_memory_write_policy(user_id)
        return policy.get(write_type, False)

    async def get_memory_write_policy(self, user_id: str) -> Dict:
        """获取用户的记忆写入策略配置。

        返回每个写入类型的允许状态:
        {
            "memory_work_case": True,
            "committed_memory": False,  # 需要显式授权
            "decision": True,
            "task": True,
            "policy_version": "1.0",
            "updated_at": "2024-01-01T00:00:00Z"
        }

        注意: 当前实现返回默认策略。
        未来可以从 UserSettings 表读取用户自定义策略。
        """
        # TODO: 从数据库读取用户自定义策略 (如果有 UserSettings 表)
        # 当前返回默认策略
        return {
            **DEFAULT_WRITE_POLICY,
            "policy_version": "1.0",
            "updated_at": _now_iso(),
            "note": "默认策略: 外部 Agent 可提交工作案件，正式记忆由内置工作 Agent 自动治理",
        }

    async def route_with_control(
        self,
        user_id: str,
        message: str,
        *,
        agent_id: Optional[str] = None,
        recent_history: Optional[List[Dict]] = None,
        persona: Optional[Dict] = None,
        memory_summary: Optional[List[str]] = None,
        task_context: Optional[List[Dict]] = None,
    ) -> Dict:
        """带控制检查的路由请求 — ContextRouter 的增强版。

        在 ContextRouter.route 的基础上:
        1. 检查 agent 的写入权限
        2. 根据权限过滤 tool_permissions
        3. 添加控制层元数据

        Args:
            user_id: 用户 ID
            message: 用户消息
            agent_id: 可选的 Agent ID (用于权限检查)
            recent_history: 最近的对话历史
            persona: 用户画像
            memory_summary: 记忆摘要
            task_context: 任务上下文

        Returns:
            Dict: 路由结果, 包含 control_plane 元数据
        """
        # 1. 调用基础路由
        base_result = await self._router.route(
            user_id=user_id,
            message=message,
            recent_history=recent_history,
            persona=persona,
            memory_summary=memory_summary,
            task_context=task_context,
        )

        # 2. 如果有 agent_id, 检查写入权限并过滤 tool_permissions
        control_meta = {
            "checked_at": _now_iso(),
            "agent_id": agent_id,
            "permission_filtered": False,
        }

        if agent_id:
            intent = base_result.get("intent", "unknown")
            original_permissions = base_result.get("tool_permissions", [])

            # 根据 intent 推断需要的写入类型
            required_write_type = self._intent_to_write_type(intent)

            # 检查写入权限
            if required_write_type:
                has_permission = await self.check_write_permission(
                    user_id=user_id,
                    agent_id=agent_id,
                    write_type=required_write_type,
                )

                if not has_permission:
                    # 过滤掉需要写入权限的工具
                    filtered_permissions = self._filter_permissions_by_write_access(
                        original_permissions, required_write_type
                    )
                    base_result["tool_permissions"] = filtered_permissions
                    control_meta["permission_filtered"] = True
                    control_meta["permission_denied_write_type"] = required_write_type

                    # 添加警告
                    warnings = base_result.get("warnings", [])
                    warnings.append(
                        f"write_permission_denied: agent {agent_id} "
                        f"cannot write {required_write_type}"
                    )
                    base_result["warnings"] = warnings

        base_result["control_plane"] = control_meta
        return base_result

    async def build_context_for_advisor(
        self,
        user_id: str,
        question: str,
        *,
        project_id: Optional[str] = None,
        recall_level: str = "work_context",
        include_persona: bool = True,
        include_conflicts: bool = True,
        include_decisions: bool = True,
    ) -> Dict:
        """为 AdvisorEngine 组装完整上下文。

        并行调用:
        - RetrievalEngine.reconstruct_context: 检索相关记忆
        - PersonaEngine.build_persona: 获取用户画像
        - ConflictChecker.check_for_user: 获取冲突记录

        Args:
            user_id: 用户 ID
            question: 用户问题
            project_id: 可选的项目 ID
            recall_level: 召回级别
            include_persona: 是否包含人格画像
            include_conflicts: 是否包含冲突记录
            include_decisions: 是否包含决策记录 (从检索结果中提取)

        Returns:
            Dict: 组装后的上下文, 包含:
                - retrieval_context: 检索结果
                - persona: 人格画像
                - conflicts: 冲突记录
                - decisions: 相关决策
                - meta: 元数据
        """
        # 并行执行所有查询
        tasks = {
            "retrieval": self._safe_retrieval(
                user_id, question, project_id, recall_level
            ),
        }

        if include_persona:
            tasks["persona"] = self._safe_persona(user_id, project_id)

        if include_conflicts:
            tasks["conflicts"] = self._safe_conflicts(user_id, project_id)

        # 等待所有任务完成
        results = await asyncio.gather(
            *tasks.values(),
            return_exceptions=True,
        )

        # 组装结果
        context = {
            "user_id": user_id,
            "question": question,
            "retrieval_context": {},
            "persona": None,
            "conflicts": [],
            "decisions": [],
            "meta": {
                "built_at": _now_iso(),
                "recall_level": recall_level,
                "project_id": project_id,
                "errors": [],
            },
        }

        task_names = list(tasks.keys())
        for name, result in zip(task_names, results):
            if isinstance(result, Exception):
                context["meta"]["errors"].append(f"{name}_error: {result}")
                logger.warning(f"build_context_for_advisor: {name} failed: {result}")
                continue

            if name == "retrieval":
                context["retrieval_context"] = result
                # 从检索结果中提取决策
                if include_decisions:
                    context["decisions"] = self._extract_decisions_from_context(result)
            elif name == "persona":
                context["persona"] = result
            elif name == "conflicts":
                context["conflicts"] = result

        return context

    # ── 内部辅助方法 ─────────────────────────────────────────────────────

    def _intent_to_write_type(self, intent: str) -> Optional[str]:
        """根据 intent 推断需要的写入类型。"""
        intent_to_write = {
            "store": WRITE_TYPE_WORK_CASE,
            "decide": WRITE_TYPE_DECISION,
            "manage_task": WRITE_TYPE_TASK,
        }
        return intent_to_write.get(intent)

    def _filter_permissions_by_write_access(
        self,
        permissions: List[Dict],
        denied_write_type: str,
    ) -> List[Dict]:
        """根据写入权限过滤工具权限列表。"""
        # 定义写入工具与写入类型的映射
        write_tools_by_type = {
            WRITE_TYPE_WORK_CASE: {"add_memory"},
            WRITE_TYPE_COMMITTED: {"add_memory", "update_memory", "delete_memory"},
            WRITE_TYPE_DECISION: {"create_decision", "update_decision"},
            WRITE_TYPE_TASK: {"create_task", "update_task", "link_task"},
        }

        denied_tools = write_tools_by_type.get(denied_write_type, set())

        filtered = []
        for perm in permissions:
            tool_name = perm.get("tool_name", "")
            if tool_name in denied_tools:
                # 跳过被拒绝的写入工具
                continue
            filtered.append(perm)

        return filtered

    async def _safe_retrieval(
        self,
        user_id: str,
        question: str,
        project_id: Optional[str],
        recall_level: str,
    ) -> Dict:
        """安全调用 RetrievalEngine, 失败时返回空上下文。"""
        try:
            engine = RetrievalEngine(self.db)
            return await engine.reconstruct_context(
                user_id=user_id,
                question=question,
                project_id=project_id,
                recall_level=recall_level,
                top_k=20,
            )
        except Exception as e:
            logger.warning(f"_safe_retrieval failed: {e}")
            return {
                "context_summary": "",
                "decision_history": [],
                "patterns": [],
                "conflicts": [],
                "relevant_memories": [],
                "entities": [],
                "meta": {"total_found": 0, "error": str(e)},
            }

    async def _safe_persona(
        self,
        user_id: str,
        project_id: Optional[str],
    ) -> Optional[Dict]:
        """安全调用 PersonaEngine, 失败时返回 None。"""
        try:
            engine = PersonaEngine(self.db)
            return await engine.build_persona(user_id)
        except Exception as e:
            logger.warning(f"_safe_persona failed: {e}")
            return None

    async def _safe_conflicts(
        self,
        user_id: str,
        project_id: Optional[str],
    ) -> List[Dict]:
        """安全调用 ConflictChecker, 失败时返回空列表。"""
        try:
            checker = ConflictChecker(self.db)
            return await checker.check_for_user(
                user_id, project_id=project_id, limit=5
            )
        except Exception as e:
            logger.warning(f"_safe_conflicts failed: {e}")
            return []

    def _extract_decisions_from_context(self, context: Dict) -> List[Dict]:
        """从检索上下文中提取决策记录。"""
        decisions = []
        decision_history = context.get("decision_history", [])
        for item in decision_history:
            if not isinstance(item, dict):
                continue
            decisions.append({
                "memory_id": item.get("memory_id", ""),
                "content": item.get("content", ""),
                "reason": item.get("reason", ""),
                "outcome": item.get("outcome", ""),
                "memory_type": item.get("memory_type", ""),
                "importance": item.get("importance", 0.0),
            })
        return decisions


# ── 便捷函数 ─────────────────────────────────────────────────────────────


async def check_write_permission(
    db: AsyncSession,
    user_id: str,
    agent_id: str,
    write_type: str,
) -> bool:
    """便捷函数: 检查写入权限。"""
    plane = ControlPlane(db)
    return await plane.check_write_permission(user_id, agent_id, write_type)


async def get_memory_write_policy(
    db: AsyncSession,
    user_id: str,
) -> Dict:
    """便捷函数: 获取用户写入策略。"""
    plane = ControlPlane(db)
    return await plane.get_memory_write_policy(user_id)


async def route_with_control(
    db: AsyncSession,
    user_id: str,
    message: str,
    *,
    agent_id: Optional[str] = None,
    **kwargs,
) -> Dict:
    """便捷函数: 带控制检查的路由。"""
    plane = ControlPlane(db)
    return await plane.route_with_control(
        user_id, message, agent_id=agent_id, **kwargs
    )


async def build_context_for_advisor(
    db: AsyncSession,
    user_id: str,
    question: str,
    **kwargs,
) -> Dict:
    """便捷函数: 为 AdvisorEngine 组装上下文。"""
    plane = ControlPlane(db)
    return await plane.build_context_for_advisor(user_id, question, **kwargs)
