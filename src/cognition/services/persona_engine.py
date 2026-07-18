import json
import logging
from collections import defaultdict
from datetime import datetime
from typing import Dict, List, Optional

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.shared.llm.providers import get_llm_provider
from src.shared.llm.model_gateway import ModelGateway
from src.memory.models.committed_memory import CommittedMemory, CommittedStatus
from src.memory.models.memory_type import MemoryType
from src.memory.models.memory_embedding import MemoryEmbedding
from src.cognition.models.persona_snapshot import PersonaSnapshot
from src.memory.services.retrieval_engine import (
    DECISION_PRIORITY,
    cosine_similarity_batch,
)
from src.shared.ids.id_generator import generate_persona_snapshot_id
from src.cognition.prompts.persona import build_persona_prompt


logger = logging.getLogger(__name__)


ALLOWED_TRAIT_CATEGORIES = {
    "decision_style",
    "values",
    "habits",
    "principles",
    "social",
    "cognitive",
}

MIN_MEMORIES_FOR_TRAITS = 5
DEFAULT_MAX_MEMORIES = 200
DEFAULT_TOP_K_BY_TYPE = 8
PERSONA_QUERY = (
    "用户稳定的人格特征、价值观、决策风格、思维习惯、社交倾向。"
    "提取跨多个事件的稳定模式，而不是某条具体事实。"
)


