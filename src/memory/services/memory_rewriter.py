"""Gen 2 / Memory Rewriter — 周期性整理零碎 memory。

设计:
- ``rewrite()`` 默认 mode = "proposal", **不直接写库**。
- 取用户最近的约 100 条 ACTIVE 正式记忆。
- 调用 LLM (temperature=0.3) 严格 JSON 输出 proposals。
- 配套 ``apply_proposals()`` 在人工确认后真正落库。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.shared.llm.providers import get_llm_provider
from src.shared.llm.model_gateway import ModelGateway
from src.memory.models.memory_type import MemoryType
from src.memory.models.committed_memory import CommittedMemory, CommittedStatus
from src.execution.models.memory_relation import MemoryRelation
from src.memory.prompts.rewriter import build_rewrite_prompt
from src.shared.ids.id_generator import generate_memory_relation_id

logger = logging.getLogger(__name__)

ALLOWED_PROPOSAL_ACTIONS = {"merge", "rewrite", "archive", "link"}

VALID_RELATION_TYPES = {
    "supports", "contradicts", "supersedes", "duplicates",
    "updates", "explains", "belongs_to", "caused_by", "resulted_in",
}


class MemoryRewriter:
    def __init__(self, db: AsyncSession):
        self.db = db

    # --------------------------------------------------------------- rewrite

    async def rewrite(
        self,
        user_id: str,
        *,
        target_types: Optional[List[str]] = None,
        max_clusters: int = 20,
    ) -> Dict:
        """生成 rewrite proposals (不写库)。

        流程:
        1. 拉取用户最近约 100 条 ACTIVE 正式记忆。
        2. 按 ``max_clusters`` 大小分块喂给 LLM。
        3. 解析 LLM JSON 输出, 规范化 proposals。
        4. 返回结构, ``applied=False`` (默认 proposal mode)。
        """
        max_clusters = max(1, min(200, int(max_clusters)))
        warnings: List[str] = []

        memories = await self._load_recent_memories(user_id, target_types, limit=100)
        if not memories:
            return {
                "user_id": user_id,
                "rewritten_count": 0,
                "merges_proposed": 0,
                "proposals": [],
                "applied": False,
                "generated_at": _now_iso(),
                "warnings": [],
            }

        mem_chunks = [memories[i : i + max_clusters] for i in range(0, len(memories), max_clusters)] or [[]]
        all_proposals: List[Dict] = []
        seen_ids: set = set()
        merges_proposed = 0

        for chunk in mem_chunks:
            chunk_proposals = await self._propose_for_chunk(chunk)
            for p in chunk_proposals:
                key = _proposal_key(p)
                if key in seen_ids:
                    continue
                seen_ids.add(key)
                all_proposals.append(p)
                if p.get("action") == "merge":
                    merges_proposed += 1

        return {
            "user_id": user_id,
            "rewritten_count": len(all_proposals),
            "merges_proposed": merges_proposed,
            "proposals": all_proposals,
            "applied": False,
            "generated_at": _now_iso(),
            "warnings": warnings,
        }

    # --------------------------------------------------------- apply_proposals

    async def apply_proposals(self, user_id: str, proposals: List[Dict]) -> Dict:
        """把审核过的 proposals 真正落到库中。

        支持 actions:
        - ``merge``: 复用 ``MemoryDeduplicator.merge()`` (取第一条为 primary, 其余为 secondary)
          并在 memory_relations 表中创建 duplicates 类型的 relation
        - ``rewrite``: 仅刷新 memory 的 body / title, status 保持 ACTIVE
        - ``archive``: 把 memory status 改成 ``SUPERSEDED``，并创建 supersedes relation
        - ``link``: 在 memory_relations 表中创建指定类型的 relation（不修改 memory 本身）
        """
        from src.memory.services.deduplicator import MemoryDeduplicator

        applied_count = 0
        failed: List[Dict] = []
        dedup = MemoryDeduplicator(self.db)

        for raw in proposals or []:
            action = "unknown"
            try:
                action = str(raw.get("action") or "").lower().strip()
                if action not in ALLOWED_PROPOSAL_ACTIONS:
                    failed.append({"reason": f"invalid_action: {action}", "proposal": raw})
                    continue

                if action == "merge":
                    ids = raw.get("memory_ids") or []
                    if not isinstance(ids, list) or len(ids) < 2:
                        failed.append({"reason": "merge_requires_at_least_2_ids", "proposal": raw})
                        continue
                    primary_id = str(ids[0])
                    secondary_ids = [str(i) for i in ids[1:]]
                    merged_body = raw.get("merged_draft")
                    merged_secondary_ids: List[str] = []
                    for sid in secondary_ids:
                        try:
                            await dedup.merge(
                                primary_id,
                                sid,
                                merged_body=merged_body if merged_body else None,
                                expected_user_id=user_id,
                            )
                            merged_secondary_ids.append(sid)
                        except LookupError as le:
                            failed.append({"reason": str(le), "proposal": raw})
                        except ValueError as ve:
                            failed.append({"reason": str(ve), "proposal": raw})
                    # 创建 duplicates relation
                    for sid in merged_secondary_ids:
                        await self._create_relation(
                            user_id=user_id,
                            source_memory_id=primary_id,
                            target_memory_id=sid,
                            relation_type="duplicates",
                            reason=raw.get("reason", "merge_proposal"),
                        )
                    if merged_secondary_ids:
                        applied_count += 1

                elif action == "rewrite":
                    mid = raw.get("memory_id")
                    draft = raw.get("draft_body")
                    if not mid or not isinstance(draft, str):
                        failed.append({"reason": "rewrite_requires_memory_id_and_draft_body", "proposal": raw})
                        continue
                    result = await self.db.execute(
                        select(CommittedMemory).where(
                            CommittedMemory.id == mid,
                            CommittedMemory.user_id == user_id,
                        )
                    )
                    mem = result.scalar_one_or_none()
                    if not mem:
                        failed.append({"reason": f"memory_not_found: {mid}", "proposal": raw})
                        continue
                    mem.body = draft
                    mem.updated_at = datetime.now(timezone.utc)
                    await self.db.commit()
                    applied_count += 1

                elif action == "archive":
                    mid = raw.get("memory_id")
                    if not mid:
                        failed.append({"reason": "archive_requires_memory_id", "proposal": raw})
                        continue
                    result = await self.db.execute(
                        select(CommittedMemory).where(
                            CommittedMemory.id == mid,
                            CommittedMemory.user_id == user_id,
                        )
                    )
                    mem = result.scalar_one_or_none()
                    if not mem:
                        failed.append({"reason": f"memory_not_found: {mid}", "proposal": raw})
                        continue
                    mem.status = CommittedStatus.SUPERSEDED
                    mem.updated_at = datetime.now(timezone.utc)
                    await self.db.commit()
                    # 创建 supersedes relation（如果有 related memory_ids）
                    related_ids = raw.get("memory_ids") or []
                    for rid in related_ids:
                        if str(rid) != str(mid):
                            await self._create_relation(
                                user_id=user_id,
                                source_memory_id=str(rid),
                                target_memory_id=str(mid),
                                relation_type="supersedes",
                                reason=raw.get("reason", "archive_proposal"),
                            )
                    applied_count += 1

                elif action == "link":
                    ids = raw.get("memory_ids") or []
                    if not isinstance(ids, list) or len(ids) != 2:
                        failed.append({"reason": "link_requires_exactly_2_memory_ids", "proposal": raw})
                        continue
                    relation_type = str(raw.get("relation_type") or "").strip()
                    if relation_type not in VALID_RELATION_TYPES:
                        failed.append({"reason": f"invalid_relation_type: {relation_type}", "proposal": raw})
                        continue
                    created = await self._create_relation(
                        user_id=user_id,
                        source_memory_id=str(ids[0]),
                        target_memory_id=str(ids[1]),
                        relation_type=relation_type,
                        reason=raw.get("reason", ""),
                    )
                    if created:
                        applied_count += 1
                    else:
                        failed.append({"reason": "relation_memories_not_owned", "proposal": raw})

            except Exception as exc:
                logger.warning(
                    "MemoryRewriter.apply_proposals failed action=%s error_type=%s",
                    action,
                    type(exc).__name__,
                )
                try:
                    await self.db.rollback()
                except Exception:
                    pass
                failed.append({"reason": str(exc), "proposal": raw})

        return {
            "user_id": user_id,
            "applied_count": applied_count,
            "failed": failed,
            "applied_at": _now_iso(),
        }

    async def _create_relation(
        self,
        *,
        user_id: str,
        source_memory_id: str,
        target_memory_id: str,
        relation_type: str,
        reason: str = "",
        confidence: float = 0.5,
    ) -> bool:
        """在 memory_relations 表中创建一条 relation 记录。"""
        try:
            if source_memory_id == target_memory_id:
                return False
            result = await self.db.execute(
                select(CommittedMemory.id).where(
                    CommittedMemory.user_id == user_id,
                    CommittedMemory.id.in_([source_memory_id, target_memory_id]),
                )
            )
            if len(set(result.scalars().all())) != 2:
                return False
            rel_id = generate_memory_relation_id()
            relation = MemoryRelation(
                id=rel_id,
                user_id=user_id,
                source_memory_id=source_memory_id,
                target_memory_id=target_memory_id,
                relation_type=relation_type,
                reason=reason,
                confidence=confidence,
            )
            self.db.add(relation)
            await self.db.commit()
            return True
        except Exception as exc:
            logger.warning("Failed to create memory relation: %s", exc)
            try:
                await self.db.rollback()
            except Exception:
                pass
            return False

    # -------------------------------------------------------------- internals

    async def _load_recent_memories(
        self,
        user_id: str,
        target_types: Optional[List[str]],
        *,
        limit: int = 100,
    ) -> List[CommittedMemory]:
        query = (
            select(CommittedMemory)
            .where(
                CommittedMemory.user_id == user_id,
                CommittedMemory.status == CommittedStatus.ACTIVE,
            )
            .order_by(CommittedMemory.created_at.desc())
            .limit(limit)
        )

        enums: List[MemoryType] = []
        for t in target_types or []:
            try:
                enums.append(MemoryType(t))
            except ValueError:
                continue
        if enums:
            query = query.where(CommittedMemory.memory_type.in_(enums))

        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def _propose_for_chunk(
        self,
        memories: List[CommittedMemory],
    ) -> List[Dict]:
        if not memories:
            return []

        mem_block = "\n".join(
            [
                f"[M{i+1}] id={m.id} type={m.memory_type.value} "
                f"importance={m.importance:.2f} created={_iso(m.created_at)}\n"
                f"  title: {m.title}\n  body: {m.body[:300]}"
                for i, m in enumerate(memories)
            ]
        ) or "(no committed memories in this chunk)"

        prompt = build_rewrite_prompt(mem_block)
        try:
            provider = get_llm_provider()
            raw = await ModelGateway(provider).generate_text(
                prompt, temperature=0.3, max_tokens=4000,
                prompt_id="memory-rewrite", prompt_version="v1",
            )
        except Exception as e:
            logger.warning(f"MemoryRewriter: LLM generate failed: {e}")
            return []

        parsed = _safe_json_loads(raw)
        if not parsed:
            return []

        items = parsed.get("proposals") or []
        return _normalize_proposals(items, memories)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _iso(dt: Optional[datetime]) -> str:
    if not dt:
        return ""
    try:
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc).isoformat()
        return dt.isoformat()
    except Exception:
        return ""


def _safe_json_loads(text: str) -> Optional[Dict]:
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
            "MemoryRewriter: JSON parse failed (%s, response_length=%d)",
            type(e).__name__,
            len(text),
        )
        return None


def _proposal_key(p: Dict) -> str:
    action = p.get("action") or ""
    if p.get("memory_ids"):
        ids = sorted(p.get("memory_ids") or [])
        return f"{action}:merge:{','.join(ids)}"
    mid = p.get("memory_id") or ""
    return f"{action}:{mid}"


def _normalize_proposals(
    items: List[Dict],
    memories: List[CommittedMemory],
) -> List[Dict]:
    mem_ids = {m.id for m in memories}
    out: List[Dict] = []
    for raw in items or []:
        if not isinstance(raw, dict):
            continue
        action = str(raw.get("action") or "").lower().strip()
        if action not in ALLOWED_PROPOSAL_ACTIONS:
            continue
        reason = str(raw.get("reason") or "").strip() or "no_reason_provided"
        proposal: Dict = {"action": action, "reason": reason}

        if action == "merge":
            ids = raw.get("memory_ids") or []
            if not isinstance(ids, list):
                continue
            real_ids: List[str] = []
            for x in ids:
                sx = str(x)
                if sx in mem_ids:
                    real_ids.append(sx)
            if len(real_ids) < 2:
                continue
            proposal["memory_ids"] = real_ids
            merged = raw.get("merged_draft")
            proposal["merged_draft"] = str(merged) if isinstance(merged, str) else None
        elif action == "rewrite":
            mid = str(raw.get("memory_id") or "")
            if mid not in mem_ids:
                continue
            draft = raw.get("draft_body")
            proposal["memory_id"] = mid
            proposal["draft_body"] = str(draft) if isinstance(draft, str) else None
        elif action == "archive":
            mid = str(raw.get("memory_id") or "")
            if mid not in mem_ids:
                continue
            proposal["memory_id"] = mid
        elif action == "link":
            ids = raw.get("memory_ids") or []
            if not isinstance(ids, list) or len(ids) != 2:
                continue
            real_ids = []
            for x in ids:
                sx = str(x)
                if sx in mem_ids:
                    real_ids.append(sx)
            if len(real_ids) != 2:
                continue
            relation_type = str(raw.get("relation_type") or "").strip()
            if relation_type not in VALID_RELATION_TYPES:
                continue
            proposal["memory_ids"] = real_ids
            proposal["relation_type"] = relation_type

        if action == "merge":
            proposal.setdefault("memory_id", None)
            proposal.setdefault("draft_body", None)
            proposal.setdefault("relation_type", None)
        elif action == "rewrite":
            proposal.setdefault("memory_ids", None)
            proposal.setdefault("merged_draft", None)
            proposal.setdefault("relation_type", None)
        elif action == "archive":
            proposal.setdefault("memory_ids", None)
            proposal.setdefault("merged_draft", None)
            proposal.setdefault("draft_body", None)
            proposal.setdefault("relation_type", None)
        elif action == "link":
            proposal.setdefault("memory_id", None)
            proposal.setdefault("draft_body", None)
            proposal.setdefault("merged_draft", None)

        out.append(proposal)

    return out[:20]
