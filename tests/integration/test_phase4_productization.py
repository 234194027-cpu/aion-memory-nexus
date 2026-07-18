"""Phase 4 产品化测试基线。"""
from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.asyncio


class TestSuite1_Stability:
    async def test_1_1_degrade_does_not_write_conflict_record(self):
        from src.memory.services.conflict_checker import _degrade_to_user_review

        relevant = [
            {"memory_id": "mem_001", "title": "t", "content": "c", "similarity": 0.8},
            {"memory_id": "mem_002", "title": "t2", "content": "c2", "similarity": 0.7},
            {"memory_id": "mem_003", "title": "t3", "content": "c3", "similarity": 0.6},
        ]
        result = _degrade_to_user_review("user_1", relevant, reason="llm_down")

        assert result["degraded_only"] is True
        assert result["conflicts"] == []
        assert result["has_conflict"] is False
        assert len(result["similar_memories"]) <= 5
        assert result["persisted_conflict_ids"] == []
        assert "llm_down" in result["warnings"][0]

    async def test_1_1_retrieval_failure_returns_degraded_only(self):
        from src.memory.services.conflict_checker import ConflictChecker

        mock_db = AsyncMock()
        checker = ConflictChecker(mock_db)

        with patch.object(checker, "__init__", lambda s, d: None):
            with patch("src.memory.services.conflict_checker.RetrievalEngine") as MockRetrieval:
                mock_engine = MockRetrieval.return_value
                mock_engine.reconstruct_context = AsyncMock(side_effect=RuntimeError("db_down"))
                checker.db = mock_db

                result = await checker.check(
                    user_id="user_1",
                    candidate={"body": "我想转行做设计", "title": "转行"},
                    recall_level="work_context",
                )

                assert result["degraded_only"] is True
                assert result["has_conflict"] is False
                assert result["conflicts"] == []

    async def test_1_2_dedup_within_24h_filters_existing_pairs(self):
        from src.memory.services.conflict_checker import _dedup_within_24h

        class FakeExecResult:
            def scalars(self_inner):
                class _S:
                    def all(self_inner2):
                        return []
                return _S()

            def all(self_inner):
                return [("mem_001", datetime.now())]

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=FakeExecResult())

        incoming = [
            {"memory_id": "mem_001", "title": "t1", "severity": "low"},
            {"memory_id": "mem_002", "title": "t2", "severity": "high"},
        ]

        with patch("src.memory.services.conflict_checker.ConflictRecord") as MockCR:
            MockCR.past_memory_id = "past_memory_id"
            MockCR.created_at = "created_at"
            MockCR.user_id = "user_id"

            result = await _dedup_within_24h(mock_db, "user_1", incoming)

        mem_ids = [c.get("memory_id") for c in result]
        assert "mem_002" in mem_ids

    async def test_1_3_merge_returns_without_embedding_timeout(self):
        from src.memory.services.deduplicator import MemoryDeduplicator

        mock_db = AsyncMock()
        primary = MagicMock()
        primary.id = "p1"
        primary.user_id = "u1"
        primary.title = "P"
        primary.body = "B"
        primary.status = "active"
        primary.updated_at = None

        secondary = MagicMock()
        secondary.id = "s1"
        secondary.user_id = "u1"
        secondary.title = "S"
        secondary.body = "SB"

        dedup = MemoryDeduplicator.__new__(MemoryDeduplicator)
        dedup.db = mock_db
        dedup._load_memory = AsyncMock(side_effect=[primary, secondary])
        dedup._copy_sources = AsyncMock()
        dedup._regenerate_embedding_safe = AsyncMock()

        mock_db.begin = MagicMock()
        mock_db.begin.return_value.__aenter__ = AsyncMock()
        mock_db.begin.return_value.__aexit__ = AsyncMock()
        mock_db.flush = AsyncMock()

        result = await dedup.merge("p1", "s1")
        assert result == "p1"
        dedup._regenerate_embedding_safe.assert_called_once()

    async def test_1_4_hygiene_returns_suggestions_only(self):
        from src.memory.tasks.memory_hygiene import run_nightly_hygiene

        mock_db = AsyncMock()

        with patch("src.memory.tasks.memory_hygiene.MemoryDeduplicator") as MockDedup:
            mock_dedup = MockDedup.return_value
            mock_dedup.find_duplicates = AsyncMock(return_value=[
                {"memory_id_a": "a1", "memory_id_b": "b1", "similarity": 0.95, "suggested_action": "merge"},
            ])

            with patch("src.memory.tasks.memory_hygiene._load_active_importance_ids"):
                with patch("src.memory.tasks.memory_hygiene.ConflictRecord") as MockCR:
                    MockCR.user_id = "user_id"
                    MockCR.status = "status"
                    MockCR.created_at = "created_at"
                    MockCR.id = "id"
                    MockCR.past_memory_id = "past_memory_id"
                    MockCR.current_memory_id = "current_memory_id"
                    MockCR.severity = "severity"

                    mock_result = MagicMock()
                    mock_result.scalars.return_value.all.return_value = []
                    mock_db.execute = AsyncMock(return_value=mock_result)

                    result = await run_nightly_hygiene(mock_db, "user_1", dedup_threshold=0.9)

        assert "duplicate_pairs" in result
        assert "stale_conflicts" in result
        assert "stats" in result
        assert result["user_id"] == "user_1"

    async def test_1_5_recency_decay_halves_at_60_days(self):
        from src.memory.services.retrieval_engine import RetrievalEngine

        memory = MagicMock()
        memory.importance = 1.0
        memory.created_at = datetime.utcnow() - timedelta(days=60)

        decay = RetrievalEngine._recency_decay(memory, half_life_days=60.0)
        assert 0.65 <= decay <= 0.75

    async def test_1_5_recency_decay_recent_is_near_1(self):
        from src.memory.services.retrieval_engine import RetrievalEngine

        memory = MagicMock()
        memory.importance = 0.8
        memory.created_at = datetime.utcnow()

        decay = RetrievalEngine._recency_decay(memory, half_life_days=60.0)
        assert decay >= 0.75

    async def test_1_5_vector_search_drops_dim_mismatch(self):
        from src.memory.services.retrieval_engine import RetrievalEngine

        engine = RetrievalEngine.__new__(RetrievalEngine)
        mock_db = AsyncMock()
        engine.db = mock_db

        m1 = MagicMock()
        m1.id = "m1"
        m2 = MagicMock()
        m2.id = "m2"

        class FakeEmb:
            def __init__(self, mid, vec, dim):
                self.memory_id = mid
                self.embedding_vector = vec
                self.dimension = dim

        fake_query_vec = [0.1] * 100
        # P0-1 修复后 _vector_search 改用 JOIN MemoryEmbedding + CommittedMemory，
        # 一次 db.execute 返回 [(emb_record, memory), ...] 元组列表
        emb1 = FakeEmb("m1", [0.1] * 100, 100)
        emb2 = FakeEmb("m2", [0.1] * 1024, 1024)
        mock_result = MagicMock()
        mock_result.all.return_value = [(emb1, m1), (emb2, m2)]

        mock_db.execute = AsyncMock(return_value=mock_result)

        with patch.object(engine, "_build_filter", return_value=True):
            mems, scores = await engine._vector_search(fake_query_vec, "user_1", None, "full_trusted", 10)
            assert len(mems) == 1
            assert mems[0].id == "m1"


