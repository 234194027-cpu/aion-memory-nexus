"""从 src/services/simulation_engine.py 提取的 prompt 模板。

包含:
- 反事实推演 (Simulation Engine)
- Timeline 高级视图 (decision chains / project evolution / cognitive shifts)
- Simulation Engine v3 (历史模式分析 / 类似决策检索)
"""
from typing import List, Dict


# ---------------------------------------------------------------------------
# Simulation Engine
# ---------------------------------------------------------------------------


def build_simulation_prompt(
    *,
    persona_block: str,
    baseline: str,
    decision_block: str,
    question: str,
    horizon_days: int,
) -> str:
    """构建反事实推演引擎的 LLM 提示。"""
    return f"""你是一名「反事实推演引擎」, 任务是扮演"用户在过去的决策风格 + 历史模式", 推断"如果当初 ... 会怎样"的可能后果。

【用户人格画像】
{persona_block}

【用户当前真实情况 (baseline)】
{baseline}

【用户当前 open 决策】
{decision_block}

【反事实问题】
{question}

【推演视角跨度】
未来约 {horizon_days} 天。

请以"用户在过去 N 个月的决策模式 + 当前处境 + 反事实假设 = 推断后果"的角色, 给出:
1. counterfactual: 反事实场景的假设 (从用户的视角, 用第一人称描述, 150 字内)。
2. outcome: 在该反事实假设下, 未来 {horizon_days} 天内最可能发生的后果 (250~500 字)。
3. confidence: 0.0~1.0, 表示对推断的自信程度 (无 baseline/无 persona/假设过大都会降低 confidence)。

【输出格式 (严格 JSON)】
{{
  "counterfactual": "...",
  "outcome": "...",
  "confidence": 0.55
}}

只输出 JSON, 不加任何前后文字。
"""


# ---------------------------------------------------------------------------
# Timeline 高级视图 Prompts
# ---------------------------------------------------------------------------


def build_decision_chain_prompt(decisions_list: List[Dict]) -> str:
    """构建决策链分析的 LLM 提示。"""
    import json
    dec_json = json.dumps(decisions_list, ensure_ascii=False, default=str)
    return f"""你是一名「决策链分析师」, 任务是分析用户一系列决策之间的因果关系。

【决策列表 (按时间排序)】
{dec_json}

请识别决策间的因果关系, 将它们归入链中, 并为每条链标注模式:
- progressive_refinement: 递进优化, 后续决策在前一决策基础上改进
- reversal: 翻转, 后续决策推翻了前一决策
- escalation: 升级, 后续决策扩大了前一决策的范围或影响
- abandonment: 放弃, 后续决策表明用户放弃了前一决策的方向

【输出格式 (严格 JSON 数组)】
[
  {{
    "chain_id": "chain_001",
    "decisions": [
      {{"decision_id": "...", "title": "...", "status": "...", "decided_at": "...", "outcome": "..."}}
    ],
    "pattern": "progressive_refinement",
    "summary": "这些决策围绕同一主题递进优化..."
  }}
]

如果决策之间没有明显关联, 每个决策单独成链。
只输出 JSON, 不加任何前后文字。
"""


def build_project_evolution_prompt(project_events: List[Dict]) -> str:
    """构建项目演化分析的 LLM 提示。"""
    import json
    events_json = json.dumps(project_events, ensure_ascii=False, default=str)
    return f"""你是一名「项目演化分析师」, 任务是分析一个项目随时间的里程碑和阶段变化。

【项目事件 (按时间排序)】
{events_json}

请分析:
1. 每个事件的 significance (高/中/低) 及简述
2. 项目经历了哪些阶段 (如: 规划/启动/开发/迭代/维护/搁置)
3. 当前所处阶段 (current_phase)
4. 项目健康度 (health_score: 0.0~1.0, 考虑决策频率、任务完成率、是否停滞等)
5. 一句话总结 (summary)

【输出格式 (严格 JSON)】
{{
  "milestones": [
    {{"date": "2024-01-01", "title": "...", "kind": "memory|decision|task", "significance": "..."}}
  ],
  "current_phase": "开发",
  "health_score": 0.7,
  "summary": "项目处于开发阶段, 近期决策频率高..."
}}

只输出 JSON, 不加任何前后文字。
"""