class PersonaEngine:

    def __init__(self, db: AsyncSession):
        self.db = db

    async def build_persona(
        self,
        user_id: str,
        *,
        max_memories: int = DEFAULT_MAX_MEMORIES,
        top_k_by_type: int = DEFAULT_TOP_K_BY_TYPE,
    ) -> Dict:
        source_memories, embed_method = await self._collect_source_memories(
            user_id, max_memories=max_memories, top_k_by_type=top_k_by_type
        )

        generated_at = datetime.utcnow().isoformat()

        if not source_memories:
            return {
                "user_id": user_id,
                "traits": [],
                "behavior_patterns": [],
                "decision_patterns": [],
                "biases": [],
                "evolution_trends": [],
                "strengths": [],
                "watchouts": [],
                "summary": "暂无可用记忆",
                "summary_model": "",
                "confidence": 0.0,
                "evidence_count": 0,
                "generated_at": generated_at,
                "embed_method": embed_method,
                "snapshot_id": None,
                "snapshot_date": None,
            }

        # 增量更新：查找当天已有的 snapshot
        old_snapshot_context = await self._load_today_snapshot(user_id)

        if len(source_memories) < MIN_MEMORIES_FOR_TRAITS:
            trait_details: List[Dict] = []
            summary = "记忆不足，暂无法生成画像"
            traits_dict: Dict = {}
            behavior_patterns: List[str] = []
            decision_patterns: List[str] = []
            biases: List[str] = []
            evolution_trends: List[str] = []
            strengths: List[str] = []
            watchouts: List[str] = []
            summary_model = ""
            confidence = 0.0
        else:
            try:
                prompt = self._build_prompt(source_memories, old_snapshot_context=old_snapshot_context)
                provider = get_llm_provider()
                response = await ModelGateway(provider).generate_text(
                    prompt, temperature=0.3, max_tokens=3000
                    , prompt_id="persona-analysis", prompt_version="v1"
                )
                parsed = self._parse_llm_response(response, source_memories)
                trait_details = parsed["trait_details"]
                traits_dict = parsed["traits"]
                behavior_patterns = parsed["behavior_patterns"]
                decision_patterns = parsed["decision_patterns"]
                biases = parsed["biases"]
                evolution_trends = parsed["evolution_trends"]
                strengths = parsed["strengths"]
                watchouts = parsed["watchouts"]
                summary = parsed["summary"]
                summary_model = parsed["summary_model"]
                confidence = parsed["confidence"]
            except Exception as exc:
                logger.exception("PersonaEngine LLM call failed: %s", exc)
                trait_details = []
                traits_dict = {}
                behavior_patterns = []
                decision_patterns = []
                biases = []
                evolution_trends = []
                strengths = []
                watchouts = []
                summary = f"画像生成失败: {exc}"
                summary_model = ""
                confidence = 0.0

        snapshot_id, snapshot_date = await self._persist_snapshot(
            user_id=user_id,
            trait_details=trait_details,
            summary=summary,
            evidence_memory_ids=[m.id for m in source_memories],
            embed_method=embed_method,
            traits_dict=traits_dict,
            behavior_patterns=behavior_patterns,
            decision_patterns=decision_patterns,
            biases=biases,
            evolution_trends=evolution_trends,
            strengths=strengths,
            watchouts=watchouts,
            summary_model=summary_model,
            confidence=confidence,
        )

        if snapshot_id:
            from src.execution.services.audit_logger import AuditLogger
            await AuditLogger.log(
                self.db,
                user_id=user_id,
                action="persona_rebuild",
                actor_type="user",
                actor_id=user_id,
                target_type="persona_snapshot",
                target_id=snapshot_id,
                detail={"trait_count": len(trait_details), "evidence_count": len(source_memories)},
            )

        return {
            "user_id": user_id,
            "traits": traits_dict,
            "trait_details": trait_details,
            "behavior_patterns": behavior_patterns,
            "decision_patterns": decision_patterns,
            "biases": biases,
            "evolution_trends": evolution_trends,
            "strengths": strengths,
            "watchouts": watchouts,
            "summary": summary,
            "summary_model": summary_model,
            "confidence": confidence,
            "evidence_count": len(source_memories),
            "generated_at": generated_at,
            "embed_method": embed_method,
            "snapshot_id": snapshot_id,
            "snapshot_date": snapshot_date,
        }

    async def _load_today_snapshot(self, user_id: str) -> Optional[Dict]:
        """加载当天已有的 snapshot 作为增量上下文。"""
        today = datetime.utcnow().strftime("%Y-%m-%d")
        try:
            result = await self.db.execute(
                select(PersonaSnapshot)
                .where(
                    PersonaSnapshot.user_id == user_id,
                    PersonaSnapshot.snapshot_date == today,
                )
                .order_by(PersonaSnapshot.created_at.desc())
                .limit(1)
            )
            snapshot = result.scalars().first()
            if not snapshot:
                return None
            return {
                "traits_json": snapshot.traits_json or "[]",
                "patterns_json": snapshot.patterns_json or "[]",
                "biases_json": snapshot.biases_json or "[]",
            }
        except Exception as exc:
            logger.warning("Failed to load today's snapshot: %s", exc)
            return None

    async def _collect_source_memories(
        self,
        user_id: str,
        *,
        max_memories: int,
        top_k_by_type: int,
    ) -> tuple:
        base_filter = and_(
            CommittedMemory.user_id == user_id,
            CommittedMemory.status == CommittedStatus.ACTIVE,
        )

        result = await self.db.execute(
            select(CommittedMemory).where(base_filter)
        )
        memories = list(result.scalars().all())

        if not memories:
            return [], "keyword"

        embed_method = "keyword"
        try:
            provider = get_llm_provider()
            query_vector = await provider.embed(PERSONA_QUERY)
            if query_vector:
                embed_method = "semantic"
        except Exception:
            query_vector = None

        if embed_method == "semantic" and query_vector:
            memory_ids = [m.id for m in memories]
            try:
                emb_result = await self.db.execute(
                    select(MemoryEmbedding).where(MemoryEmbedding.memory_id.in_(memory_ids))
                )
                emb_map = {e.memory_id: e for e in emb_result.scalars().all()}

                scored: List[tuple] = []
                for m in memories:
                    emb = emb_map.get(m.id)
                    if emb and emb.embedding_vector:
                        scored.append((m, emb.embedding_vector))
                if scored:
                    sims = cosine_similarity_batch(query_vector, [v for _, v in scored])
                    fused = []
                    for (m, _), sim in zip(scored, sims):
                        type_boost = DECISION_PRIORITY.get(m.memory_type, 0.3)
                        fused.append((m, sim * 0.6 + m.importance * type_boost * 0.4))
                    fused.sort(key=lambda x: x[1], reverse=True)
                    ranked = [m for m, _ in fused][:max_memories]
                else:
                    ranked = self._keyword_rank(memories, PERSONA_QUERY)[:max_memories]
                    embed_method = "keyword"
            except Exception as exc:
                logger.warning("Persona semantic ranking failed, fallback to keyword: %s", exc)
                ranked = self._keyword_rank(memories, PERSONA_QUERY)[:max_memories]
                embed_method = "keyword"
        else:
            ranked = self._keyword_rank(memories, PERSONA_QUERY)[:max_memories]

        return self._balance_by_type(ranked, top_k_by_type), embed_method

    def _keyword_rank(self, memories: List[CommittedMemory], query: str) -> List[CommittedMemory]:
        keywords = [w for w in query.lower().split() if len(w) > 1]
        scored = []
        for m in memories:
            text = f"{m.title} {m.body}".lower()
            if not keywords:
                score = 0.0
            else:
                hits = sum(1.0 for k in keywords if k in text)
                score = hits / max(len(keywords), 1)
            type_boost = DECISION_PRIORITY.get(m.memory_type, 0.3)
            final = score * 0.4 + m.importance * type_boost * 0.6
            scored.append((m, final))
        scored.sort(key=lambda x: x[1], reverse=True)
        return [m for m, _ in scored]

    def _balance_by_type(
        self,
        memories: List[CommittedMemory],
        top_k_by_type: int,
    ) -> List[CommittedMemory]:
        buckets: Dict[MemoryType, List[CommittedMemory]] = defaultdict(list)
        for m in memories:
            buckets[m.memory_type].append(m)
        selected: List[CommittedMemory] = []
        seen_ids = set()
        for mtype in [
            MemoryType.DECISION,
            MemoryType.INSIGHT,
            MemoryType.PRINCIPLE,
            MemoryType.PREFERENCE,
            MemoryType.CORRECTION,
            MemoryType.PERSONA_HYPOTHESIS,
            MemoryType.FACT,
            MemoryType.PROJECT_CONTEXT,
            MemoryType.TIMELINE_EVENT,
            MemoryType.TASK,
        ]:
            for m in buckets.get(mtype, [])[:top_k_by_type]:
                if m.id in seen_ids:
                    continue
                selected.append(m)
                seen_ids.add(m.id)
        for m in memories:
            if m.id not in seen_ids:
                selected.append(m)
                seen_ids.add(m.id)
        return selected

    def _build_prompt(
        self,
        memories: List[CommittedMemory],
        old_snapshot_context: Optional[Dict] = None,
    ) -> str:
        lines = []
        for i, m in enumerate(memories):
            content = (m.body or "")[:200]
            lines.append(
                f"[id={i+1}] ({m.memory_type.value}, importance={m.importance:.2f}, "
                f"confidence={m.confidence:.2f}) {m.title}: {content}"
            )
        listing = "\n".join(lines)

        return build_persona_prompt(
            memories_count=len(memories),
            listing=listing,
            old_snapshot=old_snapshot_context,
        )

    def _parse_llm_response(
        self, response: str, memories: List[CommittedMemory]
    ) -> Dict:
        from src.shared.utils.llm_output import extract_json_object
        data = extract_json_object(response)
        if data is None:
            logger.warning("PersonaEngine JSON parse failed")
            return self._empty_result("画像生成失败: JSON 解析错误")

        # 解析 traits dict（决策风格等结构化字段）
        raw_traits_dict = data.get("traits") or {}
        traits_dict: Dict = {}
        if isinstance(raw_traits_dict, dict):
            for key in ("decision_style", "risk_profile", "thinking_mode", "execution_style", "stability"):
                val = raw_traits_dict.get(key)
                if val:
                    traits_dict[key] = str(val)

        # 解析 trait_details（旧格式兼容）
        raw_trait_details = data.get("trait_details") or data.get("traits") or []
        if isinstance(raw_trait_details, dict):
            raw_trait_details = []
        clean_trait_details: List[Dict] = []
        for item in raw_trait_details:
            if not isinstance(item, dict):
                continue
            category = str(item.get("category", "")).strip()
            if category not in ALLOWED_TRAIT_CATEGORIES:
                continue
            claim = str(item.get("claim", "")).strip()
            if not claim:
                continue
            evidence_ids = self._normalize_evidence_ids(
                item.get("evidence_memory_ids"), memories
            )
            if not evidence_ids:
                continue
            try:
                confidence = float(item.get("confidence", 0.5))
            except (TypeError, ValueError):
                confidence = 0.5
            confidence = max(0.0, min(1.0, confidence))
            clean_trait_details.append({
                "category": category,
                "claim": claim,
                "evidence_memory_ids": evidence_ids,
                "confidence": round(confidence, 3),
            })
            if len(clean_trait_details) >= 10:
                break

        def _parse_str_list(val) -> List[str]:
            if not val or not isinstance(val, list):
                return []
            return [str(v) for v in val if v]

        behavior_patterns = _parse_str_list(data.get("behavior_patterns"))
        decision_patterns = _parse_str_list(data.get("decision_patterns"))
        biases = _parse_str_list(data.get("biases"))
        evolution_trends = _parse_str_list(data.get("evolution_trends"))
        strengths = _parse_str_list(data.get("strengths"))
        watchouts = _parse_str_list(data.get("watchouts"))

        summary = str(data.get("summary", "")).strip()
        if not summary:
            summary = "画像已生成。"
        summary_model = str(data.get("summary_model", "")).strip()

        try:
            confidence = float(data.get("confidence", 0.5))
        except (TypeError, ValueError):
            confidence = 0.5
        confidence = max(0.0, min(1.0, confidence))

        if not clean_trait_details and "暂无法生成画像" not in summary and "画像生成失败" not in summary:
            summary = summary or "记忆不足，暂无法生成画像"

        return {
            "trait_details": clean_trait_details,
            "traits": traits_dict,
            "behavior_patterns": behavior_patterns,
            "decision_patterns": decision_patterns,
            "biases": biases,
            "evolution_trends": evolution_trends,
            "strengths": strengths,
            "watchouts": watchouts,
            "summary": summary[:500],
            "summary_model": summary_model[:500],
            "confidence": round(confidence, 3),
        }

    def _empty_result(self, summary: str) -> Dict:
        return {
            "trait_details": [],
            "traits": {},
            "behavior_patterns": [],
            "decision_patterns": [],
            "biases": [],
            "evolution_trends": [],
            "strengths": [],
            "watchouts": [],
            "summary": summary,
            "summary_model": "",
            "confidence": 0.0,
        }

    def _normalize_evidence_ids(
        self,
        raw_evidence,
        memories: List[CommittedMemory],
    ) -> List[str]:
        if raw_evidence is None:
            return []
        if isinstance(raw_evidence, str):
            try:
                raw_evidence = json.loads(raw_evidence)
            except Exception:
                return []
        if not isinstance(raw_evidence, (list, tuple)):
            return []

        result: List[str] = []
        seen = set()
        valid_ids = {m.id for m in memories}
        for entry in raw_evidence:
            mid: Optional[str] = None
            if isinstance(entry, int):
                idx = entry - 1
                if 0 <= idx < len(memories):
                    mid = memories[idx].id
            elif isinstance(entry, str):
                stripped = entry.strip()
                if stripped.isdigit():
                    idx = int(stripped) - 1
                    if 0 <= idx < len(memories):
                        mid = memories[idx].id
                elif stripped in valid_ids:
                    mid = stripped
            if mid and mid in valid_ids and mid not in seen:
                result.append(mid)
                seen.add(mid)
        return result

    async def _persist_snapshot(
        self,
        *,
        user_id: str,
        trait_details: List[Dict],
        summary: str,
        evidence_memory_ids: List[str],
        embed_method: str,
        traits_dict: Dict,
        behavior_patterns: List[str],
        decision_patterns: List[str],
        biases: List[str],
        evolution_trends: List[str],
        strengths: List[str],
        watchouts: List[str],
        summary_model: str,
        confidence: float,
    ) -> tuple:
        snapshot_date = datetime.utcnow().strftime("%Y-%m-%d")
        snapshot_id = generate_persona_snapshot_id()

        # 合并 decision patterns 和 trait_details 中的 evidence 为 source_decision_ids
        source_decision_ids = list(set(
            eid for td in trait_details for eid in td.get("evidence_memory_ids", [])
            if eid.startswith("dec_")
        ))

        try:
            record = PersonaSnapshot(
                id=snapshot_id,
                user_id=user_id,
                snapshot_date=snapshot_date,
                mode="full",
                traits_json=json.dumps(trait_details, ensure_ascii=False),
                summary=summary,
                evidence_memory_ids=json.dumps(evidence_memory_ids, ensure_ascii=False),
                embed_method=embed_method,
                patterns_json=json.dumps({
                    "behavior_patterns": behavior_patterns,
                    "decision_patterns": decision_patterns,
                    "strengths": strengths,
                    "watchouts": watchouts,
                }, ensure_ascii=False),
                biases_json=json.dumps(biases, ensure_ascii=False),
                decision_style_json=json.dumps(traits_dict, ensure_ascii=False),
                risk_profile_json=json.dumps({
                    "risk_profile": traits_dict.get("risk_profile", ""),
                    "stability": traits_dict.get("stability", ""),
                }, ensure_ascii=False),
                evolution_json=json.dumps({
                    "evolution_trends": evolution_trends,
                    "summary_model": summary_model,
                    "confidence": confidence,
                }, ensure_ascii=False),
                source_decision_ids=json.dumps(source_decision_ids, ensure_ascii=False),
            )
            self.db.add(record)
            await self.db.commit()
        except Exception as exc:
            logger.warning("PersonaEngine snapshot persist failed: %s", exc)
            try:
                await self.db.rollback()
            except Exception:
                pass
            return None, snapshot_date
        return snapshot_id, snapshot_date