class TestSuite2_DecisionQuality:
    async def test_2_1_classify_mode_decision(self):
        from src.cognition.services.daily_briefing import classify_mode
        assert classify_mode("我该不该转行做设计") == "decision"
        assert classify_mode("要不要投资这个项目") == "decision"
        assert classify_mode("应不应该辞职") == "decision"

    async def test_2_1_classify_mode_recall(self):
        from src.cognition.services.daily_briefing import classify_mode
        assert classify_mode("上次我怎么想的") == "recall"
        assert classify_mode("以前我对这件事是什么态度") == "recall"

    async def test_2_1_classify_mode_planning(self):
        from src.cognition.services.daily_briefing import classify_mode
        assert classify_mode("接下来应该做什么") == "planning"
        assert classify_mode("下一步怎么走") == "planning"

    async def test_2_1_classify_mode_reflection(self):
        from src.cognition.services.daily_briefing import classify_mode
        assert classify_mode("我为什么总是拖延") == "reflection"
        assert classify_mode("最近模式是什么") == "reflection"

    async def test_2_1_classify_mode_review(self):
        from src.cognition.services.daily_briefing import classify_mode
        assert classify_mode("我上次这个决定对不对") == "review"
        assert classify_mode("复盘一下这个项目") == "review"

    async def test_2_1_classify_mode_empty_defaults_to_decision(self):
        from src.cognition.services.daily_briefing import classify_mode
        assert classify_mode("") == "decision"
        assert classify_mode("聊聊人生") == "decision"

    async def test_2_2_record_ask_writes_audit_log(self):
        from src.execution.services.usage_metrics import record_ask

        mock_db = AsyncMock()
        with patch("src.execution.services.usage_metrics.AuditLogger") as MockAL:
            MockAL.log = AsyncMock()
            await record_ask(mock_db, "u1", session_id="s1", mode="decision", confidence=0.7)
            MockAL.log.assert_called_once()
            call_args = MockAL.log.call_args
            assert call_args[1]["action"] == "usage_ask"
            assert call_args[1]["detail"]["mode"] == "decision"

    async def test_2_2_record_drop_writes_audit_log(self):
        from src.execution.services.usage_metrics import record_drop

        mock_db = AsyncMock()
        with patch("src.execution.services.usage_metrics.AuditLogger") as MockAL:
            MockAL.log = AsyncMock()
            await record_drop(mock_db, "u1", memory_id="m1", drop_seconds=2.5, channel="wecom")
            MockAL.log.assert_called_once()
            call_args = MockAL.log.call_args
            assert call_args[1]["detail"]["channel"] == "wecom"
            assert call_args[1]["detail"]["drop_seconds"] == 2.5


