"""从 src/services/retrieval_engine.py 提取的 prompt 模板。"""


def build_retrieval_prompt(question: str, memories_text: str) -> str:
    """构建检索引擎的 LLM 聚类提示。"""
    return f"""You are the Retrieval Engine of a Personal AI Memory System.

Your job is to retrieve and reconstruct relevant memories that help understand the user's thinking, decisions, and behavior.

You do NOT answer the user's question.
You do NOT give advice.
You only provide structured context.

User's question:
{question}

Relevant memories (use the EXACT id when referencing):
{memories_text}

Group these memories into the following categories. For each item, include the EXACT memory id from the list above.

Return ONLY a valid JSON object:

{{
  "context_summary": "brief summary of overall background for the question",
  "decision_history": [
    {{"id": 1, "content": "decision content", "reason": "reason at the time", "outcome": "result if known"}}
  ],
  "patterns": ["recurring behavioral pattern 1", "pattern 2"],
  "conflicts": [
    {{"current": "current belief", "past": "past belief", "explanation": "why thinking changed"}}
  ]
}}

Rules:
- Use the EXACT id (integer) from the memory list above. Do NOT invent ids.
- Only include items that genuinely exist in the memories.
- Preserve each memory's epistemic_status: user assertions, imported material,
  agent statements, and model inferences are not interchangeable facts.
- Preserve time: never present an earlier or ended memory as the user's current
  view without identifying its valid_from/valid_until window and any conflict.
- If a category is empty, return an empty array.
- Be concise.
- Return ONLY the JSON object, no commentary."""
