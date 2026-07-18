"""LLM-based Reranker (参考 mem0 的 rerank 做法).

在向量检索召回后, 用 LLM cross-encoder 风格对 (query, memory) 对打分 (0-10),
按分数排序, 取 top_k. 失败时 fallback 到原始顺序.

配置开关 (环境变量):
    ENABLE_RERANKER: 默认 "false" (增加延迟, 可选开启)
    RERANKER_TOP_K: 默认 5
    RERANKER_MAX_CANDIDATES: 默认 20 (限制候选数量, 避免 prompt 过长)

接口:
    async def rerank(query: str, memories: List[dict], top_k: int = 5) -> List[dict]
"""
import json
import logging
import time
from typing import List, Dict, Optional

from src.shared.config import settings
from src.shared.llm.providers import get_llm_provider, LLMProvider
from src.shared.llm.model_gateway import ModelGateway

logger = logging.getLogger(__name__)

# 配置开关: 优先读 settings (统一配置源), 兼容环境变量.
# 第三轮迭代默认开启，提升单用户本地系统的检索质量.
ENABLE_RERANKER = settings.ENABLE_RERANKER
RERANKER_TOP_K = settings.RERANKER_TOP_K
RERANKER_MAX_CANDIDATES = settings.RERANKER_MAX_CANDIDATES


