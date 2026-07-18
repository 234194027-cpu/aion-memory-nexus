"""ConflictGraphEngine — 冲突关系网引擎 (Gen 3 Cognitive OS).

职责:
1. 维护 memory 之间的冲突关系图
2. 检测新记忆是否与现有记忆冲突
3. 支持冲突传播推断 (A↔B, B↔C → A↔C)
4. 提供冲突查询接口 (供 Advisor / BeliefEngine 使用)
"""
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set

from sqlalchemy import select, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession

from src.cognition.models.conflict_graph import ConflictGraphEdge
from src.memory.models.committed_memory import CommittedMemory
from src.memory.models.memory_embedding import MemoryEmbedding
from src.memory.services.retrieval_engine import cosine_similarity
from src.shared.ids.id_generator import generate_conflict_edge_id

logger = logging.getLogger(__name__)

# 冲突类型
CONFLICT_TYPES = {
    "belief_conflict",         # 信念冲突
    "decision_conflict",       # 决策冲突
    "preference_conflict",     # 偏好冲突
    "principle_conflict",      # 原则冲突
    "strategy_conflict",       # 策略冲突
    "factual_contradiction",   # 事实矛盾
}

# 相似度阈值
SIMILARITY_THRESHOLD_TO_CONFLICT = 0.75  # 相似度高于此值且语义相反时认为冲突


