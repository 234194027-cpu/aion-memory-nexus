from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import Any

from src.memory.models.memory_type import MemoryType


MEMORY_LAYER_DESCRIPTIONS = {
    "working": "Current task state and near-term TODOs.",
    "episodic": "Time-stamped events, conversations, outcomes, and provenance.",
    "semantic": "Stable facts, project context, user model, and domain knowledge.",
    "procedural": "Preferences, decisions, principles, corrections, and reusable work patterns.",
}


MEMORY_LAYER_BY_TYPE = {
    MemoryType.TASK: "working",
    MemoryType.TIMELINE_EVENT: "episodic",
    MemoryType.FACT: "semantic",
    MemoryType.PROJECT_CONTEXT: "semantic",
    MemoryType.INSIGHT: "semantic",
    MemoryType.PERSONA_HYPOTHESIS: "semantic",
    MemoryType.DECISION: "procedural",
    MemoryType.PREFERENCE: "procedural",
    MemoryType.PRINCIPLE: "procedural",
    MemoryType.CORRECTION: "procedural",
}


CONTEXT_TIER_DESCRIPTIONS = {
    "L0": "Small routing summary for prompt headers and quick decisions.",
    "L1": "Layered working set for normal agent context injection.",
    "L2": "Full memory references for deep recall, audit, and follow-up reads.",
}

L0_CHAR_BUDGET = 600
L1_LAYER_CHAR_BUDGET = 900
L2_ITEM_CHAR_BUDGET = 700


def memory_layer_for_type(memory_type: MemoryType | str | None) -> str:
    if memory_type is None:
        return "semantic"
    if isinstance(memory_type, str):
        try:
            memory_type = MemoryType(memory_type)
        except ValueError:
            return "semantic"
    return MEMORY_LAYER_BY_TYPE.get(memory_type, "semantic")


def build_memory_uri(memory: Any) -> str:
    layer = memory_layer_for_type(getattr(memory, "memory_type", None))
    project_id = getattr(memory, "project_id", None) or "global"
    memory_id = getattr(memory, "id", "") or "unknown"
    return f"life://memory/{layer}/{project_id}/{memory_id}"


def build_context_path(memory: Any) -> str:
    layer = memory_layer_for_type(getattr(memory, "memory_type", None))
    project_id = _slug(getattr(memory, "project_id", None) or "global")
    memory_type = getattr(memory, "memory_type", None)
    memory_type_value = memory_type.value if hasattr(memory_type, "value") else str(memory_type or "unknown")
    memory_id = _slug(getattr(memory, "id", "") or "unknown")
    return f"/context/{project_id}/{layer}/{memory_type_value}/{memory_id}"


def layer_policy() -> dict[str, Any]:
    return {
        "layers": [
            {
                "name": name,
                "description": description,
                "agent_use": _agent_use_for_layer(name),
            }
            for name, description in MEMORY_LAYER_DESCRIPTIONS.items()
        ],
        "lifecycle": [
            "before_start: search or reconstruct context before substantial work",
            "during_task: keep relevant recalled memories in working context",
            "after_end: write decisions, actions, outcomes, and corrections as RawEvent",
            "daily_delta: upload changed external memories with stable external_id values",
            "hygiene: dedupe, compress, promote repeated episodic facts into semantic/procedural memory, and retire stale records",
        ],
        "context_tiers": [
            {"name": name, "description": description}
            for name, description in CONTEXT_TIER_DESCRIPTIONS.items()
        ],
    }


def _agent_use_for_layer(layer: str) -> str:
    if layer == "working":
        return "Use for immediate task state; expire or rewrite quickly."
    if layer == "episodic":
        return "Use for evidence, timelines, and source-grounded recall."
    if layer == "semantic":
        return "Use as stable project/user/domain context."
    if layer == "procedural":
        return "Use as operating rules, preferences, and proven work patterns."
    return "Use as general context."


def build_layer_summary(memories: list[Any]) -> dict[str, Any]:
    counts = Counter()
    top_ids: dict[str, list[str]] = {}
    for memory in memories:
        layer = memory_layer_for_type(getattr(memory, "memory_type", None))
        counts[layer] += 1
        top_ids.setdefault(layer, [])
        if len(top_ids[layer]) < 5:
            top_ids[layer].append(getattr(memory, "id", ""))

    return {
        "counts": {name: counts.get(name, 0) for name in MEMORY_LAYER_DESCRIPTIONS},
        "top_memory_ids": top_ids,
        "policy": layer_policy(),
    }


