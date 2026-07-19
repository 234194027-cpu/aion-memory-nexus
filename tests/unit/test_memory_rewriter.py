"""Regression tests for MemoryRewriter privacy boundaries."""

from __future__ import annotations

import asyncio
import logging

from src.memory.services.memory_rewriter import MemoryRewriter


def test_rewriter_is_retired_without_inspecting_proposal_content(caplog) -> None:
    secret = "private-memory-content-must-not-reach-logs"

    class StubSession:
        async def rollback(self) -> None:
            return None

    class ExplodingProposal(dict):
        def get(self, *_args, **_kwargs):
            raise ValueError("unreadable proposal")

    proposal = ExplodingProposal(draft_body=secret)
    with caplog.at_level(logging.WARNING, logger="src.memory.services.memory_rewriter"):
        result = asyncio.run(MemoryRewriter(StubSession()).apply_proposals("user-1", [proposal]))

    assert result["failed"][0]["reason"] == "memory_rewriter_retired_use_working_agent"
    assert secret not in caplog.text
    assert caplog.text == ""
