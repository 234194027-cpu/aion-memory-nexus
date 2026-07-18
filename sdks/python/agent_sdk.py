"""
人生记忆系统 - Agent 调用 SDK（生产可用）

用法：
    from agent_sdk import MemoryAgent

    agent = MemoryAgent(base_url="http://localhost:8000", token="<your_token>")

    # 对话开始：获取思维上下文
    ctx = agent.before_start("修复登录Bug", project_id="life-memory-system")
    print(ctx.summary)

    # 对话中：搜索记忆
    results = agent.search("之前的数据库选型是怎么决策的？")
    for m in results.relevant_memories:
        print(f"- {m.title}")

    # 对话结束：保存会话
    agent.after_end(
        session_summary="修复了密码哈希Bug",
        decisions=[{"content": "用 pbkdf2_sha256 替代 bcrypt"}],
        actions=[{"content": "修改 auth.py"}],
        artifacts=[{"name": "auth.py"}],
    )
"""

import httpx
from typing import List, Dict, Optional
from dataclasses import dataclass, field


@dataclass
class MemoryItem:
    id: str
    title: str
    body: str
    memory_type: str
    importance: float
    confidence: float
    tags: List[str] = field(default_factory=list)
    similarity: float = 0.0


@dataclass
class ContextPack:
    summary: str
    context_summary: str
    memories: List[MemoryItem]
    decision_history: List[dict]
    patterns: List[str]
    conflicts: List[dict]
    entities: List[str]
    meta: dict = field(default_factory=dict)


class MemoryAgent:

    def __init__(self, base_url: str = "http://localhost:8000", token: Optional[str] = None, agent_id: Optional[str] = None):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.agent_id = agent_id
        self._client = httpx.Client(timeout=60)

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json"}
        if self.token:
            h["X-Agent-Token"] = self.token
        return h

    def before_start(
        self,
        task: str,
        project_id: Optional[str] = None,
        recall_level: str = "work_context",
        top_k: int = 10,
    ) -> ContextPack:
        """对话开始前调用，获取思维上下文"""
        r = self._client.post(
            f"{self.base_url}/api/agent/before-start",
            headers=self._headers(),
            json={
                "agent_id": self.agent_id,
                "task": task,
                "project_id": project_id,
                "recall_level": recall_level,
                "top_k": top_k,
            },
        )
        r.raise_for_status()
        data = r.json()["context_pack"]
        return ContextPack(
            summary=data.get("summary", ""),
            context_summary=data.get("context_summary", ""),
            memories=[MemoryItem(**m) for m in data.get("memories", [])],
            decision_history=data.get("decision_history", []),
            patterns=data.get("patterns", []),
            conflicts=data.get("conflicts", []),
            entities=data.get("entities", []),
            meta=data.get("meta", {}),
        )

    def search(
        self,
        query: str,
        project_id: Optional[str] = None,
        recall_level: str = "work_context",
        top_k: int = 10,
    ) -> ContextPack:
        """对话中检索记忆"""
        return self.before_start(query, project_id, recall_level, top_k)

    def after_end(
        self,
        session_summary: str,
        decisions: Optional[List[Dict]] = None,
        actions: Optional[List[Dict]] = None,
        artifacts: Optional[List[Dict]] = None,
        project_id: Optional[str] = None,
        raw_transcript_ref: Optional[str] = None,
    ) -> Dict:
        """对话结束后调用，保存会话记忆"""
        r = self._client.post(
            f"{self.base_url}/api/agent/after-end",
            headers=self._headers(),
            json={
                "agent_id": self.agent_id,
                "session_summary": session_summary,
                "decisions": decisions or [],
                "actions": actions or [],
                "artifacts": artifacts or [],
                "project_id": project_id,
                "raw_transcript_ref": raw_transcript_ref,
            },
        )
        r.raise_for_status()
        return r.json()

    def list_types(self) -> Dict:
        """列出支持的 Agent 类型"""
        r = self._client.get(
            f"{self.base_url}/api/agent/types",
            headers=self._headers(),
        )
        r.raise_for_status()
        return r.json()

    def close(self):
        self._client.close()


def get_prompt_for_agent(base_url: str, admin_token: str, agent_id: str, regenerate: bool = False) -> Dict:
    """获取 Agent 提示词（管理员用）"""
    r = httpx.get(
        f"{base_url}/api/admin/agents/{agent_id}/prompt",
        params={"regenerate_token": regenerate},
        headers={"Authorization": f"Bearer {admin_token}"},
        timeout=60,
    )
    r.raise_for_status()
    return r.json()


def create_agent(
    base_url: str,
    admin_token: str,
    agent_name: str,
    agent_type: str = "custom",
    mission: str = "",
    role: str = "",
    goals: Optional[List[str]] = None,
    constraints: Optional[List[str]] = None,
    instructions: str = "",
    llm_provider: Optional[str] = None,
    llm_model: Optional[str] = None,
    llm_api_key: Optional[str] = None,
    default_recall_level: str = "work_context",
) -> Dict:
    """创建新 Agent（管理员用）"""
    import json
    r = httpx.post(
        f"{base_url}/api/admin/agents",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "agent_name": agent_name,
            "agent_type": agent_type,
            "default_recall_level": default_recall_level,
            "llm_provider": llm_provider,
            "llm_model": llm_model,
            "llm_api_key": llm_api_key,
            "mission": mission,
            "role": role,
            "goals": json.dumps(goals or [], ensure_ascii=False),
            "constraints": json.dumps(constraints or [], ensure_ascii=False),
            "instructions": instructions,
        },
        timeout=60,
    )
    r.raise_for_status()
    return r.json()