def build_context_tiers(memories: list[Any]) -> dict[str, Any]:
    compressed = build_compressed_context(memories)
    buckets: dict[str, list[dict[str, Any]]] = {
        name: [] for name in MEMORY_LAYER_DESCRIPTIONS
    }
    for memory in memories[:20]:
        layer = memory_layer_for_type(getattr(memory, "memory_type", None))
        buckets.setdefault(layer, []).append({
            "memory_id": getattr(memory, "id", ""),
            "memory_uri": build_memory_uri(memory),
            "context_path": build_context_path(memory),
            "title": _short(getattr(memory, "title", ""), 90),
            "summary": _short(_memory_line(memory), 180),
            "importance": round(float(getattr(memory, "importance", 0.0) or 0.0), 4),
            "confidence": round(float(getattr(memory, "confidence", 0.0) or 0.0), 4),
        })

    l0_parts = []
    for layer, items in buckets.items():
        if items:
            l0_parts.append(f"{layer}:{len(items)}")

    return {
        "L0": {
            "summary": ", ".join(l0_parts) if l0_parts else "no recalled memories",
            "compressed_text": compressed["L0"]["compressed_text"],
            "memory_count": len(memories[:20]),
        },
        "L1": {
            "layered_working_set": buckets,
            "layer_summaries": compressed["L1"]["layer_summaries"],
            "selection_rule": "Use procedural first for constraints, semantic for stable context, episodic for evidence, working for immediate tasks.",
        },
        "L2": {
            "memory_refs": [
                {
                    "memory_id": getattr(memory, "id", ""),
                    "memory_uri": build_memory_uri(memory),
                    "context_path": build_context_path(memory),
                    "content": _short(_memory_text(memory), L2_ITEM_CHAR_BUDGET),
                    "valid_from": _iso(getattr(memory, "valid_from", None)),
                    "valid_until": _iso(getattr(memory, "valid_until", None)),
                    "scope": {
                        "project_id": getattr(memory, "project_id", None),
                        "repo_id": getattr(memory, "repo_id", None),
                        "workspace_id": getattr(memory, "workspace_id", None),
                    },
                }
                for memory in memories[:20]
            ],
            "selection_rule": "Use L2 when the agent needs provenance, exact validity windows, or relation follow-up.",
        },
    }


def build_compressed_context(memories: list[Any]) -> dict[str, Any]:
    selected = memories[:20]
    layer_groups: dict[str, list[Any]] = {name: [] for name in MEMORY_LAYER_DESCRIPTIONS}
    for memory in selected:
        layer = memory_layer_for_type(getattr(memory, "memory_type", None))
        layer_groups.setdefault(layer, []).append(memory)

    top_lines = [
        _memory_line(memory)
        for memory in sorted(
            selected,
            key=lambda m: (
                float(getattr(m, "importance", 0.0) or 0.0),
                float(getattr(m, "confidence", 0.0) or 0.0),
            ),
            reverse=True,
        )[:5]
    ]
    l0 = _short(" | ".join(top_lines), L0_CHAR_BUDGET) if top_lines else "No recalled memory content."

    layer_summaries = {}
    for layer, items in layer_groups.items():
        if not items:
            layer_summaries[layer] = {
                "count": 0,
                "compressed_text": "",
                "top_paths": [],
            }
            continue
        lines = [_memory_line(item) for item in items[:6]]
        layer_summaries[layer] = {
            "count": len(items),
            "compressed_text": _short(" ".join(lines), L1_LAYER_CHAR_BUDGET),
            "top_paths": [build_context_path(item) for item in items[:6]],
        }

    return {
        "L0": {
            "purpose": "prompt_header",
            "budget_chars": L0_CHAR_BUDGET,
            "compressed_text": l0,
        },
        "L1": {
            "purpose": "normal_context",
            "budget_chars_per_layer": L1_LAYER_CHAR_BUDGET,
            "layer_summaries": layer_summaries,
        },
        "L2": {
            "purpose": "deep_recall",
            "budget_chars_per_memory": L2_ITEM_CHAR_BUDGET,
            "refs": [
                {
                    "memory_id": getattr(memory, "id", ""),
                    "memory_uri": build_memory_uri(memory),
                    "context_path": build_context_path(memory),
                    "content": _short(_memory_text(memory), L2_ITEM_CHAR_BUDGET),
                }
                for memory in selected
            ],
        },
    }


