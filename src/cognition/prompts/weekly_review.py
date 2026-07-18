"""从 src/services/weekly_review.py 提取的 prompt 模板。"""


def build_weekly_review_prompt(
    *,
    week_start_str: str,
    week_end_str: str,
    memory_block: str,
    new_block: str,
    resolved_block: str,
    new_memories_count: int,
    decisions_count: int,
) -> str:
    """构建周报生成的 LLM 提示（8 段输出）。"""
    return f"""你是用户的人生记忆周报助手。周期: {week_start_str} ~ {week_end_str}。

【本周新记忆】
{memory_block}

【本周新建决策】
{new_block}

【本周已结决策】
{resolved_block}

请严格输出 JSON (不要任何 markdown 代码块 / 解释文字 / 前缀):

{{
  "summary": "200~500 字的中文周报总览，总结本周的关键记忆、决策主题、可学习的模式。",
  "key_decisions": [
    {{"decision_id": "决策ID", "title": "决策标题", "status": "状态", "relevance": "与本周主题的关联"}}
  ],
  "important_insights": ["重要洞察 1", "重要洞察 2", "..."],
  "repeated_themes": ["反复出现的主题 1", "主题 2", "..."],
  "conflicts_or_changes": [
    {{"conflict_type": "类型", "description": "描述"}}
  ],
  "risks_to_watch": ["需要关注的风险 1", "风险 2", "..."],
  "suggested_focus_next_week": ["建议下周关注 1", "关注 2", "..."],
  "persona_observations": ["对用户人格的观察 1", "观察 2", "..."],
  "open_loops": ["悬而未决 1", "悬而未决 2", "..."],
  "cited_memories": ["引用的记忆ID 1", "..."],
  "cited_decisions": ["引用的决策ID 1", "..."]
}}

要求：
- summary 200~500 字，综合本周所有重要信息
- key_decisions 列出本周最重要的决策（可为空数组）
- important_insights 列出本周值得记录的洞察
- repeated_themes 列出反复出现的思考主题
- conflicts_or_changes 列出观点变化或矛盾（可为空数组）
- risks_to_watch 列出潜在风险
- suggested_focus_next_week 给出下周建议
- persona_observations 对用户人格/行为模式的观察
- open_loops 尚未解决的问题
- cited_memories / cited_decisions 列出引用的 ID
- 所有列表字段为空时输出空数组 []
- new_memories_count: {new_memories_count}, decisions_count: {decisions_count}
"""
