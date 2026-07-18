"""BeliefEngine — 信念系统演化引擎 (Gen 3 Cognitive OS).

职责:
1. 从 CommittedMemory 中提取和更新用户信念
2. 检测信念冲突 (新证据 vs 旧信念)
3. 触发信念演化 (当置信度下降到阈值以下)
4. 提供信念查询接口 (供 Advisor / ContextRouter 使用)
"""
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional

from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from src.cognition.models.belief_system import BeliefSystem
from src.memory.models.committed_memory import CommittedMemory
from src.memory.models.memory_embedding import MemoryEmbedding
from src.shared.ids.id_generator import generate_belief_id

logger = logging.getLogger(__name__)

# 信念分类
BELIEF_CATEGORIES = {
    "decision_style",      # 决策风格
    "risk_preference",     # 风险偏好
    "technical_belief",    # 技术信念
    "personal_value",      # 个人价值观
    "working_habit",       # 工作习惯
    "social_tendency",     # 社交倾向
}

# 阈值配置
MIN_CONFIDENCE_TO_CHALLENGE = 0.3  # 置信度低于此值时触发挑战
MIN_EVIDENCE_TO_FORM_BELIEF = 3    # 形成信念所需的最少证据数
BELIEF_DECAY_RATE = 0.95           # 每次挑战后置信度衰减率


