"""从 src/services/task_system.py 提取的 prompt 模板。"""


def build_extract_prompt(block: str, max_count: int) -> str:
    """构建任务抽取助手的 LLM 提示。"""
    return f"""你是「Aion Memory Nexus（永识中枢）」的任务抽取助手。给定最近 N 天用户产生的 TASK 类记忆, 抽取可以变成 LifeTask 的候选任务。

【输入记忆】
{block}

请严格输出 JSON 数组 (不要 markdown / 解释):
[
  {{
    "title": "一句话任务标题 (中文, 不超过 60 字)",
    "description": "可执行描述 (中文, 一两句话)",
    "priority": "P0" | "P1" | "P2" | "P3",
    "linked_memory_ids": ["mem_xxx", "..."]
  }}
]

约束:
- 最多返回 {max_count} 条
- 没有合适候选时返回 []
"""


def build_decompose_prompt(
    title: str,
    description: str,
    priority: str,
    max_sub_tasks: int,
) -> str:
    """构建任务拆解助手的 LLM 提示。"""
    return f"""你是一个任务拆解专家。请将以下任务拆解为 {max_sub_tasks} 个子任务。

主任务：{title}
描述：{description or '（无描述）'}
优先级：{priority}

请输出严格 JSON：
[
  {{"title": "子任务1标题", "description": "描述", "priority_score": 0.8}},
  {{"title": "子任务2标题", "description": "描述", "priority_score": 0.6}}
]

要求：
- 子任务应独立可执行
- 按依赖顺序排列
- 每个子任务的 priority_score 在 0-1 之间
- 不要输出 markdown, 只输出 JSON 数组
"""
