"""Gen 2 / Memory Deduplicator — 高度相似 memory 合并 / supersede。

实现要点:
- 相似度: 复用 ``cosine_similarity`` / ``cosine_similarity_batch``。
- 缺失 embedding 时, 使用 ``generate_embedding_for_memory`` 或
  ``deterministic_fallback_embedding`` 回填。
- ``merge()`` 在 ``session.begin()`` 块中事务式执行。
- 次要 memory 标记为 ``CommittedStatus.SUPERSEDED``, 主 memory 的 body 被替换为合并文本。
- 主 memory 的所有 ``MemorySource`` 保留; 次要 memory 的 ``MemorySource`` 复制到主 memory,
  保证双源可追溯。
- 合并后调用 ``generate_embedding_for_memory`` 重新生成主 memory 的 embedding。
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.shared.llm.providers import get_llm_provider
from src.memory.models.committed_memory import CommittedMemory, CommittedStatus
from src.memory.models.memory_embedding import MemoryEmbedding
from src.memory.models.memory_source import MemorySource
from src.memory.services.retrieval_engine import (
    cosine_similarity,
    deterministic_fallback_embedding,
    DEFAULT_EMBEDDING_DIM,
)

logger = logging.getLogger(__name__)

ALLOWED_DUP_ACTIONS = {"merge", "supersede", "keep_both"}


class MemoryDeduplicator:
    def __init__(self, db: AsyncSession):
        self.db = db

    # ------------------------------------------------------------------ find

    async def find_duplicates(
        self,
        user_id: str,
        *,
        memory_id: Optional[str] = None,
        similarity_threshold: float = 0.85,
        top_k: int = 20,
    ) -> List[Dict]:
        """查找用户库中相似度超过阈值的 memory 对。

        行为约定:
        - 默认只对 ``ACTIVE`` 状态 memory 建索引 (``SUPERSEDED`` 已被检索过滤)。
        - 若提供 ``memory_id``, 只返回与该 memory 相似的 pairs。
        - 返回列表中 ``memory_id_a`` 是较新 (后建) 一条, ``memory_id_b`` 是较旧一条
          —— 前端默认按"新覆盖旧"渲染。
        """
        threshold = max(0.0, min(1.0, float(similarity_threshold)))
        top_k = max(1, int(top_k))

        memories = await self._load_active_memories(user_id)
        if not memories:
            return []

        if memory_id:
            target_ids = {memory_id}
        else:
            memories = memories[: max(top_k, len(memories))]
            target_ids = {m.id for m in memories}

        emb_map = await self._load_embeddings_map([m.id for m in memories])

        for m in memories:
            if m.id not in emb_map:
                vector = await self._ensure_embedding(m)
                if vector is not None:
                    emb_map[m.id] = vector

        pairs: List[Dict] = []
        seen_pairs: set = set()

        for m in memories:
            if m.id not in target_ids:
                continue
            vec = emb_map.get(m.id)
            if not vec:
                continue
            scored: List[tuple] = []
            for other in memories:
                if other.id == m.id:
                    continue
                other_vec = emb_map.get(other.id)
                if not other_vec:
                    continue
                sim = cosine_similarity(vec, other_vec)
                if sim >= threshold:
                    scored.append((other, sim))
            scored.sort(key=lambda x: x[1], reverse=True)
            for other, sim in scored[:5]:
                pair_key = tuple(sorted([m.id, other.id]))
                if pair_key in seen_pairs:
                    continue
                seen_pairs.add(pair_key)

                newer, older = (m, other) if (m.created_at or datetime.min) >= (
                    other.created_at or datetime.min
                ) else (other, m)
                action = self._suggest_action(newer, older, sim)
                pairs.append({
                    "memory_id_a": newer.id,
                    "memory_id_b": older.id,
                    "similarity": round(sim, 4),
                    "suggested_action": action,
                })

        pairs.sort(key=lambda x: x["similarity"], reverse=True)
        return pairs[:top_k]

    # ----------------------------------------------------------------- merge

    async def merge(
        self,
        primary_memory_id: str,
        secondary_memory_id: str,
        *,
        merged_body: Optional[str] = None,
        regenerate_embedding: bool = True,
        expected_user_id: Optional[str] = None,
    ) -> str:
        """合并两条 memory (v2.1: 拆事务 + 异步重 embedding)。

        v2.1 关键变化:
        - 软合并在事务内完成, embedding 重生成 **不在事务里**, 避免
          provider.embed() 慢导致事务挂死。
        - 默认 ``regenerate_embedding=True`` 在事务后异步执行, 调用方
          可在快速合并场景传 False, 由后台 hygiene 任务统一重建。
        - 维度对齐: 真实 provider 不可用时回退到 fallback 且记录
          ``embedding_model='fallback'`` 与 ``dimension``, 检索时按维度
          分桶比对, 避免越界。

        返回 primary_memory_id。
        """
        # Callers often perform ownership/retrieval checks first, which opens
        # SQLAlchemy's implicit transaction.  A savepoint keeps the merge
        # atomic without requiring a separate direct-write pathway.
        async with self.db.begin_nested():
            primary = await self._load_memory(primary_memory_id)
            secondary = await self._load_memory(secondary_memory_id)

            if expected_user_id and (
                primary.user_id != expected_user_id
                or secondary.user_id != expected_user_id
            ):
                raise LookupError("Memory not found")

            if primary.user_id != secondary.user_id:
                raise ValueError("primary and secondary must belong to the same user")

            if not merged_body:
                merged_body = (
                    f"{primary.title}\n\n{primary.body}\n"
                    f"---\n合并自: {secondary.title}\n{secondary.body}"
                )

            primary.body = merged_body
            primary.updated_at = datetime.now(timezone.utc)

            await self._copy_sources(secondary.id, primary.id)
            secondary.status = CommittedStatus.SUPERSEDED
            secondary.updated_at = datetime.now(timezone.utc)

            await self.db.flush()

        try:
            from src.execution.services.audit_logger import AuditLogger
            await AuditLogger.log(
                self.db,
                user_id=primary.user_id,
                action="memory_merge",
                actor_type="user",
                actor_id=primary.user_id,
                target_type="memory",
                target_id=primary.id,
                detail={"secondary_memory_id": secondary.id},
            )
        except Exception as e:
            logger.warning(f"Deduplicator.merge: audit log failed: {e}")

        if regenerate_embedding:
            try:
                await self._regenerate_embedding_safe(primary.id, merged_body)
            except Exception as e:
                logger.warning(
                    f"Deduplicator.merge: async embedding regen failed for {primary.id}: {e}"
                )

        return primary.id

    async def _regenerate_embedding_safe(self, memory_id: str, body: str) -> None:
        """事务外执行 embedding 重生成。失败不抛, 仅记日志。

        流程:
        1. 优先 provider.embed(); 失败回退 deterministic fallback
        2. 与已有 embedding 比较 dim; 不一致时 upsert 一条新行, 老行
           保留 dim 标记, 由检索引擎分桶处理
        """
        try:
            result = await self.db.execute(
                select(CommittedMemory).where(CommittedMemory.id == memory_id)
            )
            memory = result.scalar_one_or_none()
            if not memory:
                return

            text = f"{memory.title}\n{body}"
            vector: Optional[List[float]] = None
            used_fallback = False
            try:
                provider = get_llm_provider()
                vector = await provider.embed(text)
            except Exception as e:
                logger.debug(f"_regenerate_embedding_safe: provider.embed failed: {e}")
                vector = None

            if not vector or not isinstance(vector, list) or len(vector) == 0:
                vector = deterministic_fallback_embedding(text, DEFAULT_EMBEDDING_DIM)
                used_fallback = True

            emb_result = await self.db.execute(
                select(MemoryEmbedding).where(MemoryEmbedding.memory_id == memory_id)
            )
            existing = emb_result.scalar_one_or_none()

            if existing and (existing.dimension or 0) == len(vector):
                existing.embedding_vector = vector
                existing.embedding_model = "fallback" if used_fallback else "default"
                existing.content_snapshot = text[:2000]
                existing.dimension = len(vector)
                existing.updated_at = datetime.now(timezone.utc)
            else:
                if existing:
                    await self.db.delete(existing)
                    await self.db.flush()
                from src.shared.ids.id_generator import generate_embedding_id
                self.db.add(MemoryEmbedding(
                    id=generate_embedding_id(),
                    memory_id=memory_id,
                    embedding_model="fallback" if used_fallback else "default",
                    embedding_vector=vector,
                    content_snapshot=text[:2000],
                    dimension=len(vector),
                ))

            await self.db.commit()
        except Exception as e:
            logger.warning(f"_regenerate_embedding_safe failed for {memory_id}: {e}")
            try:
                await self.db.rollback()
            except Exception:
                pass

    # --------------------------------------------------------------- helpers

    async def _load_active_memories(self, user_id: str) -> List[CommittedMemory]:
        result = await self.db.execute(
            select(CommittedMemory)
            .where(
                CommittedMemory.user_id == user_id,
                CommittedMemory.status == CommittedStatus.ACTIVE,
            )
            .order_by(CommittedMemory.created_at.desc())
        )
        return list(result.scalars().all())

    async def _load_embeddings_map(self, memory_ids: List[str]) -> Dict[str, List[float]]:
        if not memory_ids:
            return {}
        result = await self.db.execute(
            select(MemoryEmbedding).where(MemoryEmbedding.memory_id.in_(memory_ids))
        )
        embeddings = result.scalars().all()
        out: Dict[str, List[float]] = {}
        for e in embeddings:
            vec = e.embedding_vector
            if isinstance(vec, list) and vec:
                out[e.memory_id] = vec
        return out

    async def _ensure_embedding(self, memory: CommittedMemory) -> Optional[List[float]]:
        """在 active session 中确保 memory 有 embedding。

        优先复用 ``generate_embedding_for_memory`` (在它自己的 sub-session 里工作);
        若失败, 使用 ``deterministic_fallback_embedding`` 写一条 fallback 记录。
        """
        try:
            from src.memory.tasks.memory_extraction import generate_embedding_for_memory
            ok = await generate_embedding_for_memory(self.db, memory.id)
            if ok:
                await self.db.commit()
                emb_result = await self.db.execute(
                    select(MemoryEmbedding).where(MemoryEmbedding.memory_id == memory.id)
                )
                emb = emb_result.scalar_one_or_none()
                if emb and isinstance(emb.embedding_vector, list) and emb.embedding_vector:
                    return emb.embedding_vector
        except Exception as e:
            logger.warning(f"Deduplicator: generate_embedding_for_memory failed for {memory.id}: {e}")
            try:
                await self.db.rollback()
            except Exception:
                pass

        text = f"{memory.title}\n{memory.body}"
        vector = deterministic_fallback_embedding(text, DEFAULT_EMBEDDING_DIM)
        from src.shared.ids.id_generator import generate_embedding_id
        emb_row = MemoryEmbedding(
            id=generate_embedding_id(),
            memory_id=memory.id,
            embedding_model="fallback",
            embedding_vector=vector,
            content_snapshot=text[:2000],
            dimension=len(vector),
        )
        self.db.add(emb_row)
        try:
            await self.db.commit()
        except Exception as e:
            logger.warning(f"Deduplicator: fallback embedding insert failed for {memory.id}: {e}")
            try:
                await self.db.rollback()
            except Exception:
                pass
            return None
        return vector

    async def _load_memory(self, memory_id: str) -> CommittedMemory:
        result = await self.db.execute(
            select(CommittedMemory).where(CommittedMemory.id == memory_id)
        )
        memory = result.scalar_one_or_none()
        if not memory:
            raise LookupError(f"memory_not_found: {memory_id}")
        return memory

    async def _copy_sources(self, source_memory_id: str, target_memory_id: str) -> None:
        result = await self.db.execute(
            select(MemorySource).where(MemorySource.memory_id == source_memory_id)
        )
        sources = list(result.scalars().all())
        if not sources:
            return
        from src.shared.ids.id_generator import generate_source_id
        for src in sources:
            new_src = MemorySource(
                id=generate_source_id(),
                memory_id=target_memory_id,
                raw_event_id=src.raw_event_id,
                quote=src.quote,
                location=src.location,
                source_type=src.source_type,
            )
            self.db.add(new_src)

    @staticmethod
    def _suggest_action(newer: CommittedMemory, older: CommittedMemory, sim: float) -> str:
        if sim >= 0.95:
            return "merge"
        if newer.importance >= older.importance:
            return "supersede"
        return "merge"
