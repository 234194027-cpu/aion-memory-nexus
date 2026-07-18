"""Conflict Checker v2.1 — 检测新记忆与已有原则/decision/insight 的语义矛盾。

v2.1 升级 (Phase 4 / Sprint 1 稳定性):
- Degrade 噪声治理: LLM 失败时不再批量写入 ConflictRecord, 改为返回
  ``degraded_only=True`` 让上层决定是否提示, 防止 ConflictRecord 表被
  相似但非冲突的记忆污染成"垃圾海"。
- 24h 同对限流: 同一 (user_id, memory_id) 对在 24h 内只产生 1 条 degrade 标记。
- 持久化降级: degraded_only=True 时, 冲突不进 ConflictRecord, 只返回 similar。

设计要点 (源自白皮书第 5 节):
- 复用 ``RetrievalEngine`` 重建 top-k 候选。
- 严格 JSON 输出, ``temperature=0.2`` 保证稳定性。
- 失败/解析错误必须降级, 永远不抛 5xx, 标 ``degraded_only``。
"""
from __future__ import annotations

import json
import uuid
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from src.shared.llm.providers import get_llm_provider
from src.shared.llm.model_gateway import ModelGateway
from src.cognition.models.conflict_record import ConflictRecord
from src.memory.services.retrieval_engine import RetrievalEngine
from src.cognition.prompts.conflict import build_conflict_prompt, VALID_CONFLICT_TYPES, VALID_INTERPRETATIONS

logger = logging.getLogger(__name__)

