"""Control Plane 单元测试。"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.platform.control_plane.router import ControlPlaneRouter, DecisionAction
from src.platform.control_plane.policies.ingestion_policy import IngestionPolicy
from src.platform.control_plane.policies.memory_policy import (
    MemoryAdmissionPolicy,
    MemoryWritePolicy,
)
from src.platform.control_plane.policies.agent_policy import AgentPermissionPolicy
from src.platform.control_plane.policies.retrieval_policy import RetrievalPolicy
from src.platform.control_plane.policies.evolution_policy import EvolutionPolicy


class TestIngestionPolicy:
    @pytest.fixture
    def policy(self):
        return IngestionPolicy()

    @pytest.mark.asyncio
    async def test_allow_valid_content(self, policy):
        result = await policy.evaluate(
            {
                "content": "今天决定使用 Python 作为后端语言，因为它的生态系统丰富",
                "source": "chat",
            },
            "user_123",
        )
        assert result["action"] == DecisionAction.ALLOW

    @pytest.mark.asyncio
    async def test_reject_empty_content(self, policy):
        result = await policy.evaluate({"content": "", "source": "chat"}, "user_123")
        assert result["action"] == DecisionAction.REJECT


class TestMemoryAdmissionPolicy:
    @pytest.fixture
    def policy(self):
        return MemoryAdmissionPolicy()

    @pytest.mark.asyncio
    async def test_allow_valuable_content(self, policy):
        result = await policy.evaluate(
            {
                "content": "2024-01-15 决定采用微服务架构，因为团队规模扩大后需要独立部署",
            },
            "user_123",
        )
        assert result["action"] == DecisionAction.ALLOW


class TestMemoryWritePolicy:
    @pytest.fixture
    def policy(self):
        return MemoryWritePolicy()

    @pytest.mark.asyncio
    async def test_evidence_backed_proposal_is_autonomously_governed(self, policy):
        result = await policy.evaluate(
            {
                "importance": 0.8,
                "confidence": 0.9,
                "memory_type": "decision",
                "sensitivity": "normal",
                "suggested_action": "auto_commit",
            },
            "user_123",
        )
        assert result["action"] == DecisionAction.ALLOW
        assert result["reason"] == "autonomous_governance_allowed"
        assert result["metadata"]["requires_review"] is False


class TestAgentPermissionPolicy:
    @pytest.fixture
    def mock_db(self):
        return AsyncMock()

    @pytest.fixture
    def policy(self, mock_db):
        return AgentPermissionPolicy(mock_db)

    @pytest.mark.asyncio
    async def test_default_allow_codex_read_memory(self, policy, mock_db):
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_result
        result = await policy.check_permission("codex", "read_memory", "user_123")
        assert result["action"] == DecisionAction.ALLOW


class TestRetrievalPolicy:
    @pytest.fixture
    def policy(self):
        return RetrievalPolicy()

    @pytest.mark.asyncio
    async def test_task_only_recall_level(self, policy):
        result = await policy.evaluate("查询任务", "user_123", "task_only")
        assert result["action"] == DecisionAction.ALLOW
        assert result["metadata"]["recall_level"] == "task_only"


class TestEvolutionPolicy:
    @pytest.fixture
    def policy(self):
        return EvolutionPolicy()

    @pytest.mark.asyncio
    async def test_memory_rewrite_requires_approval(self, policy):
        result = await policy.evaluate(
            {
                "operation_type": "memory_rewrite",
                "proposals": [{"action": "merge", "memory_ids": ["m1", "m2"]}],
                "approved": False,
            },
            "user_123",
        )
        assert result["action"] == DecisionAction.ROUTE


class TestControlPlaneIntegration:
    @pytest.fixture
    def mock_db(self):
        return AsyncMock()

    @pytest.fixture
    def router(self, mock_db):
        return ControlPlaneRouter(mock_db)

    @pytest.mark.asyncio
    async def test_full_ingestion_flow(self, router):
        with patch("src.execution.services.audit_logger.AuditLogger.log", new_callable=AsyncMock):
            result = await router.decide_ingestion(
                {
                    "content": "2024-01-15 决定采用微服务架构，因为团队规模扩大后需要独立部署",
                    "source": "chat",
                },
                "user_123",
            )
            assert result.action == DecisionAction.ALLOW
