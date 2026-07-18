"""Memory Hygiene v2.1 — 周期性去重 + 冲突巡检。

Phase 4 / Sprint 1 核心组件, 解决以下问题:
- Dedup 只在候选提交时触发, 老库永远不会被扫, 数月后会有大量
  重复记忆未合并。
- ConflictRecord 表会随时间增长, 缺一个"清理已解决 / 降级过期"的策略。

本任务只做"建议", 绝不自动 merge / delete:
- 输出 ``hygiene_suggestions`` 列表, 写 WeeklyReview 备查。
- 调用方(daily briefing / weekly review)决定是否提示用户。
"""
from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.memory.models.committed_memory import CommittedMemory, CommittedStatus
from src.cognition.models.conflict_record import ConflictRecord
from src.memory.services.deduplicator import MemoryDeduplicator
from src.memory.services.memory_os import (
    build_layer_summary,
    build_memory_evolution,
    build_memory_uri,
    memory_layer_for_type,
)

logger = logging.getLogger(__name__)

DEDUP_THRESHOLD = 0.9
DEDUP_IMPORTANCE_FLOOR = 0.4
CONFLICT_STALE_DAYS = 30
CONFLICT_MAX_PER_USER = 50
HYGIENE_MEMORY_SCAN_LIMIT = 200
LOW_CONFIDENCE_THRESHOLD = 0.6
PROMOTION_TAG_MIN_COUNT = 3
COMPACTION_BODY_MIN_CHARS = 1200
SUPPORTED_HYGIENE_APPLY_TYPES = {
    "merge_duplicate_memories",
    "expire_or_rewrite_outdated_memory",
}


async def run_nightly_hygiene(
    db: AsyncSession,
    user_id: str,
    *,
    dedup_threshold: float = DEDUP_THRESHOLD,
    importance_floor: float = DEDUP_IMPORTANCE_FLOOR,
    max_pairs_per_user: int = 20,
) -> Dict:
    """对单用户跑一次 hygiene。

    Returns
    -------
    dict
        {
            "user_id": str,
            "duplicate_pairs": [...],     # 建议合并的对
            "stale_conflicts": [...],     # 30 天前未处理的冲突
            "ran_at": iso8601,
            "stats": {...},
        }
    """
    stats: Dict[str, int] = {
        "duplicate_pairs_found": 0,
        "stale_conflicts_found": 0,
        "active_memories_scanned": 0,
        "evolution_suggestions_found": 0,
    }
    duplicate_pairs: List[Dict] = []
    stale_conflicts: List[Dict] = []
    memory_evolution: Dict[str, Any] = _empty_hygiene_evolution()

    try:
        dedup = MemoryDeduplicator(db)
        pairs = await dedup.find_duplicates(
            user_id=user_id,
            similarity_threshold=dedup_threshold,
            top_k=max_pairs_per_user,
        )
        active_ids = await _load_active_importance_ids(db, user_id, importance_floor)
        for p in pairs:
            if p["memory_id_a"] in active_ids or p["memory_id_b"] in active_ids:
                duplicate_pairs.append(p)
        stats["duplicate_pairs_found"] = len(duplicate_pairs)
    except Exception as e:
        logger.warning(f"hygiene dedup failed for {user_id}: {e}")

    try:
        cutoff = datetime.now(timezone.utc) - timedelta(days=CONFLICT_STALE_DAYS)
        stmt = (
            select(ConflictRecord)
            .where(
                ConflictRecord.user_id == user_id,
                ConflictRecord.status == "open",
                ConflictRecord.created_at <= cutoff,
            )
            .order_by(ConflictRecord.created_at.asc())
            .limit(CONFLICT_MAX_PER_USER)
        )
        rows = (await db.execute(stmt)).scalars().all()
        import json as _json
        for r in rows:
            related_ids = []
            if r.related_memory_ids:
                try:
                    related_ids = _json.loads(r.related_memory_ids) or []
                except Exception:
                    related_ids = []
            stale_conflicts.append({
                "conflict_id": r.id,
                "related_memory_ids": related_ids,
                "severity": r.severity,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            })
        stats["stale_conflicts_found"] = len(stale_conflicts)
    except Exception as e:
        logger.warning(f"hygiene stale conflict scan failed for {user_id}: {e}")

    try:
        active_memories = await _load_recent_active_memories(
            db,
            user_id,
            limit=HYGIENE_MEMORY_SCAN_LIMIT,
        )
        stats["active_memories_scanned"] = len(active_memories)
        memory_evolution = build_hygiene_evolution_report(active_memories)
    except Exception as e:
        logger.warning(f"hygiene memory evolution scan failed for {user_id}: {e}")

    hygiene_suggestions = _build_hygiene_suggestions(
        duplicate_pairs=duplicate_pairs,
        stale_conflicts=stale_conflicts,
        memory_evolution=memory_evolution,
    )
    stats["evolution_suggestions_found"] = len(hygiene_suggestions)

    return {
        "user_id": user_id,
        "duplicate_pairs": duplicate_pairs,
        "stale_conflicts": stale_conflicts,
        "memory_evolution": memory_evolution,
        "hygiene_suggestions": hygiene_suggestions,
        "ran_at": datetime.now(timezone.utc).isoformat(),
        "stats": stats,
    }