def build_context_tree(memories: list[Any]) -> dict[str, Any]:
    root = {
        "name": "context",
        "path": "/context",
        "kind": "directory",
        "children": [],
    }
    children_by_path: dict[str, dict[str, Any]] = {"/context": root}
    index = []

    for memory in memories[:50]:
        project_id = _slug(getattr(memory, "project_id", None) or "global")
        layer = memory_layer_for_type(getattr(memory, "memory_type", None))
        memory_type = getattr(memory, "memory_type", None)
        memory_type_value = memory_type.value if hasattr(memory_type, "value") else str(memory_type or "unknown")
        memory_id = _slug(getattr(memory, "id", "") or "unknown")
        parts = [project_id, layer, memory_type_value]
        parent = root
        current_path = "/context"
        for part in parts:
            current_path = f"{current_path}/{part}"
            node = children_by_path.get(current_path)
            if node is None:
                node = {
                    "name": part,
                    "path": current_path,
                    "kind": "directory",
                    "children": [],
                    "memory_count": 0,
                }
                children_by_path[current_path] = node
                parent["children"].append(node)
            node["memory_count"] = int(node.get("memory_count") or 0) + 1
            parent = node

        leaf_path = f"{current_path}/{memory_id}"
        leaf = {
            "name": memory_id,
            "path": leaf_path,
            "kind": "memory",
            "memory_id": getattr(memory, "id", ""),
            "memory_uri": build_memory_uri(memory),
            "title": _short(getattr(memory, "title", ""), 90),
            "summary": _short(_memory_line(memory), 180),
        }
        parent["children"].append(leaf)
        index.append({
            "memory_id": leaf["memory_id"],
            "path": leaf_path,
            "memory_uri": leaf["memory_uri"],
            "layer": layer,
            "project_id": project_id,
            "memory_type": memory_type_value,
        })

    return {
        "root": root,
        "index": index,
        "recursive_retrieval": {
            "entrypoint": "/context",
            "path_semantics": "/context/{project_id}/{layer}/{memory_type}/{memory_id}",
            "recommended_order": [
                "Read /context/{project_id}/procedural first for operating rules.",
                "Read /context/{project_id}/semantic for stable facts.",
                "Read /context/{project_id}/episodic only when evidence or timeline matters.",
                "Open a leaf memory URI only when L2 detail is required.",
            ],
        },
    }


def build_relation_graph(memories: list[Any], relations: list[Any] | None = None) -> dict[str, Any]:
    memory_by_id = {getattr(memory, "id", ""): memory for memory in memories if getattr(memory, "id", "")}
    nodes = [
        {
            "memory_id": memory_id,
            "memory_uri": build_memory_uri(memory),
            "context_path": build_context_path(memory),
            "layer": memory_layer_for_type(getattr(memory, "memory_type", None)),
            "title": _short(getattr(memory, "title", ""), 90),
        }
        for memory_id, memory in memory_by_id.items()
    ]

    edges = []
    relation_counts = Counter()
    for relation in relations or []:
        source_id = getattr(relation, "source_memory_id", "")
        target_id = getattr(relation, "target_memory_id", "")
        if source_id not in memory_by_id and target_id not in memory_by_id:
            continue
        relation_type = str(getattr(relation, "relation_type", "") or "related")
        relation_counts[relation_type] += 1
        edges.append({
            "relation_id": getattr(relation, "id", ""),
            "source_memory_id": source_id,
            "target_memory_id": target_id,
            "relation_type": relation_type,
            "reason": _short(getattr(relation, "reason", "") or "", 160),
            "confidence": round(float(getattr(relation, "confidence", 0.0) or 0.0), 4),
            "created_at": _iso(getattr(relation, "created_at", None)),
        })

    return {
        "nodes": nodes,
        "edges": edges[:30],
        "relation_counts": dict(relation_counts),
        "summary": _relation_summary(len(nodes), edges, relation_counts),
    }


def build_memory_evolution(memories: list[Any]) -> dict[str, Any]:
    low_confidence = []
    stale_or_expired = []
    repeated_tags = Counter()
    layer_counts = Counter()

    for memory in memories[:50]:
        memory_id = getattr(memory, "id", "")
        layer = memory_layer_for_type(getattr(memory, "memory_type", None))
        layer_counts[layer] += 1

        confidence = float(getattr(memory, "confidence", 0.0) or 0.0)
        if confidence < 0.6:
            low_confidence.append(memory_id)

        valid_until = getattr(memory, "valid_until", None)
        if valid_until is not None:
            stale_or_expired.append(memory_id)

        for tag in getattr(memory, "tags", None) or []:
            repeated_tags[str(tag)] += 1

    promoted_tag_candidates = [
        tag for tag, count in repeated_tags.most_common(10) if count >= 2
    ]

    actions = []
    if low_confidence:
        actions.append("review_low_confidence")
    if stale_or_expired:
        actions.append("check_validity_windows")
    if promoted_tag_candidates:
        actions.append("promote_repeated_episode_tags")
    if layer_counts.get("procedural", 0) == 0 and memories:
        actions.append("extract_procedural_rules_if_any")

    return {
        "state_operator": "retrieve",
        "candidate_actions": actions,
        "low_confidence_memory_ids": low_confidence[:10],
        "validity_review_memory_ids": stale_or_expired[:10],
        "promotion_tag_candidates": promoted_tag_candidates,
        "layer_counts": {name: layer_counts.get(name, 0) for name in MEMORY_LAYER_DESCRIPTIONS},
        "policy": "Evolve memory by ingestion, revision, forgetting, and retrieval instead of treating records as static notes.",
    }