def build_cognitive_shift_prompt(before_memories: List[Dict], after_memories: List[Dict]) -> str:
    """构建认知变化检测的 LLM 提示。"""
    import json
    before_json = json.dumps(before_memories, ensure_ascii=False, default=str)
    after_json = json.dumps(after_memories, ensure_ascii=False, default=str)
    return f"""你是一名「认知变化检测器」, 任务是对比用户前后期记忆, 检测观点/偏好的转变。

【前半段记忆】
{before_json}

【后半段记忆】
{after_json}

请识别:
1. 用户在哪些话题上的观点发生了变化
2. 变化类型 (shift_type):
   - growth: 成长, 认知更深入
   - reversal: 翻转, 观点反向改变
   - deepening: 深化, 对同一方向更坚定
   - broadening: 拓宽, 视角更广泛
3. 每个变化的 confidence (0.0~1.0)

【输出格式 (严格 JSON 数组)】
[
  {{
    "shift_id": "shift_001",
    "topic": "...",
    "before": {{"period": "前半段时间范围", "stance": "...", "evidence_ids": ["..."]}},
    "after": {{"period": "后半段时间范围", "stance": "...", "evidence_ids": ["..."]}},
    "shift_type": "growth",
    "confidence": 0.75,
    "detected_at": "2024-01-01"
  }}
]

如果没有明显变化, 返回空数组 []。
只输出 JSON, 不加任何前后文字。
"""


# ---------------------------------------------------------------------------
# Simulation Engine v3 Prompts
# ---------------------------------------------------------------------------


def build_simulation_v3_prompt(
    *,
    persona_block: str,
    baseline: str,
    decision_block: str,
    question: str,
    horizon_days: int,
    historical_patterns: Dict,
    similar_decisions_block: str,
) -> str:
    """构建 v3 反事实推演引擎的 LLM 提示 (含历史模式和类似决策)。"""
    import json
    patterns_json = json.dumps(historical_patterns, ensure_ascii=False, default=str)
    return f"""你是一名「反事实推演引擎 v3」, 任务是扮演"用户在过去的决策风格 + 历史模式 + 类似决策经验", 推断"如果当初 ... 会怎样"的可能后果。

【用户人格画像】
{persona_block}

【用户当前真实情况 (baseline)】
{baseline}

【用户当前 open 决策】
{decision_block}

【用户历史决策模式】
{patterns_json}

【类似历史决策经验】
{similar_decisions_block}

【反事实问题】
{question}

【推演视角跨度】
未来约 {horizon_days} 天。

请结合历史模式和类似决策经验, 给出:
1. counterfactual: 反事实场景的假设 (从用户的视角, 用第一人称描述, 150 字内)。
2. outcome: 在该反事实假设下, 未来 {horizon_days} 天内最可能发生的后果 (250~500 字)。
3. confidence: 0.0~1.0, 表示对推断的自信程度。
4. risk_factors: 风险因素列表 (至少 1 个, 最多 5 个)。
5. risk_level: "low" / "medium" / "high"。
6. similar_past_decisions: 类似历史决策列表 (每项含 decision_id, title, status, lesson)。
7. historical_pattern_match: 模式匹配分析 (含 pattern, confidence, warning)。

【输出格式 (严格 JSON)】
{{
  "counterfactual": "...",
  "outcome": "...",
  "confidence": 0.55,
  "risk_factors": ["风险1", "风险2"],
  "risk_level": "medium",
  "similar_past_decisions": [
    {{"decision_id": "...", "title": "...", "status": "...", "lesson": "..."}}
  ],
  "historical_pattern_match": {{
    "pattern": "...",
    "confidence": 0.7,
    "warning": "你过去类似决策中有 60% 最终放弃"
  }}
}}

只输出 JSON, 不加任何前后文字。
"""


def build_similar_decision_lesson_prompt(decision: Dict, question: str) -> str:
    """为类似历史决策总结教训的 LLM 提示。"""
    import json
    dec_json = json.dumps(decision, ensure_ascii=False, default=str)
    return f"""请根据当前问题, 从以下历史决策中总结一条教训 (lesson), 最多 100 字。

【当前问题】
{question}

【历史决策】
{dec_json}

只输出教训文字, 不加任何前后文字。
"""
