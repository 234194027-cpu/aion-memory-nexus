import asyncio
import hashlib
import math
import re
import logging
from time import perf_counter
from typing import List, Dict, Optional, Tuple
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_
from datetime import datetime, timezone

from src.memory.models.committed_memory import CommittedMemory, CommittedStatus
from src.memory.models.memory_embedding import MemoryEmbedding
from src.memory.models.memory_type import MemoryType
from src.memory.models.graph_projection import GraphShadowObservation
from src.execution.models.memory_relation import MemoryRelation
from src.shared.config import settings
from src.shared.llm.providers import get_llm_provider
from src.shared.ids.id_generator import generate_id
from src.memory.prompts.retrieval import build_retrieval_prompt
from src.memory.services.memory_os import (
    build_context_tiers,
    build_context_path,
    build_context_tree,
    build_layer_summary,
    build_memory_evolution,
    build_memory_uri,
    build_relation_graph,
    build_retrieval_trace_entry,
    memory_layer_for_type,
)
from src.memory.services.governance_policy import (
    RECALL_LEVEL_FILTER,
    SENSITIVITY_BY_RECALL,
    VISIBILITY_BY_RECALL,
    normalize_recall_level,
)

logger = logging.getLogger(__name__)

# ── 检索模式配置 (参考 Graphiti 三信号并行融合) ─────────────────────────────
# HYBRID_SEARCH_MODE:
#   'fallback'  : 串行降级, 向量失败才用 BM25
#   'parallel'  (默认): 向量 + BM25 + 时序衰减并行计算, 加权融合
# 第三轮迭代默认开启 parallel 模式，提升检索质量.
HYBRID_SEARCH_MODE = settings.HYBRID_SEARCH_MODE.lower().strip()

# 三信号融合权重 (parallel 模式下生效)
# final_score = vector_w * vector_sim + bm25_w * bm25_score + recency_w * recency
HYBRID_WEIGHT_VECTOR = settings.HYBRID_WEIGHT_VECTOR
HYBRID_WEIGHT_BM25 = settings.HYBRID_WEIGHT_BM25
HYBRID_WEIGHT_RECENCY = settings.HYBRID_WEIGHT_RECENCY


DECISION_PRIORITY = {
    MemoryType.DECISION: 1.0,
    MemoryType.INSIGHT: 0.8,
    MemoryType.FACT: 0.6,
    MemoryType.PROJECT_CONTEXT: 0.5,
    MemoryType.PRINCIPLE: 0.7,
    MemoryType.PREFERENCE: 0.7,
    MemoryType.CORRECTION: 0.9,
    MemoryType.TIMELINE_EVENT: 0.4,
    MemoryType.PERSONA_HYPOTHESIS: 0.6,
    MemoryType.TASK: 0.2,
}

CLUSTER_CONTEXT_LIMIT = 4
CLUSTER_PATTERNS_LIMIT = 5
CLUSTER_CONFLICTS_LIMIT = 3

DEFAULT_EMBEDDING_DIM = 1024

_CN_CHAR_RE = re.compile(r"[\u4e00-\u9fff]")


def format_retrieval_memory_context(memory: CommittedMemory, index: int) -> str:
    """Format provenance and temporal facts alongside LLM-visible memory text."""
    valid_from = memory.valid_from.isoformat() if memory.valid_from else "unknown"
    valid_until = memory.valid_until.isoformat() if memory.valid_until else "ongoing"
    return (
        f"[id={index}] (type={memory.memory_type.value}, importance={memory.importance:.2f}, "
        f"epistemic_status={memory.epistemic_status}, valid_from={valid_from}, "
        f"valid_until={valid_until}) {memory.title}: {memory.body[:200]}"
    )


def _is_cjk_char(ch: str) -> bool:
    return bool(_CN_CHAR_RE.match(ch))


def _tokenize_for_search(text: str) -> List[str]:
    """v2.1 检索分词: 中英混合友好。

    - CJK: 1-gram + 2-gram 滑窗, 解决中文用空格分词退化问题
    - ASCII: 用单词边界切分, 过滤长度 <= 1 的 token
    - 全部转小写, 便于后续 BM25 / 命中比较
    """
    if not text:
        return []
    lowered = text.lower()
    tokens: List[str] = []

    cjk_chars = [ch for ch in lowered if _is_cjk_char(ch)]
    for i, ch in enumerate(cjk_chars):
        tokens.append(ch)
        if i + 1 < len(cjk_chars):
            tokens.append(ch + cjk_chars[i + 1])

    for m in re.finditer(r"[a-z0-9]+", lowered):
        tok = m.group(0)
        if len(tok) > 1:
            tokens.append(tok)

    return tokens