class TestSuite3_Usability:
    async def test_3_1_briefing_has_all_fields(self):
        from src.cognition.services.daily_briefing import build_daily_briefing

        mock_db = AsyncMock()
        empty_result = MagicMock()
        empty_result.scalar_one_or_none.return_value = None
        empty_result.scalars.return_value.all.return_value = []
        mock_db.execute = AsyncMock(return_value=empty_result)

        result = await build_daily_briefing(mock_db, "user_1")

        assert "headline" in result
        assert "open_decision" in result
        assert "old_conflict" in result
        assert "echo_principle" in result
        assert "suggested_next_step" in result
        assert "generated_at" in result
        assert isinstance(result["headline"], str)
        assert len(result["headline"]) > 0

    async def test_3_1_briefing_headline_no_decision(self):
        from src.cognition.services.daily_briefing import _compose_headline

        headline = _compose_headline(None, None, None)
        assert isinstance(headline, str)
        assert len(headline) > 0

    async def test_3_2_quick_drop_request_schema(self):
        from src.cognition.api.daily_briefing import QuickDropRequest

        req = QuickDropRequest(text="今天心情不错")
        assert req.text == "今天心情不错"
        assert req.channel == "web"

        req_wecom = QuickDropRequest(text="转行的想法", channel="wecom")
        assert req_wecom.channel == "wecom"

    async def test_3_2_quick_drop_empty_text_rejected(self):
        from src.cognition.api.daily_briefing import QuickDropRequest
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            QuickDropRequest(text="")

    async def test_3_2_quick_drop_long_text_rejected(self):
        from src.cognition.api.daily_briefing import QuickDropRequest
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            QuickDropRequest(text="a" * 2001)

    async def test_3_3_usage_summary_returns_all_metrics(self):
        from src.execution.services.usage_metrics import get_usage_summary

        mock_db = AsyncMock()
        empty_result = MagicMock()
        empty_result.scalars.return_value.all.return_value = []
        mock_db.execute = AsyncMock(return_value=empty_result)

        with patch("src.execution.services.usage_metrics.AuditLog") as MockAL:
            MockAL.user_id = "user_id"
            MockAL.action = "action"
            MockAL.created_at = "created_at"
            MockAL.detail = "detail"

            result = await get_usage_summary(mock_db, "u1", days=7)

        assert "drops" in result
        assert "asks" in result
        assert "mode_distribution" in result
        assert "active_days" in result
        assert "daily_active_streak" in result
        assert result["user_id"] == "u1"
        assert result["window_days"] == 7

    async def test_3_3_streak_calculation(self):
        from src.execution.services.usage_metrics import _calc_streak
        dates = ["2026-06-28", "2026-06-29", "2026-06-30"]
        assert _calc_streak(dates) == 3

    async def test_3_3_streak_broken(self):
        from src.execution.services.usage_metrics import _calc_streak
        dates = ["2026-06-25", "2026-06-28"]
        assert _calc_streak(dates) == 1

    async def test_3_3_streak_empty(self):
        from src.execution.services.usage_metrics import _calc_streak
        assert _calc_streak([]) == 0


class TestIntegration:
    async def test_full_daily_loop_structure(self):
        from src.cognition.services.daily_briefing import build_daily_briefing, classify_mode
        from src.cognition.api.daily_briefing import QuickDropRequest

        mock_db = AsyncMock()
        empty_result = MagicMock()
        empty_result.scalar_one_or_none.return_value = None
        empty_result.scalars.return_value.all.return_value = []
        mock_db.execute = AsyncMock(return_value=empty_result)

        briefing = await build_daily_briefing(mock_db, "user_1")
        assert briefing["headline"]

        drop = QuickDropRequest(text="今天想了很多关于转行的事")
        assert drop.text

        mode = classify_mode("接下来我应该怎么准备")
        assert mode == "planning"