ALLOWED_SEVERITY = {"high", "medium", "low"}
ALLOWED_RESOLUTION = {"supersede_old", "merge", "keep_both", "needs_user_review"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ConflictChecker:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def check(
        self,
        user_id: str,
        candidate: Dict,
        *,
        recall_level: str = "work_context",
        project_id: Optional[str] = None,
    ) -> Dict:
        """检查新记忆提案是否与已有记忆在语义上矛盾。

        v2.1 行为变化:
        - LLM 不可用 / 输出不可解析时, 返回 ``degraded_only=True``,
          ``conflicts`` 字段为空(仅暴露 similar_memories 给上层参考),
          **不写 ConflictRecord**, 防止噪声污染。
        - 同一 (user_id, memory_id) 对在 24h 内最多产生 1 条冲突记录(去重)。

        Returns
        -------
        dict
            标准结构::
                {
                    "user_id": str,
                    "has_conflict": bool,
                    "conflicts": [...],
                    "similar_memories": [...],
                    "warnings": [...],
                    "degraded_only": bool,
                    "persisted_conflict_ids": [...],
                    "checked_at": iso8601,
                }
        """
        # 兼容结构化提案使用 "content" 和既有调用使用 "body"。
        body = (candidate or {}).get("body") or (candidate or {}).get("content") or ""
        title = (candidate or {}).get("title")
        memory_type = (candidate or {}).get("memory_type")

        warnings: List[str] = []

        if not body.strip():
            return {
                "user_id": user_id,
                "has_conflict": False,
                "conflicts": [],
                "similar_memories": [],
                "warnings": ["empty_candidate"],
                "degraded_only": False,
                "checked_at": _now_iso(),
            }

        engine = RetrievalEngine(self.db)
        question = f"{title}\n{body}".strip() if title else body
        try:
            context = await engine.reconstruct_context(
                user_id=user_id,
                question=question,
                recall_level=recall_level,
                top_k=8,
            )
        except Exception as e:
            logger.warning(f"ConflictChecker: RetrievalEngine failed: {e}")
            return {
                "user_id": user_id,
                "has_conflict": False,
                "conflicts": [],
                "similar_memories": [],
                "warnings": [f"retrieval_failed: {e}"],
                "degraded_only": True,
                "checked_at": _now_iso(),
            }

        relevant = context.get("relevant_memories", []) or []
        if not relevant:
            return {
                "user_id": user_id,
                "has_conflict": False,
                "conflicts": [],
                "similar_memories": [],
                "warnings": [],
                "degraded_only": False,
                "checked_at": _now_iso(),
            }

        candidate_block = (
            f"title: {title}\n" if title else ""
        ) + (
            f"memory_type: {memory_type}\n" if memory_type else ""
        ) + f"body: {body}"

        memories_block = "\n".join(
            [
                f"[{i+1}] id={m.get('memory_id','')} "
                f"type={m.get('memory_type','')} "
                f"importance={m.get('importance', 0.0):.2f} "
                f"similarity={m.get('similarity', 0.0):.3f}\n"
                f"title: {m.get('title','')}\n"
                f"body: {m.get('content','')[:400]}"
                for i, m in enumerate(relevant)
            ]
        )

        prompt = build_conflict_prompt(candidate_block, memories_block)

        try:
            provider = get_llm_provider()
            raw = await ModelGateway(provider).generate_text(
                prompt, temperature=0.2, max_tokens=2000,
                prompt_id="conflict-check", prompt_version="v1",
            )
        except Exception as e:
            logger.warning(f"ConflictChecker: LLM generate failed: {e}")
            return _degrade_to_user_review(user_id, relevant, reason=f"llm_unavailable: {e}")

        parsed = _safe_json_loads(raw)
        if parsed is None:
            warnings.append("llm_output_unparseable")
            return _degrade_to_user_review(user_id, relevant, reason="llm_output_unparseable")

        conflicts_raw = parsed.get("conflicts") or []
        similar_raw = parsed.get("similar_memories") or []

        conflicts = _normalize_conflicts(conflicts_raw, relevant)
        similar = _normalize_similar(similar_raw, relevant)

        # ── 24h 同对去重 ────────────────────────────────────────────
        before = len(conflicts)
        conflicts = await _dedup_within_24h(self.db, user_id, conflicts)
        deduped = before - len(conflicts)
        if deduped:
            warnings.append(f"deduped_24h: {deduped}")

        has_conflict = len(conflicts) > 0

        # ── 持久化 ConflictRecord (三级处理策略) ─────────────────────
        persisted_ids = []
        if has_conflict:
            try:
                persisted_ids = await self._persist_conflicts(
                    user_id=user_id,
                    conflicts=conflicts,
                    candidate_body=body,
                    candidate_title=title,
                    project_id=project_id,
                )
            except Exception as e:
                warnings.append(f"conflict_persist_error: {e}")
                logger.warning(f"ConflictChecker: persist failed: {e}")

        # 给每条冲突附加处理策略说明
        for c in conflicts:
            sev = c.get("severity", "low")
            if sev == "low":
                c["handling_strategy"] = "recorded_silently"
            elif sev == "medium":
                c["handling_strategy"] = "remind_in_advisor"
            else:  # high
                c["handling_strategy"] = "require_human_confirmation"

        return {
            "user_id": user_id,
            "has_conflict": has_conflict,
            "conflicts": conflicts,
            "similar_memories": similar,
            "warnings": warnings,
            "degraded_only": False,
            "persisted_conflict_ids": persisted_ids,
            "checked_at": _now_iso(),
        }

    async def check_for_user(
        self,
        user_id: str,
        *,
        project_id: Optional[str] = None,
        limit: int = 5,
    ) -> List[Dict]:
        """查询用户最近的冲突记录 (供 AdvisorEngine 调用)。"""
        try:
            from sqlalchemy import select

            stmt = (
                select(ConflictRecord)
                .where(ConflictRecord.user_id == user_id)
                .order_by(ConflictRecord.created_at.desc())
                .limit(limit)
            )
            result = await self.db.execute(stmt)
            records = result.scalars().all()

            conflicts = []
            for r in records:
                conflicts.append({
                    "conflict_id": r.id,
                    "conflict_type": r.conflict_type,
                    "interpretation": r.interpretation,
                    "severity": r.severity,
                    "status": r.status,
                    "current_content": r.current_statement,
                    "past_content": r.past_statement,
                    "recommended_action": r.recommended_action,
                    "detected_at": r.created_at.isoformat() if r.created_at else None,
                })
            return conflicts
        except Exception as e:
            logger.warning(f"ConflictChecker.check_for_user: {e}")
            return []

    async def _persist_conflicts(
        self,
        user_id: str,
        conflicts: List[Dict],
        candidate_body: str,
        candidate_title: Optional[str],
        project_id: Optional[str],
    ) -> List[str]:
        """将检测到的冲突写入 ConflictRecord 表。

        三级处理策略:
        - low: 记录 conflict record, 不打断用户
        - medium: 记录, 在 Advisor 回答中提醒
        - high: 记录, 要求人工确认, 不自动更新记忆
        """
        persisted_ids: List[str] = []
        for c in conflicts:
            conflict_id = uuid.uuid4().hex[:16]
            record = ConflictRecord(
                id=conflict_id,
                user_id=user_id,
                conflict_type=c.get("conflict_type", "unknown"),
                interpretation=c.get("interpretation", "unknown"),
                severity=c.get("severity", "low"),
                status="open",
                current_statement=f"{candidate_title or ''}\n{candidate_body}".strip() if candidate_body else "",
                past_statement=c.get("explanation"),
                related_memory_ids=json.dumps([c.get("memory_id")] if c.get("memory_id") else []),
                recommended_action=c.get("suggested_resolution", "review"),
                confidence=0.5,
            )
            self.db.add(record)
            persisted_ids.append(conflict_id)

        await self.db.commit()
        return persisted_ids


def _safe_json_loads(text: str) -> Optional[Dict]:
    """从 LLM 输出中提取 JSON object, 容忍 markdown 包裹。"""
    if not text:
        return None
    text = text.strip()
    try:
        if text.startswith("```"):
            text = text.strip("`")
            if text.startswith("json"):
                text = text[4:]
            start = text.find("{")
            end = text.rfind("}")
            if start != -1 and end != -1 and end > start:
                text = text[start : end + 1]
        return json.loads(text)
    except Exception as e:
        logger.warning(
            "ConflictChecker: JSON parse failed (%s, response_length=%d)",
            type(e).__name__,
            len(text),
        )
        return None


def _id_to_memory_map(relevant: List[Dict]) -> Dict[str, Dict]:
    return {str(m.get("memory_id", "")): m for m in relevant}


def _normalize_conflicts(raw: List[Dict], relevant: List[Dict]) -> List[Dict]:
    id_map = _id_to_memory_map(relevant)
    cleaned: List[Dict] = []
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        mid = str(item.get("memory_id") or "").strip()
        mem = id_map.get(mid) or {}
        severity = str(item.get("severity") or "low").lower().strip()
        if severity not in ALLOWED_SEVERITY:
            severity = "low"
        resolution = str(item.get("suggested_resolution") or "needs_user_review").lower().strip()
        if resolution not in ALLOWED_RESOLUTION:
            resolution = "needs_user_review"

        # v2.0: 解析 conflict_type 和 interpretation
        conflict_type = str(item.get("conflict_type") or "unknown").lower().strip()
        if conflict_type not in VALID_CONFLICT_TYPES:
            conflict_type = "unknown" if "unknown" in VALID_CONFLICT_TYPES else "belief_conflict"
            # fallback to the most generic valid type
            conflict_type = "belief_conflict"

        interpretation = str(item.get("interpretation") or "unknown").lower().strip()
        if interpretation not in VALID_INTERPRETATIONS:
            interpretation = "unknown"

        cleaned.append({
            "memory_id": mid,
            "title": str(item.get("title") or mem.get("title") or ""),
            "memory_type": str(item.get("memory_type") or mem.get("memory_type") or ""),
            "severity": severity,
            "conflict_type": conflict_type,
            "interpretation": interpretation,
            "explanation": str(item.get("explanation") or "").strip(),
            "suggested_resolution": resolution,
        })
    cleaned = [c for c in cleaned if c["memory_id"]]
    return cleaned


def _normalize_similar(raw: List[Dict], relevant: List[Dict]) -> List[Dict]:
    id_map = _id_to_memory_map(relevant)
    cleaned: List[Dict] = []
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        mid = str(item.get("memory_id") or "").strip()
        mem = id_map.get(mid) or {}
        try:
            sim = float(item.get("similarity"))
            if sim < 0.0:
                sim = 0.0
            elif sim > 1.0:
                sim = 1.0
        except (TypeError, ValueError):
            sim = float(mem.get("similarity") or 0.0)
        cleaned.append({
            "memory_id": mid,
            "title": str(item.get("title") or mem.get("title") or ""),
            "similarity": round(sim, 4),
        })
    cleaned = [s for s in cleaned if s["memory_id"]]
    cleaned.sort(key=lambda x: x["similarity"], reverse=True)
    return cleaned[:5]


def _degrade_to_user_review(user_id: str, relevant: List[Dict], reason: str) -> Dict:
    """LLM 不可用 / 输出不可解析时的降级。

    v2.1 关键变化: **不再批量写入 ConflictRecord**, 否则会把所有相似(但
    不一定是冲突)的记忆污染进冲突表, 让 Advisor 长期被噪声淹没。

    行为约定:
    - ``conflicts`` 留空(LLM 没给结果, 我们不能瞎标 conflict)
    - ``degraded_only=True`` 让上游决定是否在前端做"待人工复核"灰显
    - 仅返回 similar_memories 供 UI 参考
    """
    return {
        "user_id": user_id,
        "has_conflict": False,
        "conflicts": [],
        "degraded_only": True,
        "similar_memories": [
            {
                "memory_id": str(m.get("memory_id", "")),
                "title": str(m.get("title", "")),
                "similarity": round(float(m.get("similarity") or 0.0), 4),
            }
            for m in (relevant or [])[:5]
            if m.get("memory_id")
        ],
        "warnings": [reason],
        "persisted_conflict_ids": [],
        "checked_at": _now_iso(),
    }


async def _dedup_within_24h(
    db: AsyncSession,
    user_id: str,
    conflicts: List[Dict],
) -> List[Dict]:
    """24h 内的同 (user_id, related_memory_ids) 只保留 1 条冲突。

    返回过滤后的冲突列表。
    """
    if not conflicts:
        return conflicts
    try:
        from sqlalchemy import select
        from datetime import timedelta

        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        memory_ids = [c.get("memory_id") for c in conflicts if c.get("memory_id")]
        if not memory_ids:
            return conflicts
        # ConflictRecord.related_memory_ids 是 JSON 字符串, 无法直接 IN 查询
        # 改为查最近 24h 所有该用户的 conflict, 再在 Python 中匹配
        stmt = select(ConflictRecord).where(
            ConflictRecord.user_id == user_id,
            ConflictRecord.created_at >= cutoff,
        )
        rows = await db.execute(stmt)
        seen_pairs: set = set()
        for record in rows.scalars().all():
            try:
                related = json.loads(record.related_memory_ids or "[]")
                for mid in related:
                    seen_pairs.add(str(mid))
            except Exception:
                continue
        return [c for c in conflicts if c.get("memory_id") not in seen_pairs]
    except Exception as e:
        logger.warning(f"_dedup_within_24h failed: {e}")
        return conflicts
