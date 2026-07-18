"""从 src/services/conflict_checker.py 提取的 prompt 模板 (v2.0)。"""

# v2.0 支持的 conflict_type
VALID_CONFLICT_TYPES = {
    "belief_conflict",      # 观点冲突
    "decision_conflict",    # 决策冲突
    "preference_conflict",  # 偏好冲突
    "principle_conflict",   # 原则冲突
    "strategy_conflict",    # 策略冲突
    "timeline_change",      # 阶段变化
    "correction",           # 明确修正
}

# v2.0 支持的 interpretation
VALID_INTERPRETATIONS = {
    "growth",               # 认知进化
    "changed_context",      # 外部条件变化
    "inconsistency",        # 自相矛盾
    "repeated_error",       # 重复错误
    "unknown",              # 信息不足
}


def build_conflict_prompt(candidate_block: str, memories_block: str) -> str:
    """构建冲突检测模块的 LLM 提示 (v2.0)。

    要求 LLM 返回的每条冲突包含 conflict_type 和 interpretation 字段。
    """
    return f"""You are the Conflict Detection module of a Personal Memory System (v2.0).

Your job is to decide whether a NEW candidate memory contradicts any EXISTING memory
in the user's library.

A "conflict" means semantic contradiction or direct incompatibility (e.g. "caffeine keeps me awake"
vs "caffeine makes me sleepy" / "I prefer X" vs "I hate X" / "I will use SQLite" vs "I will use PostgreSQL").

Similar topic but compatible statements (e.g. elaboration, refinement, or addition) are NOT conflicts —
they belong in "similar_memories" with a similarity score.

---

NEW candidate memory:
{candidate_block}

---

EXISTING memories (use the EXACT id from the list, never invent):
{memories_block}

---

Return ONLY a valid JSON object in the following shape:

{{
  "conflicts": [
    {{
      "memory_id": "<id from existing list, integer or string>",
      "title": "<title of the existing memory>",
      "memory_type": "<type of the existing memory>",
      "severity": "high" | "medium" | "low",
      "conflict_type": "belief_conflict" | "decision_conflict" | "preference_conflict" | "principle_conflict" | "strategy_conflict" | "timeline_change" | "correction",
      "interpretation": "growth" | "changed_context" | "inconsistency" | "repeated_error" | "unknown",
      "explanation": "1-2 sentence explanation of why these contradict",
      "suggested_resolution": "supersede_old" | "merge" | "keep_both" | "needs_user_review"
    }}
  ],
  "similar_memories": [
    {{"memory_id": "<id>", "title": "<title>", "similarity": 0.0}}
  ]
}}

Rules:
- Only flag a real semantic contradiction as a "conflict". Mere topical overlap is NOT a conflict.
- severity: "high" = direct contradiction, "medium" = partial / likely contradiction, "low" = soft tension.
- conflict_type: Classify the nature of the conflict:
    - "belief_conflict" — 观点冲突: different beliefs or opinions about the same topic
    - "decision_conflict" — 决策冲突: a new decision contradicts a past decision
    - "preference_conflict" — 偏好冲突: stated preferences have changed
    - "principle_conflict" — 原则冲突: violates a previously stated principle
    - "strategy_conflict" — 策略冲突: different strategies/approaches for the same goal
    - "timeline_change" — 阶段变化: timeline or plan has been revised
    - "correction" — 明确修正: the user explicitly corrects a past record
- interpretation: Classify what the conflict means:
    - "growth" — 认知进化: the user has evolved their thinking (positive)
    - "changed_context" — 外部条件变化: external circumstances changed, making the old view outdated
    - "inconsistency" — 自相矛盾: the user contradicts themselves without clear reason
    - "repeated_error" — 重复错误: repeating a known mistake pattern
    - "unknown" — 信息不足: not enough information to determine the cause
- suggested_resolution:
    - "supersede_old" — the new memory clearly replaces the old one.
    - "merge" — they should be combined into a single memory.
    - "keep_both" — both are valuable and not mutually exclusive.
    - "needs_user_review" — ambiguity, defer to human.
- If there are no conflicts, return "conflicts": [].
- "similar_memories" should be the top-5 most similar (not conflicting) memories with a similarity 0.0-1.0.
- Use the EXACT memory_id as given in the list above.
- Return ONLY the JSON object, no commentary."""