def build_retrieval_trace_entry(
    *,
    memory: Any,
    rank: int,
    similarity: float,
    final_score: float,
    embed_method: str,
    recall_level: str,
) -> dict[str, Any]:
    layer = memory_layer_for_type(getattr(memory, "memory_type", None))
    memory_type = getattr(memory, "memory_type", None)
    memory_type_value = memory_type.value if hasattr(memory_type, "value") else str(memory_type)
    tags = getattr(memory, "tags", None) or []
    return {
        "rank": rank,
        "memory_id": getattr(memory, "id", ""),
        "memory_uri": build_memory_uri(memory),
        "context_path": build_context_path(memory),
        "layer": layer,
        "memory_type": memory_type_value,
        "matched_by": embed_method,
        "recall_level": recall_level,
        "reason": _trace_reason(layer, memory_type_value, similarity, tags),
        "score": {
            "similarity": round(float(similarity or 0.0), 4),
            "final": round(float(final_score or 0.0), 4),
            "importance": round(float(getattr(memory, "importance", 0.0) or 0.0), 4),
            "confidence": round(float(getattr(memory, "confidence", 0.0) or 0.0), 4),
        },
        "scope": {
            "project_id": getattr(memory, "project_id", None),
            "repo_id": getattr(memory, "repo_id", None),
            "workspace_id": getattr(memory, "workspace_id", None),
        },
        "created_at": _iso(getattr(memory, "created_at", None)),
        "tags": tags,
    }


def _trace_reason(layer: str, memory_type: str, similarity: float, tags: list[Any]) -> str:
    parts = [f"{memory_type} memory in {layer} layer"]
    if similarity > 0:
        parts.append("matched query content")
    if tags:
        parts.append(f"tags={','.join(str(tag) for tag in tags[:3])}")
    return "; ".join(parts)


def build_agent_memory_protocol(task: str, recall_level: str) -> dict[str, Any]:
    return {
        "task": task,
        "recall_level": recall_level,
        "required_steps": [
            "Read context_pack.summary, context_tiers, memory_layers, relation_graph, and retrieval_trace before acting.",
            "Use L0 compressed_text as prompt header, L1 layer_summaries as the normal context, and L2 refs only for deep recall.",
            "Use context_tree paths to recursively narrow from project to layer to memory type before opening exact memories.",
            "Use procedural memories as operating constraints unless they conflict with newer user instructions.",
            "Use semantic memories as stable context and episodic memories as evidence.",
            "When relation_graph has edges, prefer connected evidence over isolated similarity hits.",
            "At task end, call memory_after_end with summary, decisions, actions, and artifacts.",
            "For external memory stores, call memory_upload_daily_delta with stable external_id values.",
        ],
        "write_back_shape": {
            "summary": "What was done and what changed.",
            "decisions": "Durable choices, rules, or preferences learned.",
            "actions": "Commands, checks, and operational steps completed.",
            "artifacts": "Files, URLs, ids, reports, or deploy outputs created.",
        },
    }


def _iso(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat()
    return None


def _short(value: Any, limit: int) -> str:
    text = str(value or "")
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)] + "..."


def _memory_text(memory: Any) -> str:
    title = str(getattr(memory, "title", "") or "").strip()
    body = str(getattr(memory, "body", "") or "").strip()
    if title and body:
        return f"{title}: {body}"
    return title or body


def _memory_line(memory: Any) -> str:
    layer = memory_layer_for_type(getattr(memory, "memory_type", None))
    memory_type = getattr(memory, "memory_type", None)
    memory_type_value = memory_type.value if hasattr(memory_type, "value") else str(memory_type or "unknown")
    importance = round(float(getattr(memory, "importance", 0.0) or 0.0), 2)
    text = _short(_memory_text(memory), 220)
    return f"[{layer}/{memory_type_value}/imp={importance}] {text}"


def _slug(value: Any) -> str:
    text = str(value or "unknown").strip()
    if not text:
        return "unknown"
    safe = []
    for ch in text:
        if ch.isalnum() or ch in {"_", "-", "."}:
            safe.append(ch)
        else:
            safe.append("_")
    return "".join(safe)[:120] or "unknown"


def _relation_summary(node_count: int, edges: list[dict[str, Any]], relation_counts: Counter) -> str:
    if not edges:
        return f"{node_count} recalled memory nodes; no explicit relations found."
    parts = [f"{rel_type}:{count}" for rel_type, count in relation_counts.most_common(5)]
    return f"{node_count} recalled memory nodes; {len(edges)} relation edges ({', '.join(parts)})."
