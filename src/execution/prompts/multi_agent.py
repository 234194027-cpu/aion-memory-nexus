"""从 src/services/multi_agent_orchestrator.py 提取的 prompt 模板。"""


def build_agent_prompt(
    *,
    agent_name: str,
    agent_role: str,
    persona_block: str,
    context_summary: str,
    memory_block: str,
    decision_block: str,
    question: str,
) -> str:
    """构建单个子 agent 的回答提示。"""
    return f"""你是「{agent_name}」, 角色: {agent_role}。
{persona_block}

请基于以下信息回答用户的问题, 表达你独有的视角和倾向 (用中文, 200~500 字内)。

【上下文摘要】
{context_summary or '（无）'}

【相关记忆】
{memory_block}

【决策历史】
{decision_block}

用户问题: {question}
你的回答:
"""


def build_synthesize_prompt(
    *,
    persona_block: str,
    question: str,
    draft_block: str,
) -> str:
    """构建多 agent 综合答案的提示。"""
    return f"""你是一名高级「认知军师」, 需要综合多个 agent 的初稿, 给出一份统一答案。

【用户人格画像】
{persona_block}

【用户问题】
{question}

【各 Agent 初稿】
{draft_block}

要求:
- 用中文给出统一答案, 400~800 字内;
- 提炼共识 + 指出分歧, 不要简单拼接;
- 如有引用记忆/决策, 注明 [记忆:id] 或 [决策:id] (无 id 不强行编);
- 不确定时坦诚说明。

统一答案:
"""


# ---------------------------------------------------------------------------
# Multi-Agent Orchestrator v3 — 4 种角色的 prompt 构建函数
# ---------------------------------------------------------------------------


def build_research_prompt(
    *,
    question: str,
    context: str,
    memories: str,
) -> str:
    """构建 Research Agent 的 prompt。"""
    from src.execution.prompts.agent_roles import RESEARCH_AGENT_PROMPT

    return f"""{RESEARCH_AGENT_PROMPT}

【用户问题】
{question}

【上下文摘要】
{context}

【相关记忆】
{memories}

请严格输出 JSON 对象 (不要 markdown 包裹):
{{
  "findings": ["发现1", "发现2", ...],
  "gaps": ["信息缺口1", "信息缺口2", ...],
  "sources": ["mem_xxx", ...]
}}
"""


def build_planning_prompt(
    *,
    question: str,
    research_output: str,
    open_decisions: str,
) -> str:
    """构建 Planning Agent 的 prompt。"""
    from src.execution.prompts.agent_roles import PLANNING_AGENT_PROMPT

    return f"""{PLANNING_AGENT_PROMPT}

【用户问题】
{question}

【Research Agent 输出】
{research_output}

【待决决策】
{open_decisions}

请严格输出 JSON 对象 (不要 markdown 包裹):
{{
  "steps": [
    {{
      "title": "步骤标题",
      "description": "详细描述",
      "priority": "high" | "medium" | "low",
      "dependencies": ["前置步骤标题", ...]
    }}
  ],
  "rationale": "整体计划的理由"
}}
"""


def build_critic_prompt(
    *,
    plan: str,
    decision_history: str,
    persona: str,
) -> str:
    """构建 Critic Agent 的 prompt。"""
    from src.execution.prompts.agent_roles import CRITIC_AGENT_PROMPT

    return f"""{CRITIC_AGENT_PROMPT}

【计划内容】
{plan}

【决策历史】
{decision_history}

【用户人格画像】
{persona}

请严格输出 JSON 对象 (不要 markdown 包裹):
{{
  "risks": [
    {{
      "risk": "风险描述",
      "severity": "high" | "medium" | "low",
      "suggestion": "应对建议"
    }}
  ],
  "objections": ["反对意见1", "反对意见2", ...],
  "historical_lessons": ["历史教训1", "历史教训2", ...]
}}
"""


def build_executor_prompt(
    *,
    final_plan: str,
    tool_permissions: str,
) -> str:
    """构建 Executor Agent 的 prompt。"""
    from src.execution.prompts.agent_roles import EXECUTOR_AGENT_PROMPT

    return f"""{EXECUTOR_AGENT_PROMPT}

【最终计划】
{final_plan}

【可用工具及权限】
{tool_permissions}

请严格输出 JSON 对象 (不要 markdown 包裹):
{{
  "actions_taken": [
    {{
      "action": "动作描述",
      "tool_used": "工具名或null",
      "status": "success" | "failed" | "skipped"
    }}
  ],
  "results": ["结果1", "结果2", ...],
  "next_steps": ["后续建议1", "后续建议2", ...]
}}
"""