def cosine_similarity(a: List[float], b: List[float]) -> float:
    if not a or not b:
        return 0.0
    if len(a) != len(b):
        m = min(len(a), len(b))
        a = a[:m]
        b = b[:m]
    dot = 0.0
    sum_a = 0.0
    sum_b = 0.0
    for x, y in zip(a, b):
        dot += x * y
        sum_a += x * x
        sum_b += y * y
    if sum_a == 0.0 or sum_b == 0.0:
        return 0.0
    return dot / (math.sqrt(sum_a) * math.sqrt(sum_b))


def cosine_similarity_batch(query: List[float], vectors: List[List[float]]) -> List[float]:
    if not query or not vectors:
        return [0.0] * len(vectors)
    n = min(len(query), len(vectors[0]) if vectors else 0)
    if n == 0:
        return [0.0] * len(vectors)

    q = query[:n]
    q_norm_sq = sum(x * x for x in q)
    if q_norm_sq == 0:
        return [0.0] * len(vectors)
    q_norm = math.sqrt(q_norm_sq)

    results = []
    for v in vectors:
        if not v:
            results.append(0.0)
            continue
        m = min(n, len(v))
        if m == 0:
            results.append(0.0)
            continue
        dot = 0.0
        v_norm_sq = 0.0
        for i in range(m):
            dot += q[i] * v[i]
            v_norm_sq += v[i] * v[i]
        if v_norm_sq == 0.0:
            results.append(0.0)
        else:
            results.append(dot / (q_norm * math.sqrt(v_norm_sq)))
    return results


def deterministic_fallback_embedding(text: str, dim: int = DEFAULT_EMBEDDING_DIM) -> List[float]:
    import hashlib
    vec = [0.0] * dim
    for i, ch in enumerate(text):
        h = hashlib.md5(f"{ch}_{i}".encode()).digest()
        for j in range(4):
            idx = (h[j] + i) % dim
            vec[idx] += (h[j + 4] / 255.0) - 0.5
    norm = math.sqrt(sum(x * x for x in vec))
    if norm > 0:
        vec = [x / norm for x in vec]
    return vec