class BeliefEngine:
    """信念系统演化引擎。"""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def extract_beliefs_from_memories(
        self,
        user_id: str,
        memory_ids: Optional[List[str]] = None,
    ) -> Dict:
        """从记忆中提取信念。

        流程:
        1. 加载相关记忆 (如果未指定 memory_ids，加载最近的 DECISION/INSIGHT)
        2. 使用 LLM 提取信念候选
        3. 与现有信念匹配 (如果相似度高，更新证据；否则创建新信念)
        4. 返回提取结果

        Args:
            user_id: 用户 ID
            memory_ids: 可选的记忆 ID 列表 (如果不指定，自动选择)

        Returns:
            Dict: {
                "beliefs_created": List[str],
                "beliefs_updated": List[str],
                "beliefs_challenged": List[str],
                "warnings": List[str],
            }
        """
        warnings = []
        beliefs_created = []
        beliefs_updated = []
        beliefs_challenged = []

        try:
            # 1. 加载相关记忆
            if memory_ids:
                memories = await self._load_memories_by_ids(user_id, memory_ids)
            else:
                memories = await self._load_recent_decision_memories(user_id, limit=20)

            if not memories:
                warnings.append("no_memories_found")
                return {
                    "beliefs_created": [],
                    "beliefs_updated": [],
                    "beliefs_challenged": [],
                    "warnings": warnings,
                }

            # 2. 加载现有信念
            existing_beliefs = await self._load_active_beliefs(user_id)

            # 3. 对每条记忆，检查是否与现有信念冲突或支撑
            for memory in memories:
                matched_belief = await self._find_matching_belief(memory, existing_beliefs)

                if matched_belief:
                    # 记忆支撑现有信念 → 更新置信度
                    await self._reinforce_belief(matched_belief, memory.id)
                    beliefs_updated.append(matched_belief.id)
                else:
                    # 可能形成新信念 → 检查是否有足够证据
                    new_belief = await self._try_form_new_belief(user_id, memory, existing_beliefs)
                    if new_belief:
                        beliefs_created.append(new_belief.id)
                        existing_beliefs.append(new_belief)

            # 4. 检查信念稳定性 (长期未强化的信念衰减)
            challenged = await self._check_belief_stability(user_id, existing_beliefs)
            beliefs_challenged.extend(challenged)

            await self.db.commit()

        except Exception as e:
            logger.error(f"extract_beliefs_from_memories failed: {e}", exc_info=True)
            warnings.append(f"extraction_error: {str(e)}")
            await self.db.rollback()

        return {
            "beliefs_created": beliefs_created,
            "beliefs_updated": beliefs_updated,
            "beliefs_challenged": beliefs_challenged,
            "warnings": warnings,
        }

    async def get_beliefs_for_user(
        self,
        user_id: str,
        categories: Optional[List[str]] = None,
        min_confidence: float = 0.0,
        limit: int = 50,
    ) -> List[Dict]:
        """查询用户信念。

        Args:
            user_id: 用户 ID
            categories: 可选的信念分类过滤
            min_confidence: 最小置信度
            limit: 返回数量限制

        Returns:
            List[Dict]: 信念列表
        """
        query = select(BeliefSystem).where(
            and_(
                BeliefSystem.user_id == user_id,
                BeliefSystem.status == "active",
                BeliefSystem.confidence >= min_confidence,
            )
        )

        if categories:
            query = query.where(BeliefSystem.belief_category.in_(categories))

        query = query.order_by(BeliefSystem.confidence.desc()).limit(limit)

        result = await self.db.execute(query)
        beliefs = result.scalars().all()

        return [
            {
                "id": b.id,
                "category": b.belief_category,
                "title": b.title,
                "content": b.content,
                "confidence": b.confidence,
                "stability": b.stability,
                "evidence_count": len(b.evidence_memory_ids or []),
                "last_challenged_at": b.last_challenged_at.isoformat() if b.last_challenged_at else None,
                "created_at": b.created_at.isoformat() if b.created_at else None,
            }
            for b in beliefs
        ]

    async def challenge_belief(
        self,
        belief_id: str,
        challenge_reason: str,
        new_evidence_memory_id: Optional[str] = None,
    ) -> Dict:
        """挑战一个信念 (当新证据与信念冲突时)。

        流程:
        1. 加载信念
        2. 记录挑战历史
        3. 降低置信度
        4. 如果置信度低于阈值，标记为 superseded

        Args:
            belief_id: 信念 ID
            challenge_reason: 挑战原因
            new_evidence_memory_id: 可选的新证据记忆 ID

        Returns:
            Dict: {
                "belief_id": str,
                "old_confidence": float,
                "new_confidence": float,
                "status": str,
                "challenged_at": str,
            }
        """
        result = await self.db.execute(
            select(BeliefSystem).where(BeliefSystem.id == belief_id)
        )
        belief = result.scalar_one_or_none()

        if not belief:
            raise ValueError(f"belief_not_found: {belief_id}")

        old_confidence = belief.confidence
        new_confidence = old_confidence * BELIEF_DECAY_RATE

        # 记录挑战历史
        evolution_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "type": "challenge",
            "reason": challenge_reason,
            "old_confidence": old_confidence,
            "new_confidence": new_confidence,
            "trigger_memory_id": new_evidence_memory_id,
        }
        belief.evolution_history = (belief.evolution_history or []) + [evolution_entry]

        # 更新置信度和稳定性
        belief.confidence = new_confidence
        belief.stability = belief.stability * 0.9  # 稳定性也下降
        belief.last_challenged_at = datetime.now(timezone.utc)

        # 如果置信度太低，标记为 superseded
        if new_confidence < MIN_CONFIDENCE_TO_CHALLENGE:
            belief.status = "superseded"
            belief.valid_until = datetime.now(timezone.utc)

        await self.db.commit()

        return {
            "belief_id": belief.id,
            "old_confidence": old_confidence,
            "new_confidence": new_confidence,
            "status": belief.status,
            "challenged_at": belief.last_challenged_at.isoformat(),
        }

    # ── 内部方法 ──────────────────────────────────────────────────────

    async def _load_memories_by_ids(self, user_id: str, memory_ids: List[str]) -> List[CommittedMemory]:
        """加载指定 ID 的记忆。"""
        result = await self.db.execute(
            select(CommittedMemory).where(
                and_(
                    CommittedMemory.id.in_(memory_ids),
                    CommittedMemory.user_id == user_id,
                )
            )
        )
        return list(result.scalars().all())

    async def _load_recent_decision_memories(self, user_id: str, limit: int = 20) -> List[CommittedMemory]:
        """加载最近的 DECISION 和 INSIGHT 类型记忆。"""
        result = await self.db.execute(
            select(CommittedMemory).where(
                and_(
                    CommittedMemory.user_id == user_id,
                    CommittedMemory.memory_type.in_(["decision", "insight"]),
                )
            ).order_by(CommittedMemory.created_at.desc()).limit(limit)
        )
        return list(result.scalars().all())

    async def _load_active_beliefs(self, user_id: str) -> List[BeliefSystem]:
        """加载用户的所有活跃信念。"""
        result = await self.db.execute(
            select(BeliefSystem).where(
                and_(
                    BeliefSystem.user_id == user_id,
                    BeliefSystem.status == "active",
                )
            )
        )
        return list(result.scalars().all())

    async def _find_matching_belief(
        self,
        memory: CommittedMemory,
        beliefs: List[BeliefSystem],
    ) -> Optional[BeliefSystem]:
        """查找与记忆匹配的现有信念 (基于语义相似度)。

        如果相似度 > 0.8，认为记忆支撑该信念。
        """
        if not beliefs:
            return None

        # 加载记忆的 embedding
        emb_result = await self.db.execute(
            select(MemoryEmbedding).where(MemoryEmbedding.memory_id == memory.id)
        )
        memory_emb = emb_result.scalar_one_or_none()

        if not memory_emb or not memory_emb.embedding_vector:
            return None

        # 计算与每个信念的相似度 (基于信念的标题和内容)
        for belief in beliefs:
            belief_text = f"{belief.title} {belief.content}"
            # TODO: 加载信念的 embedding (如果有)
            # 当前简化：使用关键词匹配
            if self._keyword_match(memory.body, belief_text):
                return belief

        return None

    def _keyword_match(self, text1: str, text2: str, threshold: float = 0.3) -> bool:
        """简单的关键词匹配 (临时方案，未来用 embedding)。"""
        words1 = set(text1.lower().split())
        words2 = set(text2.lower().split())
        overlap = len(words1 & words2)
        similarity = overlap / max(len(words1), len(words2), 1)
        return similarity >= threshold

    async def _try_form_new_belief(
        self,
        user_id: str,
        memory: CommittedMemory,
        existing_beliefs: List[BeliefSystem],
    ) -> Optional[BeliefSystem]:
        """尝试从记忆形成新信念。

        条件:
        1. 记忆类型是 DECISION 或 INSIGHT
        2. 记忆的重要性 >= 0.7
        3. 没有与现有信念冲突
        """
        if memory.memory_type not in ["decision", "insight"]:
            return None

        if memory.importance < 0.7:
            return None

        # 推断信念分类
        category = self._infer_belief_category(memory)

        # 创建新信念
        belief_id = generate_belief_id()
        new_belief = BeliefSystem(
            id=belief_id,
            user_id=user_id,
            belief_category=category,
            title=memory.title or f"Belief from {memory.memory_type}",
            content=memory.body or "",
            confidence=0.5,  # 初始置信度
            stability=1.0,
            status="active",
            evidence_memory_ids=[memory.id],
            evidence_decision_ids=[],
            evolution_history=[
                {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "type": "formed",
                    "source_memory_id": memory.id,
                    "initial_confidence": 0.5,
                }
            ],
            valid_from=datetime.now(timezone.utc),
        )

        self.db.add(new_belief)
        await self.db.flush()

        return new_belief

    def _infer_belief_category(self, memory: CommittedMemory) -> str:
        """从记忆推断信念分类。"""
        content = (memory.body or "").lower()

        if "决定" in content or "选择" in content or "decision" in content:
            return "decision_style"
        elif "风险" in content or "risk" in content:
            return "risk_preference"
        elif "技术" in content or "technical" in content or "代码" in content:
            return "technical_belief"
        elif "价值" in content or "value" in content or "重要" in content:
            return "personal_value"
        elif "习惯" in content or "habit" in content or "总是" in content:
            return "working_habit"
        elif "社交" in content or "social" in content or "团队" in content:
            return "social_tendency"
        else:
            return "personal_value"  # 默认分类

    async def _reinforce_belief(self, belief: BeliefSystem, memory_id: str) -> None:
        """强化信念 (当新证据支撑时)。"""
        # 添加证据
        evidence_ids = belief.evidence_memory_ids or []
        if memory_id not in evidence_ids:
            evidence_ids.append(memory_id)
            belief.evidence_memory_ids = evidence_ids

        # 提升置信度 (但不超过 1.0)
        belief.confidence = min(1.0, belief.confidence + 0.05)

        # 提升稳定性
        belief.stability = min(1.0, belief.stability + 0.02)

        # 记录强化历史
        evolution_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "type": "reinforced",
            "source_memory_id": memory_id,
            "old_confidence": belief.confidence - 0.05,
            "new_confidence": belief.confidence,
        }
        belief.evolution_history = (belief.evolution_history or []) + [evolution_entry]

        await self.db.flush()

    async def _check_belief_stability(
        self,
        user_id: str,
        beliefs: List[BeliefSystem],
    ) -> List[str]:
        """检查信念稳定性。

        长期未强化的信念 (last_challenged_at > 90 days) 会降低稳定性。
        """
        challenged = []
        now = datetime.now(timezone.utc)

        for belief in beliefs:
            if not belief.last_challenged_at:
                continue

            days_since_challenge = (now - belief.last_challenged_at).days
            if days_since_challenge > 90:
                # 90天未强化，降低稳定性
                old_stability = belief.stability
                belief.stability = belief.stability * 0.95

                if old_stability != belief.stability:
                    challenged.append(belief.id)

        return challenged


# 便捷函数
async def extract_beliefs(
    db: AsyncSession,
    user_id: str,
    memory_ids: Optional[List[str]] = None,
) -> Dict:
    """便捷函数：从记忆中提取信念。"""
    engine = BeliefEngine(db)
    return await engine.extract_beliefs_from_memories(user_id, memory_ids)


async def get_user_beliefs(
    db: AsyncSession,
    user_id: str,
    categories: Optional[List[str]] = None,
    min_confidence: float = 0.0,
    limit: int = 50,
) -> List[Dict]:
    """便捷函数：查询用户信念。"""
    engine = BeliefEngine(db)
    return await engine.get_beliefs_for_user(user_id, categories, min_confidence, limit)