async def _load_active_importance_ids(
    db: AsyncSession,
    user_id: str,
    importance_floor: float,
) -> set:
    """加载满足 importance 阈值的 active memory id 集合。"""
    try:
        stmt = select(CommittedMemory.id).where(
            CommittedMemory.user_id == user_id,
            CommittedMemory.status == CommittedStatus.ACTIVE,
            CommittedMemory.importance >= importance_floor,
        )
        rows = await db.execute(stmt)
        return {r[0] for r in rows.all()}
    except Exception as e:
        logger.warning(f"_load_active_importance_ids failed: {e}")
        return set()


async def _load_recent_active_memories(
    db: AsyncSession,
    user_id: str,
    *,
    limit: int,
) -> List[CommittedMemory]:
    stmt = (
        select(CommittedMemory)
        .where(
            CommittedMemory.user_id == user_id,
            CommittedMemory.status == CommittedStatus.ACTIVE,
        )
        .order_by(CommittedMemory.created_at.desc())
        .limit(limit)
    )
    rows = await db.execute(stmt)
    return list(rows.scalars().all())


def build_hygiene_evolution_report(memories: List[Any]) -> Dict[str, Any]:
    now = datetime.now(timezone.utc)
    low_confidence = []
    expired = []
    validity_review = []
    compaction_candidates = []
    repeated_tags = Counter()

    for memory in memories:
        confidence = float(getattr(memory, "confidence", 0.0) or 0.0)
        if getattr(memory, "id", "") and confidence < LOW_CONFIDENCE_THRESHOLD:
            low_confidence.append(_memory_ref(memory, reason="confidence_below_threshold"))

        valid_until = _aware(getattr(memory, "valid_until", None))
        if valid_until is not None:
            if valid_until <= now:
                expired.append(_memory_ref(memory, reason="valid_until_passed"))
            else:
                validity_review.append(_memory_ref(memory, reason="has_validity_window"))

        body = str(getattr(memory, "body", "") or "")
        if len(body) >= COMPACTION_BODY_MIN_CHARS:
            compaction_candidates.append({
                **_memory_ref(memory, reason="large_body_candidate_for_summary"),
                "body_chars": len(body),
            })

        for tag in getattr(memory, "tags", None) or []:
            repeated_tags[str(tag)] += 1

    promotion_candidates = [
        {
            "tag": tag,
            "count": count,
            "suggested_action": "promote_repeated_episode_or_project_pattern",
        }
        for tag, count in repeated_tags.most_common(20)
        if count >= PROMOTION_TAG_MIN_COUNT
    ]

    layer_summary = build_layer_summary(memories)
    base_evolution = build_memory_evolution(memories)
    candidate_actions = set(base_evolution.get("candidate_actions") or [])
    if expired:
        candidate_actions.add("expire_or_rewrite_outdated_memories")
    if validity_review:
        candidate_actions.add("review_validity_windows")
    if compaction_candidates:
        candidate_actions.add("compact_large_memories")
    if promotion_candidates:
        candidate_actions.add("promote_repeated_tags")

    return {
        **base_evolution,
        "state_operator": "nightly_hygiene",
        "candidate_actions": sorted(candidate_actions),
        "layer_summary": layer_summary,
        "low_confidence": low_confidence[:20],
        "expired": expired[:20],
        "validity_review": validity_review[:20],
        "promotion_candidates": promotion_candidates[:20],
        "compaction_candidates": compaction_candidates[:20],
        "policy": "Suggestion-only daily evolution. No merge, archive, delete, or rewrite is applied by this task.",
    }