class LLMReranker:
    """LLM-based reranker: 让 LLM 对 (query, memory) 对批量打分 0-10.

    实现策略 (参考 mem0):
        1. 把所有候选 memory 一次性塞进 prompt
        2. 让 LLM 返回 JSON {"scores": [s1, s2, ...]} (0-10 整数)
        3. 按分数降序排序, 取 top_k
        4. 任何异常 (LLM 失败 / JSON 解析失败 / 长度不匹配) 都 fallback 到原顺序
    """

    def __init__(self, provider: Optional[LLMProvider] = None):
        # 复用现有 LLM provider, 不引入新依赖
        self.provider = provider or get_llm_provider()

    async def rerank(
        self,
        query: str,
        memories: List[Dict],
        top_k: int = 5,
    ) -> List[Dict]:
        """对 memories 按 LLM 打分重新排序, 返回 top_k.

        Args:
            query: 用户问题
            memories: 候选 memory 字典列表, 每个至少含 'title' / 'content' 字段
            top_k: 返回前 K 条

        Returns:
            重排后的 memory 字典列表 (长度 <= top_k). 失败时 fallback 到原顺序.
        """
        if not memories:
            return []
        # 候选数 <= top_k 时无需 rerank
        if len(memories) <= top_k:
            return memories[:top_k]

        # 计时与埋点字段 (提前初始化, 保证 except 分支也能读取).
        _llm_start = time.perf_counter()
        _retrieval_candidate_count = len(memories)
        _prompt_length = 0
        try:
            # 限制候选数量, 避免 prompt 过长导致 LLM 拒答或超时
            capped = memories[:RERANKER_MAX_CANDIDATES]
            _retrieval_candidate_count = len(capped)
            prompt = self._build_prompt(query, capped)
            _prompt_length = len(prompt)
            response = await ModelGateway(self.provider).generate_text(
                prompt, temperature=0.0, max_tokens=2000,
                prompt_id="memory-rerank", prompt_version="v1",
            )
            _llm_duration_ms = int((time.perf_counter() - _llm_start) * 1000)
            # 记录 reranker LLM 调用埋点 (字段与 llm_trace 对齐, 便于聚合分析)
            logger.info(
                "Reranker LLM call succeeded",
                extra={
                    "provider": "reranker",
                    "model": "default",
                    "prompt_length": _prompt_length,
                    "response_length": len(response) if response else 0,
                    "duration_ms": _llm_duration_ms,
                    "success": True,
                    "retrieval_candidate_count": _retrieval_candidate_count,
                },
            )
            scores = self._parse_scores(response, len(capped))
            if not scores or len(scores) != len(capped):
                # 解析失败或长度不匹配, fallback 到原顺序
                logger.warning(
                    "Reranker: score parsing failed (expected %d, got %s), fallback",
                    len(capped),
                    len(scores) if scores else "none",
                )
                return memories[:top_k]
            scored = list(zip(capped, scores))
            scored.sort(key=lambda x: x[1], reverse=True)
            return [m for m, _ in scored[:top_k]]
        except Exception as e:
            # 任何异常都 fallback, 不破坏主流程
            _llm_duration_ms = int((time.perf_counter() - _llm_start) * 1000)
            logger.warning(
                "Reranker LLM call failed: %s; fallback to original order",
                e,
                extra={
                    "provider": "reranker",
                    "model": "default",
                    "prompt_length": _prompt_length,
                    "response_length": 0,
                    "duration_ms": _llm_duration_ms,
                    "success": False,
                    "error_type": type(e).__name__,
                    "retrieval_candidate_count": _retrieval_candidate_count,
                },
            )
            return memories[:top_k]

    def _build_prompt(self, query: str, memories: List[Dict]) -> str:
        """构造让 LLM 批量打分的 prompt (单次调用, 节省 token)."""
        lines = [
            "你是一个记忆相关性评分器。给定用户问题和若干待排序的正式记忆,",
            "请对每条记忆与问题的相关性打分 (0-10 整数, 10 最相关, 0 完全无关)。",
            "只考虑语义相关性, 忽略时间因素和重要性权重。",
            "",
            f"用户问题: {query}",
            "",
            "待排序的正式记忆:",
        ]
        for i, m in enumerate(memories, 1):
            title = str(m.get("title") or "")[:80]
            content = str(m.get("content") or m.get("body") or "")[:200]
            lines.append(f"[{i}] title: {title}")
            lines.append(f"    content: {content}")
        lines.append("")
        lines.append("请严格按以下 JSON 格式输出, 不要任何额外说明或 markdown 包裹:")
        lines.append('{"scores": [s1, s2, ...]}')
        lines.append(
            "其中 s1, s2, ... 分别对应 [1], [2], ... 的分数 (0-10 整数), 顺序与数量必须完全一致."
        )
        return "\n".join(lines)

    def _parse_scores(
        self, response: str, expected_count: int
    ) -> List[float]:
        """解析 LLM 返回的 JSON, 提取分数列表.

        容忍 ```json``` 包裹和前后噪声文本. 长度不匹配视为解析失败.
        """
        if not response:
            return []
        text = response.strip()
        # 容忍 ```json``` 包裹
        if text.startswith("```"):
            text = text.strip("`")
            if text.startswith("json"):
                text = text[4:]
            start = text.find("{")
            end = text.rfind("}")
            if start != -1 and end != -1 and end > start:
                text = text[start : end + 1]
        try:
            data = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            return []
        scores_raw = data.get("scores") if isinstance(data, dict) else None
        if not isinstance(scores_raw, list):
            return []
        scores: List[float] = []
        for s in scores_raw:
            try:
                v = float(s)
            except (ValueError, TypeError):
                v = 0.0
            # 钳位到 [0, 10]
            v = max(0.0, min(10.0, v))
            scores.append(v)
        if len(scores) != expected_count:
            # 长度不匹配, 视为解析失败 (避免错位)
            return []
        return scores


# 模块级单例 (lazy 初始化, 避免每次调用都创建 provider)
_default_reranker: Optional[LLMReranker] = None


def get_reranker() -> LLMReranker:
    """获取默认 reranker 单例."""
    global _default_reranker
    if _default_reranker is None:
        _default_reranker = LLMReranker()
    return _default_reranker


async def rerank(
    query: str, memories: List[Dict], top_k: int = 5
) -> List[Dict]:
    """模块级便捷函数: 用默认 reranker 重排.

    受 ENABLE_RERANKER 开关控制: 关闭时直接返回前 top_k 条 (保持原顺序).
    """
    if not ENABLE_RERANKER:
        return memories[:top_k] if memories else []
    return await get_reranker().rerank(query, memories, top_k=top_k)