class ConflictGraphEngine:
    """冲突关系网引擎。"""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def detect_conflicts_for_memory(
        self,
        user_id: str,
        memory_id: str,
    ) -> Dict:
        """检测一条记忆与其他记忆的冲突。

        流程:
        1. 加载目标记忆
        2. 加载用户的所有活跃记忆
        3. 计算语义相似度
        4. 对高相似度记忆，使用 LLM 判断是否冲突
        5. 创建冲突边
        6. 推断传递性冲突

        Args:
            user_id: 用户 ID
            memory_id: 目标记忆 ID

        Returns:
            Dict: {
                "memory_id": str,
                "conflicts_detected": List[Dict],
                "transitive_conflicts": List[Dict],
                "warnings": List[str],
            }
        """
        warnings = []
        conflicts_detected = []
        transitive_conflicts = []

        try:
            # 1. 加载目标记忆
            target_memory = await self._load_memory(user_id, memory_id)
            if not target_memory:
                warnings.append("memory_not_found")
                return {
                    "memory_id": memory_id,
                    "conflicts_detected": [],
                    "transitive_conflicts": [],
                    "warnings": warnings,
                }

            # 2. 加载用户的所有活跃记忆
            all_memories = await self._load_active_memories(user_id, exclude_id=memory_id)
            if not all_memories:
                return {
                    "memory_id": memory_id,
                    "conflicts_detected": [],
                    "transitive_conflicts": [],
                    "warnings": ["no_other_memories"],
                }

            # 3. 计算语义相似度
            target_emb = await self._get_memory_embedding(memory_id)
            if not target_emb:
                warnings.append("no_embedding_for_target")
                return {
                    "memory_id": memory_id,
                    "conflicts_detected": [],
                    "transitive_conflicts": [],
                    "warnings": warnings,
                }

            # 4. 对高相似度记忆，判断是否冲突
            for other_memory in all_memories:
                other_emb = await self._get_memory_embedding(other_memory.id)
                if not other_emb:
                    continue

                similarity = cosine_similarity(target_emb, other_emb)
                if similarity >= SIMILARITY_THRESHOLD_TO_CONFLICT:
                    # 高相似度，使用 LLM 判断是否冲突
                    is_conflict, conflict_type, explanation = await self._check_semantic_conflict(
                        target_memory, other_memory
                    )

                    if is_conflict:
                        # 创建冲突边
                        edge = await self._create_conflict_edge(
                            user_id=user_id,
                            memory_id_a=memory_id,
                            memory_id_b=other_memory.id,
                            conflict_type=conflict_type,
                            statement_a=target_memory.body,
                            statement_b=other_memory.body,
                            explanation=explanation,
                            confidence=similarity,
                            detected_by="conflict_graph_engine",
                        )
                        conflicts_detected.append({
                            "edge_id": edge.id,
                            "other_memory_id": other_memory.id,
                            "conflict_type": conflict_type,
                            "similarity": similarity,
                            "explanation": explanation,
                        })

            # 5. 推断传递性冲突
            transitive = await self._infer_transitive_conflicts(user_id, memory_id)
            transitive_conflicts.extend(transitive)

            await self.db.commit()

        except Exception as e:
            logger.error(f"detect_conflicts_for_memory failed: {e}", exc_info=True)
            warnings.append(f"detection_error: {str(e)}")
            await self.db.rollback()

        return {
            "memory_id": memory_id,
            "conflicts_detected": conflicts_detected,
            "transitive_conflicts": transitive_conflicts,
            "warnings": warnings,
        }

    async def get_conflicts_for_user(
        self,
        user_id: str,
        resolution_status: Optional[List[str]] = None,
        limit: int = 50,
    ) -> List[Dict]:
        """查询用户的冲突关系。

        Args:
            user_id: 用户 ID
            resolution_status: 可选的解析状态过滤
            limit: 返回数量限制

        Returns:
            List[Dict]: 冲突边列表
        """
        query = select(ConflictGraphEdge).where(ConflictGraphEdge.user_id == user_id)

        if resolution_status:
            query = query.where(ConflictGraphEdge.resolution_status.in_(resolution_status))

        query = query.order_by(ConflictGraphEdge.created_at.desc()).limit(limit)

        result = await self.db.execute(query)
        edges = result.scalars().all()

        return [
            {
                "id": e.id,
                "memory_id_a": e.memory_id_a,
                "memory_id_b": e.memory_id_b,
                "conflict_type": e.conflict_type,
                "severity": e.severity,
                "statement_a": e.statement_a[:200] if e.statement_a else None,
                "statement_b": e.statement_b[:200] if e.statement_b else None,
                "explanation": e.explanation,
                "resolution_status": e.resolution_status,
                "confidence": e.confidence,
                "created_at": e.created_at.isoformat() if e.created_at else None,
            }
            for e in edges
        ]

    async def resolve_conflict(
        self,
        edge_id: str,
        resolution_status: str,
        resolution_note: Optional[str] = None,
    ) -> Dict:
        """解析一个冲突边。

        Args:
            edge_id: 冲突边 ID
            resolution_status: 解析状态 (resolved / acknowledged / superseded)
            resolution_note: 解析说明

        Returns:
            Dict: {
                "edge_id": str,
                "old_status": str,
                "new_status": str,
                "resolved_at": str,
            }
        """
        result = await self.db.execute(
            select(ConflictGraphEdge).where(ConflictGraphEdge.id == edge_id)
        )
        edge = result.scalar_one_or_none()

        if not edge:
            raise ValueError(f"conflict_edge_not_found: {edge_id}")

        old_status = edge.resolution_status
        edge.resolution_status = resolution_status
        edge.resolution_note = resolution_note
        edge.resolved_at = datetime.now(timezone.utc)

        await self.db.commit()

        return {
            "edge_id": edge.id,
            "old_status": old_status,
            "new_status": resolution_status,
            "resolved_at": edge.resolved_at.isoformat(),
        }

    async def get_conflict_clusters(
        self,
        user_id: str,
        min_cluster_size: int = 3,
    ) -> List[Dict]:
        """查找冲突聚类 (多个记忆相互冲突的群组)。

        使用简单的连通分量算法。

        Args:
            user_id: 用户 ID
            min_cluster_size: 最小聚类大小

        Returns:
            List[Dict]: 聚类列表
        """
        # 加载所有未解析的冲突边
        result = await self.db.execute(
            select(ConflictGraphEdge).where(
                and_(
                    ConflictGraphEdge.user_id == user_id,
                    ConflictGraphEdge.resolution_status == "unresolved",
                )
            )
        )
        edges = result.scalars().all()

        if not edges:
            return []

        # 构建邻接表
        adjacency: Dict[str, Set[str]] = {}
        for edge in edges:
            if edge.memory_id_a not in adjacency:
                adjacency[edge.memory_id_a] = set()
            if edge.memory_id_b not in adjacency:
                adjacency[edge.memory_id_b] = set()
            adjacency[edge.memory_id_a].add(edge.memory_id_b)
            adjacency[edge.memory_id_b].add(edge.memory_id_a)

        # 查找连通分量 (聚类)
        visited: Set[str] = set()
        clusters = []

        for node in adjacency:
            if node in visited:
                continue

            # BFS 查找连通分量
            cluster = []
            queue = [node]
            while queue:
                current = queue.pop(0)
                if current in visited:
                    continue
                visited.add(current)
                cluster.append(current)
                for neighbor in adjacency.get(current, set()):
                    if neighbor not in visited:
                        queue.append(neighbor)

            if len(cluster) >= min_cluster_size:
                clusters.append({
                    "memory_ids": cluster,
                    "size": len(cluster),
                    "edge_count": sum(len(adjacency[m]) for m in cluster) // 2,
                })

        return clusters

    # ── 内部方法 ──────────────────────────────────────────────────────

    async def _load_memory(self, user_id: str, memory_id: str) -> Optional[CommittedMemory]:
        """加载单条记忆。"""
        result = await self.db.execute(
            select(CommittedMemory).where(
                and_(
                    CommittedMemory.id == memory_id,
                    CommittedMemory.user_id == user_id,
                )
            )
        )
        return result.scalar_one_or_none()

    async def _load_active_memories(
        self,
        user_id: str,
        exclude_id: Optional[str] = None,
        limit: int = 100,
    ) -> List[CommittedMemory]:
        """加载用户的所有活跃记忆。"""
        query = select(CommittedMemory).where(
            and_(
                CommittedMemory.user_id == user_id,
                CommittedMemory.status == "active",
            )
        )

        if exclude_id:
            query = query.where(CommittedMemory.id != exclude_id)

        query = query.limit(limit)

        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def _get_memory_embedding(self, memory_id: str) -> Optional[List[float]]:
        """获取记忆的 embedding 向量。"""
        result = await self.db.execute(
            select(MemoryEmbedding).where(MemoryEmbedding.memory_id == memory_id)
        )
        emb = result.scalar_one_or_none()
        return emb.embedding_vector if emb else None

    async def _check_semantic_conflict(
        self,
        memory_a: CommittedMemory,
        memory_b: CommittedMemory,
    ) -> tuple[bool, str, str]:
        """使用 LLM 判断两条记忆是否语义冲突。

        Returns:
            tuple: (is_conflict, conflict_type, explanation)
        """
        # TODO: 调用 LLM 判断冲突
        # 当前简化：如果两条记忆类型相同但内容相反，认为冲突
        # 未来需要更复杂的语义分析

        # 临时方案：检查关键词
        text_a = (memory_a.body or "").lower()
        text_b = (memory_b.body or "").lower()

        # 简单的反义词检测 (示例)
        opposite_pairs = [
            ("应该", "不应该"),
            ("必须", "不必"),
            ("好", "坏"),
            ("正确", "错误"),
            ("同意", "反对"),
        ]

        for word1, word2 in opposite_pairs:
            if (word1 in text_a and word2 in text_b) or (word2 in text_a and word1 in text_b):
                return True, "belief_conflict", f"语义相反: {word1} vs {word2}"

        return False, "", ""

    async def _create_conflict_edge(
        self,
        user_id: str,
        memory_id_a: str,
        memory_id_b: str,
        conflict_type: str,
        statement_a: str,
        statement_b: str,
        explanation: str,
        confidence: float,
        detected_by: str,
    ) -> ConflictGraphEdge:
        """创建冲突边。"""
        edge_id = generate_conflict_edge_id()
        edge = ConflictGraphEdge(
            id=edge_id,
            user_id=user_id,
            memory_id_a=memory_id_a,
            memory_id_b=memory_id_b,
            conflict_type=conflict_type,
            severity="medium",
            statement_a=statement_a[:500],
            statement_b=statement_b[:500],
            explanation=explanation,
            resolution_status="unresolved",
            confidence=confidence,
            detected_by=detected_by,
        )
        self.db.add(edge)
        await self.db.flush()
        return edge

    async def _infer_transitive_conflicts(
        self,
        user_id: str,
        memory_id: str,
    ) -> List[Dict]:
        """推断传递性冲突。

        如果 A↔B 冲突，B↔C 冲突，则推断 A↔C 可能冲突。
        """
        transitive = []

        # 查找与 memory_id 直接冲突的记忆
        result = await self.db.execute(
            select(ConflictGraphEdge).where(
                and_(
                    ConflictGraphEdge.user_id == user_id,
                    or_(
                        ConflictGraphEdge.memory_id_a == memory_id,
                        ConflictGraphEdge.memory_id_b == memory_id,
                    ),
                )
            )
        )
        direct_edges = result.scalars().all()

        direct_conflicts = set()
        for edge in direct_edges:
            other_id = edge.memory_id_b if edge.memory_id_a == memory_id else edge.memory_id_a
            direct_conflicts.add(other_id)

        # 对每个直接冲突的记忆，查找它的冲突
        for conflict_id in direct_conflicts:
            result2 = await self.db.execute(
                select(ConflictGraphEdge).where(
                    and_(
                        ConflictGraphEdge.user_id == user_id,
                        or_(
                            ConflictGraphEdge.memory_id_a == conflict_id,
                            ConflictGraphEdge.memory_id_b == conflict_id,
                        ),
                    )
                )
            )
            indirect_edges = result2.scalars().all()

            for edge in indirect_edges:
                other_id = edge.memory_id_b if edge.memory_id_a == conflict_id else edge.memory_id_a
                if other_id != memory_id and other_id not in direct_conflicts:
                    # 推断传递性冲突
                    transitive.append({
                        "memory_id": other_id,
                        "via_memory_id": conflict_id,
                        "confidence": 0.5,  # 传递性冲突置信度较低
                        "reason": f"传递性冲突: {memory_id} ↔ {conflict_id} ↔ {other_id}",
                    })

        return transitive


# 便捷函数
async def detect_conflicts(
    db: AsyncSession,
    user_id: str,
    memory_id: str,
) -> Dict:
    """便捷函数：检测记忆的冲突。"""
    engine = ConflictGraphEngine(db)
    return await engine.detect_conflicts_for_memory(user_id, memory_id)


async def get_user_conflicts(
    db: AsyncSession,
    user_id: str,
    resolution_status: Optional[List[str]] = None,
    limit: int = 50,
) -> List[Dict]:
    """便捷函数：查询用户冲突。"""
    engine = ConflictGraphEngine(db)
    return await engine.get_conflicts_for_user(user_id, resolution_status, limit)