def _build_hygiene_suggestions(
    *,
    duplicate_pairs: List[Dict],
    stale_conflicts: List[Dict],
    memory_evolution: Dict[str, Any],
) -> List[Dict[str, Any]]:
    suggestions: List[Dict[str, Any]] = []

    for pair in duplicate_pairs:
        suggestions.append({
            "type": "merge_duplicate_memories",
            "priority": "high",
            "memory_ids": [pair.get("memory_id_a"), pair.get("memory_id_b")],
            "reason": "duplicate_pair_above_threshold",
            "proposal": pair,
            "auto_apply": False,
        })

    for conflict in stale_conflicts:
        suggestions.append({
            "type": "review_stale_conflict",
            "priority": "medium",
            "conflict_id": conflict.get("conflict_id"),
            "memory_ids": conflict.get("related_memory_ids") or [],
            "reason": "open_conflict_older_than_threshold",
            "proposal": conflict,
            "auto_apply": False,
        })

    for ref in memory_evolution.get("expired", []):
        suggestions.append({
            "type": "expire_or_rewrite_outdated_memory",
            "priority": "medium",
            "memory_ids": [ref.get("memory_id")],
            "reason": ref.get("reason"),
            "proposal": ref,
            "auto_apply": False,
        })

    for ref in memory_evolution.get("low_confidence", []):
        suggestions.append({
            "type": "review_low_confidence_memory",
            "priority": "low",
            "memory_ids": [ref.get("memory_id")],
            "reason": ref.get("reason"),
            "proposal": ref,
            "auto_apply": False,
        })

    for candidate in memory_evolution.get("promotion_candidates", []):
        suggestions.append({
            "type": "promote_repeated_pattern",
            "priority": "low",
            "tag": candidate.get("tag"),
            "reason": "tag_repeated_across_memories",
            "proposal": candidate,
            "auto_apply": False,
        })

    for ref in memory_evolution.get("compaction_candidates", []):
        suggestions.append({
            "type": "compact_large_memory",
            "priority": "low",
            "memory_ids": [ref.get("memory_id")],
            "reason": ref.get("reason"),
            "proposal": ref,
            "auto_apply": False,
        })

    return suggestions


def hygiene_suggestions_to_rewrite_proposals(
    suggestions: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Convert approved hygiene suggestions into existing rewriter proposals.

    This function is intentionally conservative. It never applies anything by
    itself and only maps suggestion types whose write semantics are explicit.
    """
    proposals: List[Dict[str, Any]] = []
    unsupported: List[Dict[str, Any]] = []

    for suggestion in suggestions or []:
        suggestion_type = str(suggestion.get("type") or "").strip()
        memory_ids = [
            str(memory_id)
            for memory_id in suggestion.get("memory_ids") or []
            if memory_id
        ]

        if suggestion_type == "merge_duplicate_memories":
            if len(memory_ids) < 2:
                unsupported.append({
                    "type": suggestion_type,
                    "reason": "merge_duplicate_memories_requires_at_least_2_memory_ids",
                    "suggestion": suggestion,
                })
                continue
            proposals.append({
                "action": "merge",
                "memory_ids": memory_ids,
                "reason": suggestion.get("reason") or "hygiene_duplicate_merge",
                "merged_draft": None,
            })
            continue

        if suggestion_type == "expire_or_rewrite_outdated_memory":
            if len(memory_ids) != 1:
                unsupported.append({
                    "type": suggestion_type,
                    "reason": "expire_or_rewrite_outdated_memory_requires_exactly_1_memory_id",
                    "suggestion": suggestion,
                })
                continue
            proposals.append({
                "action": "archive",
                "memory_id": memory_ids[0],
                "memory_ids": memory_ids,
                "reason": suggestion.get("reason") or "hygiene_expired_memory_archive",
            })
            continue

        unsupported.append({
            "type": suggestion_type,
            "reason": "unsupported_hygiene_suggestion_type",
            "suggestion": suggestion,
        })

    return {
        "proposals": proposals,
        "unsupported": unsupported,
    }


def _empty_hygiene_evolution() -> Dict[str, Any]:
    return build_hygiene_evolution_report([])


def _memory_ref(memory: Any, *, reason: str) -> Dict[str, Any]:
    return {
        "memory_id": getattr(memory, "id", ""),
        "memory_uri": build_memory_uri(memory),
        "title": str(getattr(memory, "title", "") or "")[:120],
        "memory_layer": memory_layer_for_type(getattr(memory, "memory_type", None)),
        "importance": round(float(getattr(memory, "importance", 0.0) or 0.0), 4),
        "confidence": round(float(getattr(memory, "confidence", 0.0) or 0.0), 4),
        "valid_from": _iso(getattr(memory, "valid_from", None)),
        "valid_until": _iso(getattr(memory, "valid_until", None)),
        "reason": reason,
    }


def _aware(value: Any) -> Optional[datetime]:
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _iso(value: Any) -> Optional[str]:
    if isinstance(value, datetime):
        return value.isoformat()
    return None
