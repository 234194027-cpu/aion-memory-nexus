"""从 src/services/memory_rewriter.py 提取的 prompt 模板。"""


def build_rewrite_prompt(mem_block: str) -> str:
    """构建记忆整理器的 LLM 提示。"""
    return f"""You are the Memory Rewriter module of a Personal Memory System.

Your job: scan the user's recent governed memories, then propose
restructuring actions to keep the memory library clean and high-signal.

Allowed actions (use EXACT strings):
- "merge": combine 2 or more memories that are near-duplicates or fragmented
  expressions of the same fact/decision. Provide "memory_ids" (>=2) and
  "merged_draft" (the proposed combined body).
- "rewrite": improve a single memory's wording/structure. Provide
  "memory_id" and "draft_body".
- "archive": mark a memory as no longer relevant. Provide "memory_id".
- "link": create a semantic relation between two memories without modifying
  either memory. Provide "memory_ids" (exactly 2), "relation_type" (one of:
  supports, contradicts, supersedes, duplicates, updates, explains, belongs_to,
  caused_by, resulted_in), and "reason".

Do NOT propose deletion of decisions, principles, or user-shared facts.
Do NOT propose action against memories outside the provided list.
Every proposal must have a short "reason" explaining the rationale.

---

Recent committed memories (use EXACT M-id, e.g. "M1", "M2"):
{mem_block}

Return ONLY a valid JSON object:

{{
  "proposals": [
    {{
      "action": "merge" | "rewrite" | "archive" | "link",
      "memory_ids": ["M1", "M2"],
      "memory_id": null,
      "reason": "...",
      "merged_draft": "...",
      "draft_body": null,
      "relation_type": null
    }}
  ]
}}

Rules:
- For "merge" actions, fill "memory_ids" and "merged_draft"; leave "memory_id"
  and "draft_body" and "relation_type" null.
- For "rewrite" actions, fill "memory_id" and "draft_body"; leave
  "memory_ids" and "merged_draft" and "relation_type" null.
- For "archive" actions, fill "memory_id"; leave others null.
- For "link" actions, fill "memory_ids" (exactly 2), "relation_type", and
  "reason"; leave "merged_draft" and "draft_body" null.
- If the library is already clean, return "proposals": [].
- Limit to at most 20 proposals per chunk.
- Return ONLY the JSON object, no commentary.
"""
