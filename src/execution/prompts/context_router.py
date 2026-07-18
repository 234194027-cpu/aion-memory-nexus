"""从 src/services/context_router.py 提取的 prompt 模板。"""


def build_routing_prompt(
    message: str,
    history_block: str,
    *,
    persona_block: str = "",
    memory_summary_block: str = "",
    task_context_block: str = "",
) -> str:
    """构建认知 OS 路由器的 LLM 提示 (v3)。"""
    extra_blocks = ""
    if persona_block:
        extra_blocks += f"\n【当前 Persona】\n{persona_block}\n"
    if memory_summary_block:
        extra_blocks += f"\n【已知记忆摘要】\n{memory_summary_block}\n"
    if task_context_block:
        extra_blocks += f"\n【活跃任务】\n{task_context_block}\n"

    return f"""你是「Aion Memory Nexus（永识中枢）」的认知 OS 路由器 (v3)。给定用户的最新消息和最近对话历史, 判断:
1. intent: ask | store | decide | reflect | compare | review | manage_task | unknown
2. recall_level: task_only | work_context | personal_context | full_trusted
3. suggested_agent_type: memory_curator | cognitive_advisor | task_assistant | default
4. confidence: 0.0 ~ 1.0
5. rationale: 一句话中文解释

【最近对话历史 (最多 6 条)】
{history_block}
{extra_blocks}
【用户最新消息】
{message}

约束:
- intent=store 用于「帮我记住/保存/记录」
- intent=decide 用于「我决定/已经决定/做出决定」
- intent=compare 用于「对比/比较/哪个好」
- intent=review 用于「周报/复盘/总结/回顾」
- intent=manage_task 用于「任务/计划/待办/接下来要」
- intent=reflect 用于「为什么/解释/反思」
- intent=ask 是兜底问答
- recall_level 越大召回越多, store 用 full_trusted
- 严禁返回 markdown / 解释文字, 只返回严格 JSON

返回格式 (严格 JSON, 末尾不留逗号):
{{
  "intent": "...",
  "recall_level": "...",
  "suggested_agent_type": "...",
  "confidence": 0.0,
  "rationale": "...",
  "execution_strategy": {{
    "mode": "single",
    "priority_order": ["step1"],
    "estimated_steps": 1
  }},
  "blocked_info": []
}}
"""
