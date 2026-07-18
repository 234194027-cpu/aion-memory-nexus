"""Shared helpers for parsing LLM text output (markdown code-block stripping + JSON extraction).

These replace duplicated logic that previously lived in:
- context_router._parse_llm_json
- advisor_engine._parse_llm_json
- simulation_engine._parse_response
- task_system._parse_decompose_payload / _parse_extract_payload
- life_timeline._parse_json_list / _parse_json_object
- persona_engine._parse_llm_response (inline markdown strip)
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional, Union

logger = logging.getLogger(__name__)


def strip_markdown_code_block(text: str) -> str:
    """Remove markdown ``` or ```json ... ``` wrapping from LLM output."""
    if not text:
        return ""
    text = text.strip()
    if not text.startswith("```"):
        return text
    # Remove opening ``` line
    first_nl = text.find("\n")
    if first_nl != -1:
        text = text[first_nl + 1:]
    else:
        # Single-line code block
        text = text[3:]
    # Remove closing ```
    if text.endswith("```"):
        text = text[:-3]
    return text.strip()


def extract_json(
    text: str,
) -> Union[Dict, List, None]:
    """Try to parse *text* as JSON.

    1. Strip markdown code-block wrappers.
    2. Try direct ``json.loads``.
    3. Fall back to finding the outermost ``{…}`` or ``[…]`` and parsing that.
    4. Return ``None`` on failure.
    """
    if not text:
        return None
    cleaned = strip_markdown_code_block(text)
    # Direct parse
    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, (dict, list)):
            return parsed
    except (json.JSONDecodeError, ValueError):
        pass
    # Fallback: find outermost braces / brackets
    for open_ch, close_ch in [("{", "}"), ("[", "]")]:
        start = cleaned.find(open_ch)
        end = cleaned.rfind(close_ch)
        if start != -1 and end != -1 and end > start:
            try:
                parsed = json.loads(cleaned[start : end + 1])
                if isinstance(parsed, (dict, list)):
                    return parsed
            except (json.JSONDecodeError, ValueError):
                pass
    return None


def extract_json_object(text: str) -> Optional[Dict]:
    """Like :func:`extract_json` but only returns dicts."""
    result = extract_json(text)
    return result if isinstance(result, dict) else None


def extract_json_list(text: str) -> Optional[List]:
    """Like :func:`extract_json` but only returns lists."""
    result = extract_json(text)
    return result if isinstance(result, list) else None


def safe_json_loads(text: Optional[str], default: Any = None) -> Any:
    """``json.loads`` that returns *default* on any failure instead of raising."""
    if not text:
        return default
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# Shared formatting helpers for memory / decision blocks
# ---------------------------------------------------------------------------


def format_memory_block(
    memories: List[Dict],
    limit: int = 10,
) -> str:
    """Format a list of memory dicts into a numbered text block.

    Each dict is expected to have keys like ``memory_type``, ``importance``,
    ``memory_id``, ``title``, ``content``.
    """
    lines = []
    for i, m in enumerate(memories[:limit]):
        lines.append(
            f"[{i+1}] (类型={m.get('memory_type','')}, 重要性={m.get('importance',0):.2f}) "
            f"id={m.get('memory_id','')} 标题={m.get('title','')} 内容={m.get('content','')[:200]}"
        )
    return "\n".join(lines) if lines else "（无相关记忆）"


def format_decision_block(
    decisions: List[Dict],
    limit: int = 5,
) -> str:
    """Format a list of decision dicts into a bulleted text block."""
    lines = []
    for d in decisions[:limit]:
        lines.append(
            f"- {d.get('content','')} (原因: {d.get('reason','')}, 结果: {d.get('outcome','')})"
        )
    return "\n".join(lines) if lines else "（无明显决策历史）"


def persona_to_text(persona) -> Optional[str]:
    """Convert a persona dict/object to a human-readable text block.

    Handles ``None``, ``str``, and ``dict`` inputs.
    """
    if persona is None:
        return None
    if isinstance(persona, str):
        return persona.strip() or None
    if isinstance(persona, dict):
        summary = persona.get("summary") or persona.get("description")
        if summary:
            return str(summary).strip()
        bullets = []
        for k in ("traits", "preferences", "habits", "values"):
            v = persona.get(k)
            if isinstance(v, list):
                for item in v[:5]:
                    bullets.append(f"- {k}: {item}")
        return "\n".join(bullets) if bullets else None
    return None
