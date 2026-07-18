"""从 src/services/persona_engine.py 提取的 prompt 模板。"""

from typing import Dict, Optional


def build_persona_prompt(
    memories_count: int,
    listing: str,
    old_snapshot: Optional[Dict] = None,
) -> str:
    """构建人格画像引擎的 LLM 提示。

    Args:
        memories_count: 记忆数量。
        listing: 记忆列表文本。
        old_snapshot: 如有旧 snapshot，传入 {"traits_json": ..., "patterns_json": ..., "biases_json": ...}。
    """
    context_block = ""
    if old_snapshot:
        old_traits = old_snapshot.get("traits_json", "[]")
        old_patterns = old_snapshot.get("patterns_json", "[]")
        old_biases = old_snapshot.get("biases_json", "[]")
        context_block = (
            "\n【上一次画像快照（增量基础）】\n"
            f"旧 traits: {old_traits}\n"
            f"旧行为模式: {old_patterns}\n"
            f"旧认知偏差: {old_biases}\n"
            "请在此基础上**增量更新**：保留仍成立的特征，修正已有变化的，补充新发现的。\n"
        )

    return (
        '你是"人格画像引擎"，请从下面用户的记忆库中归纳出完整的人格画像。\n'
        + context_block
        + "\n输出必须是严格 JSON，字段如下:\n"
        "{\n"
        '  "traits": {\n'
        '    "decision_style": "决策风格描述",\n'
        '    "risk_profile": "风险偏好描述",\n'
        '    "thinking_mode": "思维模式描述",\n'
        '    "execution_style": "执行风格描述",\n'
        '    "stability": "稳定性描述"\n'
        "  },\n"
        '  "trait_details": [\n'
        '    {"category": "decision_style|values|habits|principles|social|cognitive",\n'
        '     "claim": "...", "evidence_memory_ids": [1,2,...], "confidence": 0.8}\n'
        "  ],\n"
        '  "behavior_patterns": ["行为模式1", "行为模式2", "..."],\n'
        '  "decision_patterns": ["决策模式1", "决策模式2", "..."],\n'
        '  "biases": ["认知偏差1", "认知偏差2", "..."],\n'
        '  "evolution_trends": ["进化趋势1", "进化趋势2", "..."],\n'
        '  "strengths": ["优势1", "优势2", "..."],\n'
        '  "watchouts": ["需要注意1", "需要注意2", "..."],\n'
        '  "summary": "200字以内的画像摘要，可直接展示给用户",\n'
        '  "summary_model": "模型内部对用户的简短认知模型描述",\n'
        '  "confidence": 0.0\n'
        "}\n\n"
        "要求：\n"
        "- trait_details 中每条 claim 必须有至少 1 个 evidence_memory_id 真实存在于 [记忆列表]\n"
        "- trait_details 中 category 仅允许: decision_style, values, habits, principles, social, cognitive\n"
        "- confidence 在 0~1 之间，反映整体画像可靠度\n"
        "- summary 不超过 200 字\n"
        '- 如果记忆太少（比如 < 5 条）则 trait_details 输出空数组，summary 设为"记忆不足，暂无法生成画像"\n'
        "- behavior_patterns / decision_patterns / biases / evolution_trends 为空时输出空数组\n\n"
        f"用户记忆（共 {memories_count} 条，每条形如 [id=序号] (类型, importance, confidence) 标题: 内容）\n"
        f"{listing}\n"
    )