class RetrievalEngine:

    def __init__(self, db: AsyncSession):
        self.db = db

    async def reconstruct_context(
        self,
        user_id: str,
        question: str,
        project_id: Optional[str] = None,
        recall_level: str = "work_context",
        top_k: int = 20,
    ) -> Dict:
        recall_level = normalize_recall_level(recall_level)
        query_vector = None
        embed_method = "keyword"
        try:
            provider = get_llm_provider()
            query_vector = await provider.embed(question)
            if query_vector and len(query_vector) > 0:
                embed_method = "semantic"
            else:
                query_vector = None
        except Exception:
            query_vector = None

        # 根据 HYBRID_SEARCH_MODE 选择检索策略
        # - 'parallel': 三信号并行融合 (向量 + BM25 + 时序衰减), 参考 Graphiti
        # - 'fallback' (默认): 串行 fallback, 向量失败才用 BM25 (保留原有行为)
        if HYBRID_SEARCH_MODE == "parallel":
            candidates, scores = await self._hybrid_search(
                query_vector,
                question,
                user_id,
                project_id,
                recall_level,
                top_k * 2,
            )
            # parallel 模式下 embed_method 标记为 hybrid
            if embed_method == "semantic":
                embed_method = "hybrid"
            else:
                embed_method = "hybrid_keyword"
        elif embed_method == "semantic" and query_vector:
            candidates, scores = await self._vector_search(
                query_vector, user_id, project_id, recall_level, top_k * 2
            )
            # 如果向量搜索返回空，回退到关键词搜索
            if not candidates:
                candidates, scores = await self._keyword_search(
                    question, user_id, project_id, recall_level, top_k * 2
                )
                embed_method = "keyword_fallback"
        else:
            candidates, scores = await self._keyword_search(
                question, user_id, project_id, recall_level, top_k * 2
            )

        if not candidates:
            return self._empty_context(question, embed_method, recall_level)

        prioritized, p_scores, final_scores = self._prioritize_by_type(candidates, scores)

        # Reranker 精排 (参考 mem0): 在 _prioritize_by_type 后用 LLM 对 top 候选重新打分.
        # 受 ENABLE_RERANKER 开关控制 (默认关闭, 因为增加延迟). 失败时 fallback 到原顺序.
        prioritized = await self._maybe_rerank(question, prioritized, p_scores, final_scores)

        clusters = await self._llm_cluster(question, prioritized[:10])
        relations = await self._load_relations(user_id, prioritized[:20])

        context = self._build_output(
            question,
            prioritized,
            p_scores,
            final_scores,
            clusters,
            embed_method,
            recall_level,
            relations,
        )
        from src.memory.services.graph_projection import retrieve_verified_graph_context

        graph_started = perf_counter()
        graph_context = await retrieve_verified_graph_context(
            self.db,
            user_id=user_id,
            question=question,
            project_id=project_id,
            recall_level=recall_level,
        )
        graph_latency_ms = max(0, round((perf_counter() - graph_started) * 1000))
        context["graph_context"] = graph_context
        # V2.5.1 is deliberately shadow-only: Graphiti output is measured but
        # can never alter baseline ranking or the L1/L2 context supplied to an
        # Agent. Promotion requires a future explicit architecture release.
        context["meta"]["graph_mode"] = "shadow" if settings.GRAPHITI_ENABLED else "disabled"
        if settings.GRAPHITI_ENABLED and settings.GRAPHITI_SHADOW_MODE:
            baseline_ids = [item.id for item in prioritized[:top_k]]
            graph_ids = [str(item) for item in graph_context.get("source_memory_ids", [])]
            relations = graph_context.get("relations", [])
            source_refs = sum(len(item.get("sources") or []) for item in relations if isinstance(item, dict))
            coverage = 1.0 if not relations else min(1.0, source_refs / len(relations))
            self.db.add(GraphShadowObservation(
                id=generate_id("gso"),
                user_id=user_id,
                query_hash=hashlib.sha256(question.encode("utf-8")).hexdigest(),
                baseline_memory_ids=baseline_ids,
                graph_memory_ids=graph_ids,
                graph_relation_count=len(relations),
                novel_verified_count=len(set(graph_ids).difference(baseline_ids)),
                source_coverage=coverage,
                graph_latency_ms=graph_latency_ms,
                token_used=0,
                mode="shadow",
            ))
            await self.db.flush()

        return context

    async def _maybe_rerank(
        self,
        question: str,
        memories: List[CommittedMemory],
        p_scores: List[float],
        final_scores: List[float],
    ) -> List[CommittedMemory]:
        """可选的 LLM reranker 精排 (参考 mem0).

        如果 ENABLE_RERANKER=false (默认), 直接返回原顺序.
        如果开启, 把 CommittedMemory 转成 dict 喂给 reranker, 然后按 reranker 返回顺序
        重排 memories (同时同步 p_scores / final_scores 的顺序, 通过返回新 list 实现).

        注意: reranker 接收 List[dict], 返回 List[dict]; 这里只用来重排顺序,
        不替换 memories 的内容, 因此 p_scores / final_scores 列表的顺序也需要同步调整.
        由于 p_scores / final_scores 是 list 而非可变引用, 这里只能返回重排后的 memories;
        scores 的顺序由 _build_output 内部用 enumerate 重新读取, 因此只要 memories 顺序对了,
        scores 顺序在 _build_output 中会按新顺序取值 (scores[i] 仍对应 memories[i]).
        """
        # 延迟导入避免循环依赖和模块加载时副作用
        from src.memory.services.reranker import ENABLE_RERANKER, get_reranker

        if not ENABLE_RERANKER or not memories or len(memories) <= 1:
            return memories

        try:
            # 取 top 候选喂给 reranker (避免 prompt 过长)
            rerank_input_size = min(len(memories), 20)
            top_memories = memories[:rerank_input_size]
            rest_memories = memories[rerank_input_size:]

            # 转成 dict 列表 (reranker 接口要求)
            mem_dicts = [
                {
                    "memory_id": m.id,
                    "title": m.title,
                    "content": m.body,
                    "memory_type": m.memory_type.value,
                    "epistemic_status": m.epistemic_status,
                    "importance": float(m.importance or 0.0),
                }
                for m in top_memories
            ]

            reranker = get_reranker()
            reranked_dicts = await reranker.rerank(
                question, mem_dicts, top_k=rerank_input_size
            )

            if not reranked_dicts:
                return memories

            # 按 reranker 返回的 memory_id 顺序重排 top_memories
            id_to_memory = {m.id: m for m in top_memories}
            reranked_memories: List[CommittedMemory] = []
            for d in reranked_dicts:
                mid = d.get("memory_id")
                m = id_to_memory.get(mid)
                if m is not None:
                    reranked_memories.append(m)
            # 补全 reranker 可能漏掉的 (理论上不会, 但防御性处理)
            seen_ids = {m.id for m in reranked_memories}
            for m in top_memories:
                if m.id not in seen_ids:
                    reranked_memories.append(m)

            # 拼接剩余部分 (未被 reranker 处理的)
            return reranked_memories + rest_memories
        except Exception as e:
            logger.warning("rerank failed, keep original order: %s", e)
            return memories

    def _build_filter(
        self,
        user_id: str,
        project_id: Optional[str],
        recall_level: str,
    ):
        recall_level = normalize_recall_level(recall_level)
        conditions = [
            CommittedMemory.user_id == user_id,
            CommittedMemory.status == CommittedStatus.ACTIVE,
            or_(
                CommittedMemory.valid_until.is_(None),
                CommittedMemory.valid_until > datetime.now(timezone.utc),
            ),
        ]

        allowed_types = RECALL_LEVEL_FILTER.get(recall_level)
        if allowed_types is not None:
            conditions.append(CommittedMemory.memory_type.in_(allowed_types))

        allowed_sens = SENSITIVITY_BY_RECALL.get(recall_level)
        if allowed_sens is not None:
            conditions.append(CommittedMemory.sensitivity.in_(allowed_sens))

        allowed_visibility = VISIBILITY_BY_RECALL[recall_level]
        conditions.append(CommittedMemory.visibility_scope.in_(allowed_visibility))

        if project_id:
            conditions.append(CommittedMemory.project_id == project_id)

        return and_(*conditions)

    async def _vector_search(
        self,
        query_vector: List[float],
        user_id: str,
        project_id: Optional[str],
        recall_level: str,
        limit: int,
    ) -> Tuple[List[CommittedMemory], List[float]]:
        """向量检索：优先使用 Zvec（如果配置启用且可用），否则 fallback 到数据库查询。

        Zvec 返回 memory_id + similarity score，然后从数据库读取 CommittedMemory
        并应用过滤约束（user_id、status、project_id、recall_level、sensitivity、memory_type）。

        项目约束：Embedding storage must use independent 'memory_embeddings' table.
        """
        zvec_result = await self._zvec_vector_search(
            query_vector, user_id, project_id, recall_level, limit
        )
        if zvec_result[0]:
            return zvec_result

        return await self._fallback_vector_search(
            query_vector, user_id, project_id, recall_level, limit
        )

    async def _zvec_vector_search(
        self,
        query_vector: List[float],
        user_id: str,
        project_id: Optional[str],
        recall_level: str,
        limit: int,
    ) -> Tuple[List[CommittedMemory], List[float]]:
        """使用 Zvec 进行向量检索，返回经过数据库过滤后的结果。"""
        try:
            from src.memory.services.vector_index_backend import get_vector_index_backend

            backend = get_vector_index_backend()
            if not backend.is_available():
                return [], []

            multiplier = settings.ZVEC_QUERY_CANDIDATE_MULTIPLIER
            candidate_limit = limit * multiplier

            results = backend.query(query_vector, candidate_limit)
            if not results:
                return [], []

            memory_ids = [mid for mid, _ in results]
            if not memory_ids:
                return [], []

            id_to_score = dict(results)
            filter_cond = self._build_filter(user_id, project_id, recall_level)

            stmt = (
                select(CommittedMemory)
                .where(CommittedMemory.id.in_(memory_ids))
                .where(filter_cond)
            )
            result = await self.db.execute(stmt)
            memories = list(result.scalars().all())

            if not memories:
                return [], []

            memories_with_scores = [
                (m, id_to_score.get(str(m.id), 0.0)) for m in memories
            ]
            memories_with_scores.sort(key=lambda x: x[1], reverse=True)
            memories_with_scores = memories_with_scores[:limit]

            return [m for m, _ in memories_with_scores], [s for _, s in memories_with_scores]
        except Exception as e:
            logger.warning(f"Zvec vector search failed, falling back: {e}")
            return [], []

    async def _fallback_vector_search(
        self,
        query_vector: List[float],
        user_id: str,
        project_id: Optional[str],
        recall_level: str,
        limit: int,
    ) -> Tuple[List[CommittedMemory], List[float]]:
        """Fallback 向量检索：直接基于 MemoryEmbedding 表查询。"""
        filter_cond = self._build_filter(user_id, project_id, recall_level)

        stmt = (
            select(MemoryEmbedding, CommittedMemory)
            .join(CommittedMemory, MemoryEmbedding.memory_id == CommittedMemory.id)
            .where(filter_cond)
        )
        result = await self.db.execute(stmt)
        rows = result.all()

        if not rows:
            return [], []

        filtered_memories: List[CommittedMemory] = []
        scores: List[float] = []
        for emb_record, memory in rows:
            emb = getattr(emb_record, "embedding_vector", None)
            declared_dim = getattr(emb_record, "dimension", None)
            if not emb:
                continue
            if declared_dim is not None and declared_dim != len(query_vector):
                continue
            if len(emb) != len(query_vector):
                continue
            filtered_memories.append(memory)
            scores.append(cosine_similarity(query_vector, emb))

        if not filtered_memories:
            return [], []

        paired = sorted(zip(filtered_memories, scores), key=lambda x: x[1], reverse=True)
        paired = paired[:limit]

        return [m for m, _ in paired], [s for _, s in paired]

    async def _keyword_search(
        self,
        question: str,
        user_id: str,
        project_id: Optional[str],
        recall_level: str,
        limit: int,
    ) -> Tuple[List[CommittedMemory], List[float]]:
        """v2.1: 改用 _tokenize_for_search, 支持中文。

        评分: BM25 简化版 (饱和项 + 长度归一化), 避免纯 hit/total 让长文档天然高分。
        如果 BM25 搜索返回空结果，回退到简单的文本匹配搜索。
        """
        keywords = _tokenize_for_search(question or "")
        if not keywords:
            return [], []

        filter_cond = self._build_filter(user_id, project_id, recall_level)
        result = await self.db.execute(
            select(CommittedMemory).where(filter_cond)
        )
        memories = result.scalars().all()
        if not memories:
            return [], []

        k1 = 1.5
        b = 0.75
        docs_tokens: List[List[str]] = []
        doc_lens: List[int] = []
        for m in memories:
            tokens = _tokenize_for_search(f"{m.title} {m.body}")
            docs_tokens.append(tokens)
            doc_lens.append(len(tokens))
        avg_len = (sum(doc_lens) / len(doc_lens)) if doc_lens else 1.0

        df: Dict[str, int] = {}
        for tokens in docs_tokens:
            for t in set(tokens):
                df[t] = df.get(t, 0) + 1
        N = max(len(memories), 1)

        scored: List[Tuple[CommittedMemory, float]] = []
        for m, tokens, dl in zip(memories, docs_tokens, doc_lens):
            if not tokens:
                scored.append((m, 0.0))
                continue
            tf: Dict[str, int] = {}
            for t in tokens:
                tf[t] = tf.get(t, 0) + 1
            score = 0.0
            for kw in keywords:
                f = tf.get(kw, 0)
                if f == 0:
                    continue
                idf = math.log(1 + (N - df.get(kw, 0) + 0.5) / (df.get(kw, 0) + 0.5))
                norm = (f * (k1 + 1)) / (f + k1 * (1 - b + b * dl / max(avg_len, 1.0)))
                score += idf * norm
            score = score / max(len(keywords), 1)
            scored.append((m, score))

        scored.sort(key=lambda x: (x[1], x[0].importance), reverse=True)
        top = scored[:limit]
        
        # 如果 BM25 搜索返回空结果或所有分数为 0，回退到简单文本匹配
        if not top or all(s == 0.0 for _, s in top):
            return await self._simple_text_search(question, user_id, project_id, recall_level, limit)
        
        return [m for m, _ in top], [s for _, s in top]
    
    async def _simple_text_search(
        self,
        question: str,
        user_id: str,
        project_id: Optional[str],
        recall_level: str,
        limit: int,
    ) -> Tuple[List[CommittedMemory], List[float]]:
        """简单文本匹配回退：使用 SQL LIKE 进行模糊匹配。
        
        当 BM25 关键词搜索失败时，使用更宽松的文本匹配确保能找到相关记忆。
        """
        if not question:
            return [], []
        
        filter_cond = self._build_filter(user_id, project_id, recall_level)
        
        # 提取问题中的关键词（简单分词）
        question_lower = question.lower()
        words = re.findall(r'[\u4e00-\u9fff]+|[a-zA-Z]{2,}', question_lower)
        
        if not words:
            return [], []
        
        # 构建 OR 条件：只要标题或内容包含任一关键词即可
        from sqlalchemy import or_
        text_conditions = []
        for word in words[:5]:  # 限制关键词数量避免查询过慢
            text_conditions.append(CommittedMemory.title.ilike(f'%{word}%'))
            text_conditions.append(CommittedMemory.body.ilike(f'%{word}%'))
        
        if text_conditions:
            combined_filter = and_(filter_cond, or_(*text_conditions))
            result = await self.db.execute(
                select(CommittedMemory).where(combined_filter).limit(limit * 2)
            )
            memories = result.scalars().all()
            
            if memories:
                # 简单评分：匹配的关键词越多分数越高
                scored = []
                for m in memories:
                    text = f"{m.title} {m.body}".lower()
                    match_count = sum(1 for w in words if w in text)
                    score = match_count / len(words) if words else 0.0
                    scored.append((m, score))
                
                scored.sort(key=lambda x: (x[1], x[0].importance), reverse=True)
                top = scored[:limit]
                return [m for m, _ in top], [s for _, s in top]
        
        # No lexical evidence is not permission to disclose unrelated memories.
        # Let the caller return its established empty-context/abstention result.
        return [], []

    async def _hybrid_search(
        self,
        query_vector: Optional[List[float]],
        question: str,
        user_id: str,
        project_id: Optional[str],
        recall_level: str,
        limit: int,
    ) -> Tuple[List[CommittedMemory], List[float]]:
        """三信号并行融合检索 (参考 Graphiti).

        并行执行三种检索信号:
            1. 向量语义检索 (cosine similarity)
            2. BM25 关键词检索
            3. 时序衰减加权 (importance * recency_decay)

        融合公式:
            final_score = HYBRID_WEIGHT_VECTOR * vector_sim_norm
                        + HYBRID_WEIGHT_BM25 * bm25_score_norm
                        + HYBRID_WEIGHT_RECENCY * recency_norm
            (默认 0.6 / 0.3 / 0.1)

        去重: 同一 memory 出现在多个信号中时, 各信号贡献各自的归一化分数,
              按融合公式累加 (即一个 memory 可以同时获得向量分 + BM25 分 + 时序分).
              若某信号下未命中该 memory, 该信号贡献 0.

        Args:
            query_vector: 查询向量 (可能为 None, 此时向量信号贡献 0)
            question: 用户问题文本
            user_id / project_id / recall_level: 过滤条件
            limit: 返回数量上限

        Returns:
            (memories, final_scores) 按融合分数降序排序.
        """
        # 并行执行三个信号 (return_exceptions=True 防止单个信号失败拖垮整体)
        # 时序信号不需要 query_vector / question, 总是可以执行
        vector_task = self._safe_vector_search(
            query_vector, user_id, project_id, recall_level, limit
        )
        keyword_task = self._keyword_search(
            question, user_id, project_id, recall_level, limit
        )
        recency_task = self._recency_search(
            user_id, project_id, recall_level, limit
        )

        results = await asyncio.gather(
            vector_task, keyword_task, recency_task, return_exceptions=True
        )

        # 归一化并融合
        # score_map: memory_id -> 累加的融合分数
        # memory_map: memory_id -> CommittedMemory 对象
        score_map: Dict[str, float] = {}
        memory_map: Dict[str, CommittedMemory] = {}

        # 信号 1: 向量
        vector_result = results[0]
        if isinstance(vector_result, tuple) and vector_result[0]:
            mems, raw_scores = vector_result
            self._merge_signal(
                score_map,
                memory_map,
                mems,
                raw_scores,
                HYBRID_WEIGHT_VECTOR,
            )

        # 信号 2: BM25
        keyword_result = results[1]
        if isinstance(keyword_result, tuple) and keyword_result[0]:
            mems, raw_scores = keyword_result
            self._merge_signal(
                score_map,
                memory_map,
                mems,
                raw_scores,
                HYBRID_WEIGHT_BM25,
            )

        # 信号 3: 时序衰减
        recency_result = results[2]
        if isinstance(recency_result, tuple) and recency_result[0]:
            mems, raw_scores = recency_result
            self._merge_signal(
                score_map,
                memory_map,
                mems,
                raw_scores,
                HYBRID_WEIGHT_RECENCY,
            )

        if not score_map:
            return [], []

        # 按融合分数降序排序, 截取 limit
        sorted_items = sorted(score_map.items(), key=lambda x: x[1], reverse=True)
        sorted_items = sorted_items[:limit]

        memories = [memory_map[mid] for mid, _ in sorted_items]
        final_scores = [score for _, score in sorted_items]
        return memories, final_scores

    async def _safe_vector_search(
        self,
        query_vector: Optional[List[float]],
        user_id: str,
        project_id: Optional[str],
        recall_level: str,
        limit: int,
    ) -> Tuple[List[CommittedMemory], List[float]]:
        """向量检索的安全包装: query_vector 为 None 或失败时返回空, 不抛异常.

        用于 _hybrid_search 中并行调用, 避免向量信号失败影响其他信号.
        """
        if not query_vector:
            return [], []
        try:
            return await self._vector_search(
                query_vector, user_id, project_id, recall_level, limit
            )
        except Exception as e:
            logger.warning("hybrid_search: vector signal failed: %s", e)
            return [], []

    async def _recency_search(
        self,
        user_id: str,
        project_id: Optional[str],
        recall_level: str,
        limit: int,
    ) -> Tuple[List[CommittedMemory], List[float]]:
        """时序衰减信号: 拉取最近记忆, 按 importance * recency_decay 评分.

        参考 Graphiti 的 temporal signal: 新记忆 + 高重要性 = 高分.
        用于三信号融合的第三个信号.
        """
        filter_cond = self._build_filter(user_id, project_id, recall_level)
        # 多取一些 (limit * 3), 让 recency 排序有区分度
        result = await self.db.execute(
            select(CommittedMemory)
            .where(filter_cond)
            .order_by(CommittedMemory.created_at.desc())
            .limit(max(limit * 3, limit))
        )
        memories = list(result.scalars().all())
        if not memories:
            return [], []
        scores = [self._recency_decay(m) for m in memories]
        return memories, scores

    @staticmethod
    def _merge_signal(
        score_map: Dict[str, float],
        memory_map: Dict[str, CommittedMemory],
        memories: List[CommittedMemory],
        raw_scores: List[float],
        weight: float,
    ) -> None:
        """把单个信号的分数归一化后累加进 score_map (原地修改).

        归一化: score_norm = raw_score / max(raw_scores) (max 防除零).
        融合: score_map[mid] += weight * score_norm.
        同一 memory 在同一信号下只有一个分数, 无需取 max.
        """
        if not memories or not raw_scores:
            return
        max_score = max(raw_scores)
        if max_score <= 0:
            # 所有分数都是 0, 归一化无意义, 跳过该信号
            return
        for m, s in zip(memories, raw_scores):
            mid = str(m.id)
            memory_map[mid] = m
            norm = s / max_score if max_score > 0 else 0.0
            score_map[mid] = score_map.get(mid, 0.0) + weight * norm

    async def _load_relations(
        self,
        user_id: str,
        memories: List[CommittedMemory],
        limit: int = 80,
    ) -> List[MemoryRelation]:
        memory_ids = [m.id for m in memories if getattr(m, "id", None)]
        if not memory_ids:
            return []
        try:
            result = await self.db.execute(
                select(MemoryRelation)
                .where(MemoryRelation.user_id == user_id)
                .where(
                    or_(
                        MemoryRelation.source_memory_id.in_(memory_ids),
                        MemoryRelation.target_memory_id.in_(memory_ids),
                    )
                )
                .order_by(MemoryRelation.created_at.desc())
                .limit(limit)
            )
            return list(result.scalars().all())
        except Exception:
            logger.exception("Failed to load memory relation graph")
            return []

    @staticmethod
    def _recency_decay(m: CommittedMemory, half_life_days: float = 60.0) -> float:
        """v2.1 记忆衰减: 半衰期 60 天, importance 越高衰减越慢。

        effective_weight = importance * decay_factor
        """
        if not m.created_at:
            return float(m.importance or 0.0)
        try:
            created_at = m.created_at
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)
            age_days = max(0.0, (datetime.now(timezone.utc) - created_at).total_seconds() / 86400.0)
        except Exception:
            return float(m.importance or 0.0)
        decay = 0.5 ** (age_days / max(half_life_days, 1.0))
        return float(m.importance or 0.0) * (0.4 + 0.6 * decay)

    def _prioritize_by_type(
        self,
        memories: List[CommittedMemory],
        sim_scores: List[float],
    ) -> Tuple[List[CommittedMemory], List[float]]:
        """v2.1: 排序权重 = sim * 0.6 + effective_weight * type_boost * 0.4

        effective_weight 引入 recency decay, 老记忆不会顶掉新信号。
        """
        scored = []
        for i, m in enumerate(memories):
            type_boost = DECISION_PRIORITY.get(m.memory_type, 0.3)
            sim = sim_scores[i] if i < len(sim_scores) else 0.0
            ew = self._recency_decay(m)
            final = sim * 0.6 + ew * type_boost * 0.4
            scored.append((m, final, sim))

        scored.sort(key=lambda x: x[1], reverse=True)

        return (
            [m for m, _, _ in scored],
            [s for _, _, s in scored],
            [final for _, final, _ in scored],
        )

    async def _llm_cluster(
        self,
        question: str,
        memories: List[CommittedMemory],
    ) -> Dict:
        if not memories:
            return {
                "context_summary": "",
                "decision_history": [],
                "patterns": [],
                "conflicts": [],
            }

        memories_text = "\n".join(
            format_retrieval_memory_context(memory, index)
            for index, memory in enumerate(memories, start=1)
        )

        prompt = build_retrieval_prompt(question, memories_text)

        try:
            # Text generation goes through the shared gateway so this legacy
            # retrieval path gets the same timeout/error boundary as Runtime.
            # Embedding remains on the provider adapter because it has a
            # different capability contract.
            from src.shared.llm.model_gateway import ModelGateway

            response = await ModelGateway(get_llm_provider()).generate_text(
                prompt,
                temperature=0.2,
                max_tokens=2000,
                prompt_id="retrieval-cluster",
                prompt_version="v1",
            )
            import json
            data = json.loads(response)

            decision_history = (data.get("decision_history") or [])[:CLUSTER_CONTEXT_LIMIT]
            patterns = (data.get("patterns") or [])[:CLUSTER_PATTERNS_LIMIT]
            conflicts = (data.get("conflicts") or [])[:CLUSTER_CONFLICTS_LIMIT]
            context_summary = data.get("context_summary") or ""

            for item in decision_history:
                try:
                    idx = int(item.get("id", 0)) - 1
                    if 0 <= idx < len(memories):
                        item["memory_id"] = memories[idx].id
                        item["_memory"] = memories[idx]
                    else:
                        item["memory_id"] = ""
                        item["_memory"] = None
                except (ValueError, TypeError):
                    item["memory_id"] = ""
                    item["_memory"] = None

            return {
                "context_summary": context_summary,
                "decision_history": decision_history,
                "patterns": patterns,
                "conflicts": conflicts,
            }
        except Exception:
            return {
                "context_summary": f"Found {len(memories)} relevant memories.",
                "decision_history": [],
                "patterns": [],
                "conflicts": [],
            }

    def _build_output(
        self,
        question: str,
        memories: List[CommittedMemory],
        scores: List[float],
        final_scores: List[float],
        clusters: Dict,
        embed_method: str,
        recall_level: str,
        relations: Optional[List[MemoryRelation]] = None,
    ) -> Dict:
        id_to_memory = {m.id: m for m in memories}

        decision_history = []
        for item in clusters.get("decision_history", []):
            mid = item.get("memory_id", "")
            mem = id_to_memory.get(mid) or item.get("_memory")
            decision_history.append({
                "memory_id": mid,
                "content": item.get("content", ""),
                "reason": item.get("reason", ""),
                "outcome": item.get("outcome", ""),
                "memory_type": mem.memory_type.value if mem else None,
                "epistemic_status": mem.epistemic_status if mem else None,
                "importance": mem.importance if mem else 0.0,
            })

        conflicts = []
        for item in clusters.get("conflicts", []):
            conflicts.append({
                "current": item.get("current", ""),
                "past": item.get("past", ""),
                "explanation": item.get("explanation", ""),
            })

        relevant_memories = []
        entities_set = set()
        for i, m in enumerate(memories[:20]):
            sim_score = scores[i] if i < len(scores) else 0.0
            relevant_memories.append({
                "memory_id": m.id,
                "memory_uri": build_memory_uri(m),
                "context_path": build_context_path(m),
                "title": m.title,
                "content": m.body[:300] + "..." if len(m.body) > 300 else m.body,
                "memory_type": m.memory_type.value,
                "epistemic_status": m.epistemic_status,
                "memory_layer": memory_layer_for_type(m.memory_type),
                "importance": m.importance,
                "confidence": m.confidence,
                "tags": m.tags or [],
                "similarity": round(sim_score, 4),
                "final_score": round(final_scores[i] if i < len(final_scores) else sim_score, 4),
                "valid_from": m.valid_from.isoformat() if m.valid_from else None,
                "valid_until": m.valid_until.isoformat() if m.valid_until else None,
            })
            if m.tags:
                for tag in m.tags:
                    entities_set.add(str(tag))

        return {
            "context_summary": clusters.get("context_summary", ""),
            "decision_history": decision_history,
            "patterns": clusters.get("patterns", []),
            "conflicts": conflicts,
            "relevant_memories": relevant_memories,
            "entities": sorted(list(entities_set)),
            "context_tiers": build_context_tiers(memories[:20]),
            "context_tree": build_context_tree(memories[:20]),
            "memory_layers": build_layer_summary(memories[:20]),
            "relation_graph": build_relation_graph(memories[:20], relations or []),
            "graph_context": {
                "mode": "not_queried", "available": False, "fallback": True,
                "relations": [], "source_memory_ids": [],
            },
            "memory_evolution": build_memory_evolution(memories[:20]),
            "retrieval_trace": [
                build_retrieval_trace_entry(
                    memory=m,
                    rank=i + 1,
                    similarity=scores[i] if i < len(scores) else 0.0,
                    final_score=final_scores[i] if i < len(final_scores) else 0.0,
                    embed_method=embed_method,
                    recall_level=recall_level,
                )
                for i, m in enumerate(memories[:20])
            ],
            "meta": {
                "total_found": len(memories),
                "question": question,
                "retrieved_at": datetime.utcnow().isoformat(),
                "embed_method": embed_method,
                "recall_level": recall_level,
            },
        }

    def _empty_context(self, question: str, embed_method: str, recall_level: str) -> Dict:
        return {
            "context_summary": "No relevant memories found for this question.",
            "decision_history": [],
            "patterns": [],
            "conflicts": [],
            "relevant_memories": [],
            "entities": [],
            "context_tiers": build_context_tiers([]),
            "context_tree": build_context_tree([]),
            "memory_layers": build_layer_summary([]),
            "relation_graph": build_relation_graph([]),
            "graph_context": {
                "mode": "not_queried", "available": False, "fallback": True,
                "relations": [], "source_memory_ids": [],
            },
            "memory_evolution": build_memory_evolution([]),
            "retrieval_trace": [],
            "meta": {
                "total_found": 0,
                "question": question,
                "retrieved_at": datetime.utcnow().isoformat(),
                "embed_method": embed_method,
                "recall_level": recall_level,
            },
        }
