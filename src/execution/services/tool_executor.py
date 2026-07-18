"""Tool Layer 基础框架。

提供可扩展的工具注册和执行机制，供 Multi-Agent Orchestrator 调用。
"""
import logging
import os
from abc import ABC, abstractmethod
from typing import Dict, Optional
from pathlib import Path

logger = logging.getLogger(__name__)

# 安全开关：本地文件读取工具默认禁用。该工具允许读取任意路径文件（受 allowlist 限制），
# 启用后只能读取 OBSIDIAN_VAULT_PATH 与 MEDIA_STORAGE_DIR 下的文件。
ENABLE_FILESYSTEM_TOOL = os.getenv("ENABLE_FILESYSTEM_TOOL", "").lower() in ("1", "true", "yes")


class BaseTool(ABC):
    """工具基类"""
    name: str = ""
    description: str = ""

    @abstractmethod
    async def execute(self, user_id: str, params: dict) -> dict:
        """执行工具，返回 {"status": "success"|"error", "result": ..., "error": str}"""
        raise NotImplementedError

    def to_dict(self) -> dict:
        return {"name": self.name, "description": self.description}


class MemoryTool(BaseTool):
    """内部记忆读取工具"""
    name = "read_memory"
    description = "检索用户记忆"

    def __init__(self, db):
        self.db = db

    async def execute(self, user_id: str, params: dict) -> dict:
        from src.memory.services.retrieval_engine import RetrievalEngine
        engine = RetrievalEngine(self.db)
        question = params.get("question", "")
        recall_level = params.get("recall_level", "work_context")
        result = await engine.reconstruct_context(
            user_id=user_id, question=question, recall_level=recall_level,
        )
        return {"status": "success", "result": result}


class TaskTool(BaseTool):
    """内部任务操作工具"""
    name = "manage_task"
    description = "创建和管理任务"

    def __init__(self, db):
        self.db = db

    async def execute(self, user_id: str, params: dict) -> dict:
        from src.execution.services.task_system import TaskSystem
        ts = TaskSystem(self.db)
        action = params.get("action", "create")
        if action == "create":
            task = await ts.create_task(
                user_id=user_id,
                title=params.get("title", "Untitled"),
                description=params.get("description"),
            )
            return {"status": "success", "result": {"task_id": task.id, "title": task.title}}
        elif action == "list":
            tasks = await ts.list_tasks(user_id, status=params.get("status"))
            return {"status": "success", "result": [{"id": t.id, "title": t.title} for t in tasks]}
        return {"status": "error", "error": f"Unknown action: {action}"}


class CodeRunnerTool(BaseTool):
    """代码执行工具占位；没有外部隔离沙箱时始终拒绝执行。"""
    name = "execute_code"
    description = "在外部隔离沙箱中执行 Python 代码（当前未配置，始终禁用）"

    async def execute(self, user_id: str, params: dict) -> dict:
        logger.warning("execute_code rejected because no external sandbox is configured")
        return {"status": "error", "error": "external code sandbox not configured"}


class FileSystemTool(BaseTool):
    """文件系统工具（只读）

    安全说明：默认禁用。启用后仅允许读取 OBSIDIAN_VAULT_PATH 与 MEDIA_STORAGE_DIR 下的文件，
    防止任意文件读取。如需启用，请设置环境变量 ENABLE_FILESYSTEM_TOOL=true。
    """
    name = "read_file"
    description = "读取本地文件（只读，默认禁用，需 ENABLE_FILESYSTEM_TOOL=true）"

    @staticmethod
    def _allowed_base_dirs() -> list[Path]:
        from src.shared.config import OBSIDIAN_VAULT_DIR, MEDIA_STORAGE_DIR
        return [OBSIDIAN_VAULT_DIR.resolve(), MEDIA_STORAGE_DIR.resolve()]

    async def execute(self, user_id: str, params: dict) -> dict:
        # 安全：默认禁用文件读取工具
        if not ENABLE_FILESYSTEM_TOOL:
            logger.warning("read_file tool invoked but disabled (set ENABLE_FILESYSTEM_TOOL=true to enable)")
            return {"status": "error", "error": "filesystem tool disabled"}
        path_str = params.get("path", "")
        if not path_str:
            return {"status": "error", "error": "path required"}
        # 安全：路径遍历防御 - 解析后必须位于允许的根目录之内
        try:
            target = Path(path_str).resolve()
        except (OSError, ValueError):
            return {"status": "error", "error": "invalid path"}
        allowed_bases = self._allowed_base_dirs()
        if not any(
            target == base or base in target.parents
            for base in allowed_bases
        ):
            logger.warning("read_file rejected path outside allowed dirs: %s", target)
            return {"status": "error", "error": "path outside allowed directories"}
        if not target.exists() or not target.is_file():
            return {"status": "error", "error": "file not found"}
        if target.stat().st_size > 1_000_000:  # 1MB limit
            return {"status": "error", "error": "File too large (>1MB)"}
        try:
            with open(target, "r", encoding="utf-8", errors="replace") as f:
                content = f.read(50000)  # 50K chars max
            return {"status": "success", "result": content}
        except Exception as e:
            # 安全：不向调用方泄露内部异常细节
            logger.error("read_file failed: %s", e)
            return {"status": "error", "error": "file read failed"}


# 工具注册表
TOOL_REGISTRY: Dict[str, type] = {
    "read_memory": MemoryTool,
    "manage_task": TaskTool,
    "execute_code": CodeRunnerTool,
    "read_file": FileSystemTool,
}


class ToolExecutor:
    """工具执行引擎"""

    def __init__(self, db):
        self.db = db
        self._tools: Dict[str, BaseTool] = {}

    def get_tool(self, tool_name: str) -> Optional[BaseTool]:
        if tool_name not in self._tools:
            cls = TOOL_REGISTRY.get(tool_name)
            if cls is None:
                return None
            if tool_name in ("read_memory", "manage_task"):
                self._tools[tool_name] = cls(self.db)
            else:
                self._tools[tool_name] = cls()
        return self._tools.get(tool_name)

    async def execute(self, user_id: str, tool_name: str, params: dict) -> dict:
        tool = self.get_tool(tool_name)
        if tool is None:
            return {"status": "error", "error": f"Unknown tool: {tool_name}"}
        try:
            return await tool.execute(user_id, params)
        except Exception as e:
            # 安全：不向调用方泄露内部异常细节（可能含路径、模块名、内部状态等敏感信息）
            logger.error("Tool %s execution failed: %s", tool_name, e)
            return {"status": "error", "error": "tool execution failed"}

    def list_tools(self) -> list:
        tools = []
        for name in TOOL_REGISTRY:
            tool = self.get_tool(name)
            if tool is not None:
                tools.append(tool.to_dict())
        return tools
